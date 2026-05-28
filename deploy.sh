#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# deploy.sh - turn a fresh Ubuntu/Debian VPS into a Claude-connectable
#             Linux agent: a tmux-backed shell exposed over Streamable HTTP
#             with automatic HTTPS.
#
#   Author : Mahmoud Alkhatib
#   YouTube: https://www.youtube.com/@malkhatib
#   License: MIT - free to use, modify, and share. Keep this credit. :)
#
# USAGE:
#   sudo ./deploy.sh <domain> [admin-email]
#   e.g.  sudo ./deploy.sh lmcp.malkhatib.com iam@malkhatib.com
#
# Run it from the SAME folder that contains linux_mcp_server.py.
# Point your domain's A record at this VPS BEFORE running (Caddy needs it
# for the TLS certificate).
# ============================================================================

# ---- settings you may want to tweak ----------------------------------------
SERVICE_USER="mcpagent"
APP_DIR="/opt/linux-mcp"
PORT="8080"

# Give the agent root powers (so Claude can install packages, manage services,
# etc.). FALSE = safe, user-level only. TRUE = full sysadmin. Demo with care.
GRANT_SUDO="false"

# Lock port 443 so ONLY Claude's cloud can reach the server. Fill from
# Anthropic's published IP ranges. Empty = allow any source IP (NOT advised
# for a public shell).  e.g. ANTHROPIC_IPS=("160.79.104.0/23")
ANTHROPIC_IPS=()
# ----------------------------------------------------------------------------

DOMAIN="${1:-}"
EMAIL="${2:-admin@${DOMAIN:-example.com}}"

[[ -z "$DOMAIN" ]] && { echo "Usage: sudo ./deploy.sh <domain> [email]"; exit 1; }
[[ $EUID -ne 0 ]]  && { echo "Please run with sudo / as root."; exit 1; }

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$SRC_DIR/linux_mcp_server.py" ]] || {
  echo "linux_mcp_server.py not found next to this script."; exit 1; }

echo ">> [1/8] Installing base packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y tmux python3 python3-venv python3-pip ufw curl gnupg \
                   debian-keyring debian-archive-keyring apt-transport-https

echo ">> [2/8] Installing Caddy (automatic HTTPS)..."
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y
  apt-get install -y caddy
fi

echo ">> [3/8] Creating low-privilege service user '$SERVICE_USER'..."
id -u "$SERVICE_USER" >/dev/null 2>&1 || \
  useradd --create-home --shell /bin/bash "$SERVICE_USER"

if [[ "$GRANT_SUDO" == "true" ]]; then
  echo "   !! GRANT_SUDO=true -> giving $SERVICE_USER passwordless root."
  echo "$SERVICE_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/$SERVICE_USER"
  chmod 440 "/etc/sudoers.d/$SERVICE_USER"
fi

echo ">> [4/8] Installing the MCP server..."
mkdir -p "$APP_DIR"
cp "$SRC_DIR/linux_mcp_server.py" "$APP_DIR/"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet mcp uvicorn
touch "$APP_DIR/audit.log"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

echo ">> [5/8] Creating systemd service..."
NNP="true"; [[ "$GRANT_SUDO" == "true" ]] && NNP="false"
cat >/etc/systemd/system/linux-mcp.service <<EOF
[Unit]
Description=Linux VPS Agent (MCP server)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
Environment=MCP_HOST=127.0.0.1
Environment=MCP_PORT=$PORT
Environment=MCP_ALLOWED_HOST=$DOMAIN
Environment=MCP_AUDIT_LOG=$APP_DIR/audit.log
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/linux_mcp_server.py
Restart=on-failure
NoNewPrivileges=$NNP

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable linux-mcp.service
systemctl restart linux-mcp.service

echo ">> [6/8] Configuring Caddy reverse proxy + TLS for $DOMAIN..."
if [[ ${#ANTHROPIC_IPS[@]} -gt 0 ]]; then
  RANGES="${ANTHROPIC_IPS[*]}"
  cat >/etc/caddy/Caddyfile <<EOF
$DOMAIN {
    tls $EMAIL
    @claude remote_ip $RANGES
    handle @claude {
        reverse_proxy 127.0.0.1:$PORT
    }
    handle {
        respond "Forbidden" 403
    }
}
EOF
else
  cat >/etc/caddy/Caddyfile <<EOF
$DOMAIN {
    tls $EMAIL
    reverse_proxy 127.0.0.1:$PORT
}
EOF
fi
systemctl restart caddy

echo ">> [7/8] Firewall (ufw)..."
ufw allow 22/tcp
ufw allow 80/tcp
if [[ ${#ANTHROPIC_IPS[@]} -gt 0 ]]; then
  for cidr in "${ANTHROPIC_IPS[@]}"; do ufw allow from "$cidr" to any port 443 proto tcp; done
else
  ufw allow 443/tcp
fi
ufw --force enable

echo ">> [8/8] Done."
echo "--------------------------------------------------------------"
echo " MCP endpoint :  https://$DOMAIN/mcp"
echo " Add in Claude:  Settings > Connectors > Add custom connector"
echo "                 paste the URL above (it is authless)"
echo " Watch live   :  sudo -u $SERVICE_USER tmux attach -t claude"
echo " Audit log    :  tail -f $APP_DIR/audit.log"
echo " Sudo powers  :  GRANT_SUDO=$GRANT_SUDO"
echo "--------------------------------------------------------------"
echo " Built by Mahmoud Alkhatib  |  https://www.youtube.com/@malkhatib"
echo "--------------------------------------------------------------"
