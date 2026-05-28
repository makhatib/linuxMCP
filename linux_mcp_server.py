#!/usr/bin/env python3
"""
============================================================================
  Linux VPS Agent - MCP server
  Let Claude control a Linux VPS through a tmux-backed shell.

  Author : Mahmoud Alkhatib
  YouTube: https://www.youtube.com/@malkhatib
  License: MIT - free to use, modify, and share. Keep this credit. :)
============================================================================

Exposes a controlled, tmux-backed Linux shell to Claude over Streamable HTTP.

Why tmux (not plain "run this bash command"):
  - Persistent sessions: cwd / env / activated venvs / ssh sessions survive
    between tool calls, so Claude behaves like a real operator, not a fresh
    subprocess each time.
  - You can `tmux attach` to the SAME session and watch Claude work live on
    screen (great for a YouTube demo, and good for oversight).

Safety baked in:
  - Binds to 127.0.0.1 only. TLS + public exposure is handled by Caddy in front.
  - Command denylist for obviously destructive operations.
  - Full audit log of every command (tail -f it during the demo).

MCP endpoint (Streamable HTTP):  http://127.0.0.1:8080/mcp
Only the `tmux` binary + the `mcp` python package are required. No libtmux.
"""

import os
import re
import time
import uuid
import logging
import subprocess

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# ---------------------------------------------------------------------------
# Config (override via environment variables in the systemd unit)
# ---------------------------------------------------------------------------
HOST            = os.environ.get("MCP_HOST", "127.0.0.1")
PORT            = int(os.environ.get("MCP_PORT", "8080"))
DEFAULT_SESSION = os.environ.get("MCP_DEFAULT_SESSION", "claude")
AUDIT_LOG       = os.environ.get("MCP_AUDIT_LOG", "/opt/linux-mcp/audit.log")
CMD_TIMEOUT     = int(os.environ.get("MCP_CMD_TIMEOUT", "60"))   # seconds

# Public hostname Caddy/Cloudflare forwards as the Host header. Must be set when
# running behind a reverse proxy, or the SDK's DNS-rebinding protection rejects
# requests with "421 Misdirected Request". Empty = disable the protection
# (fine when you secure the host at the firewall/proxy layer instead).
ALLOWED_HOST    = os.environ.get("MCP_ALLOWED_HOST", "").strip()

# Patterns refused outright. Tune for your environment.
DENY_PATTERNS = [
    r"\brm\s+-rf\s+/(?:\s|$)",          # rm -rf /
    r"\brm\s+-rf\s+/\*",                # rm -rf /*
    r"\bmkfs\b",                        # format a filesystem
    r"\bdd\b.*\bof=/dev/",              # overwrite a block device
    r":\s*\(\)\s*\{.*\};\s*:",          # fork bomb
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r">\s*/dev/sd[a-z]",                # write to a raw disk
    r"\bchmod\s+-R\s+777\s+/(?:\s|$)",
]

# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
logging.basicConfig(filename=AUDIT_LOG, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
_audit_log = logging.getLogger("audit")

def _audit(action: str, detail: str) -> None:
    _audit_log.info("%s | %s", action, detail.replace("\n", "\\n")[:2000])

# ---------------------------------------------------------------------------
# tmux helpers (shell out to the tmux binary - robust across versions)
# ---------------------------------------------------------------------------
def _tmux(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)

def _has_session(name: str) -> bool:
    return _tmux("has-session", "-t", name).returncode == 0

def _ensure(name: str) -> None:
    if not _has_session(name):
        _tmux("new-session", "-d", "-s", name)
        _audit("SESSION_CREATE", name)

def _capture(name: str, lines: int = 2000) -> str:
    return _tmux("capture-pane", "-p", "-t", name, "-S", f"-{lines}").stdout

def _denied(command: str):
    for pat in DENY_PATTERNS:
        if re.search(pat, command):
            return pat
    return None

# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------
if ALLOWED_HOST:
    _security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[ALLOWED_HOST, f"{ALLOWED_HOST}:*", "127.0.0.1:*", "localhost:*"],
        allowed_origins=[f"https://{ALLOWED_HOST}", "https://claude.ai", "https://claude.com"],
    )
else:
    _security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

mcp = FastMCP(
    "Linux VPS Agent",
    host=HOST,
    port=PORT,
    json_response=True,
    transport_security=_security,
)

@mcp.tool()
def run_command(command: str, session: str = DEFAULT_SESSION,
                timeout: int = CMD_TIMEOUT) -> str:
    """Run a shell command in a persistent tmux session; return output + exit code.

    State (cwd, env, activated venvs, ssh sessions) persists across calls within
    the same session. Use this for normal, non-interactive commands.
    """
    bad = _denied(command)
    if bad:
        _audit("DENIED", f"{session} :: {command} :: {bad}")
        return f"REFUSED: command matched a denied pattern ({bad}). Not executed."

    _audit("RUN", f"{session} :: {command}")
    _ensure(session)

    # Run inside the session (so cwd/env persist) but capture output and exit
    # code via temp files - far more robust than scraping the visible pane.
    uid     = uuid.uuid4().hex[:8]
    outfile = f"/tmp/mcp_{uid}.out"
    rcfile  = f"/tmp/mcp_{uid}.rc"
    wrapped = f"{{ {command} ; }} > {outfile} 2>&1; echo $? > {rcfile}"
    _tmux("send-keys", "-t", session, wrapped, "Enter")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(rcfile):
            try:
                rc = open(rcfile).read().strip()
            except OSError:
                rc = ""
            if rc == "":                 # file exists but echo not flushed yet
                time.sleep(0.1)
                continue
            try:
                out = open(outfile).read()
            except OSError:
                out = ""
            for f in (outfile, rcfile):
                try:
                    os.remove(f)
                except OSError:
                    pass
            _audit("DONE", f"{session} :: rc={rc}")
            return f"[exit code: {rc}]\n{out.rstrip()}"
        time.sleep(0.2)

    _audit("TIMEOUT", f"{session} :: {command}")
    return (f"[timeout after {timeout}s - still running] "
            f"(use capture_pane on session '{session}' to see live output)")

@mcp.tool()
def send_keys(session: str, keys: str, enter: bool = True) -> str:
    """Send raw keystrokes to a session WITHOUT waiting for completion.

    Use for interactive programs: answering prompts, vim, REPLs, ssh password,
    etc. Follow up with capture_pane to see the result.
    """
    _audit("SENDKEYS", f"{session} :: {keys}")
    _ensure(session)
    args = ["send-keys", "-t", session, keys]
    if enter:
        args.append("Enter")
    _tmux(*args)
    time.sleep(0.5)
    return _capture(session, lines=200)

@mcp.tool()
def capture_pane(session: str = DEFAULT_SESSION) -> str:
    """Return what is currently on screen in a session's pane."""
    _ensure(session)
    return _capture(session)

@mcp.tool()
def list_sessions() -> str:
    """List all active tmux sessions."""
    out = _tmux("list-sessions", "-F", "#{session_name}").stdout.strip()
    return out or "(no active sessions)"

@mcp.tool()
def new_session(name: str) -> str:
    """Create a new named tmux session."""
    _ensure(name)
    return f"Session '{name}' ready."

@mcp.tool()
def kill_session(name: str) -> str:
    """Terminate a tmux session."""
    if not _has_session(name):
        return f"No session named '{name}'."
    _tmux("kill-session", "-t", name)
    _audit("SESSION_KILL", name)
    return f"Session '{name}' killed."

if __name__ == "__main__":
    _ensure(DEFAULT_SESSION)
    mcp.run(transport="streamable-http")
