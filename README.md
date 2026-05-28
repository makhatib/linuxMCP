# Linux VPS Agent — control a Linux server with Claude (via MCP)

Turn a bare Linux VPS into a **Claude-connectable agent** in one command.
Instead of running a heavyweight autonomous-agent runtime on the box, **Claude
becomes the agent** and the server only runs a tiny tmux-backed MCP server.

**Built by Mahmoud Alkhatib** · https://www.youtube.com/@malkhatib · MIT License

---

## What you get

- A persistent **tmux**-backed shell Claude can drive (`run_command`, `send_keys`,
  `capture_pane`, `list_sessions`, `new_session`, `kill_session`).
- Automatic **HTTPS** via Caddy (real Let's Encrypt cert, zero config).
- A **low-privilege** service user, a **command denylist**, and a full **audit log**.
- You can `tmux attach` and watch Claude work **live** — perfect for a demo.

## Requirements

- A fresh **Ubuntu/Debian VPS** with a **public IP**.
- A **domain/subdomain** with an A record pointing at the VPS (set this *before*
  running, so Caddy can issue the TLS certificate).
- A Claude **Pro / Max / Team / Enterprise** plan (custom connectors).

## Quick start

```bash
git clone <your-repo> && cd linux-vps-agent
sudo ./deploy.sh <your-domain> <your-email>
# e.g.  sudo ./deploy.sh agent.malkhatib.cloud iam@malkhatib.com
```

When it finishes it prints your endpoint: `https://<your-domain>/mcp`

In Claude: **Settings → Connectors → Add custom connector** → paste the URL.
It's authless, so no OAuth client ID is needed.

## Options (top of `deploy.sh`)

| Setting         | Default | What it does                                                        |
|-----------------|---------|---------------------------------------------------------------------|
| `GRANT_SUDO`    | `false` | `true` lets Claude run root tasks (install packages, manage services). |
| `ANTHROPIC_IPS` | empty   | Lock port 443 to specific source ranges (see notes below).          |

## Watch it live (the demo shot)

```bash
sudo -u mcpagent tmux attach -t claude   # left screen: the live terminal
tail -f /opt/linux-mcp/audit.log         # every command Claude runs
```

Split screen: live tmux on the left, Claude on the right. Ask Claude to check
disk usage / install nginx / start a service, and watch the commands type
themselves in real time.

## Security notes — please read

This is a **public shell**. Treat it like one:

- Always set an IP allowlist. If your domain is **proxied through Cloudflare**,
  the server sees Cloudflare IPs (not Anthropic's), so allowlist **Cloudflare's**
  ranges and enforce access at the Cloudflare layer — or set the DNS record to
  **DNS-only** so Claude reaches the box directly.
- Keep `GRANT_SUDO=false` unless you specifically need root for a demo.
- Tear the VPS down (or rotate it) after you're done. Don't leave a public,
  authless shell running.

## Troubleshooting

- **`421 Misdirected Request`** → the server isn't trusting your domain. The
  service must run with `MCP_ALLOWED_HOST=<your-domain>` (deploy.sh sets this).
- **Changes not taking effect** → `systemctl restart linux-mcp` (a re-deploy
  reloads the unit but you must restart the running process).
- **Claude says "couldn't register"** → delete the connector and add it fresh;
  repeated failed attempts can leave it tagged as an OAuth connector.
