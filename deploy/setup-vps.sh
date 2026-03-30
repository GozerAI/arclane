#!/usr/bin/env bash
# Arclane VPS setup script
# Tested on Ubuntu 22.04+ / Debian 12+
set -euo pipefail

DOMAIN="arclane.cloud"
APP_USER="arclane"
APP_DIR="/opt/arclane"
WORKSPACES_DIR="/var/arclane/workspaces"

echo "=== Arclane VPS Setup ==="

echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    docker.io docker-compose-plugin \
    git curl ufw fail2ban dnsutils openssl \
    > /dev/null

systemctl enable --now docker

echo "[2/8] Configuring firewall..."
ufw --force reset > /dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "[3/8] Creating app user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -m -d "$APP_DIR" -s /bin/bash "$APP_USER"
fi
usermod -aG docker "$APP_USER"

echo "[4/8] Cloning Arclane..."
if [ ! -d "$APP_DIR/repo" ]; then
    sudo -u "$APP_USER" git clone https://github.com/chrisarseno/arclane.git "$APP_DIR/repo"
else
    cd "$APP_DIR/repo"
    sudo -u "$APP_USER" git pull
fi

echo "[5/8] Setting up environment..."
if [ ! -f "$APP_DIR/repo/.env" ]; then
    cp "$APP_DIR/repo/.env.example" "$APP_DIR/repo/.env"
    # Generate secrets via environment variables so they never appear in
    # the process list (ps/proc). awk reads ENVIRON[], not argv.
    export _ARCLANE_GEN_SECRET
    export _ARCLANE_GEN_WEBHOOK
    _ARCLANE_GEN_SECRET="$(openssl rand -hex 32)"
    _ARCLANE_GEN_WEBHOOK="$(openssl rand -hex 32)"
    awk '{
        gsub(/change-me-in-production/, ENVIRON["_ARCLANE_GEN_SECRET"]);
        gsub(/change-me-webhook-secret/, ENVIRON["_ARCLANE_GEN_WEBHOOK"]);
        print
    }' "$APP_DIR/repo/.env" > "$APP_DIR/repo/.env.tmp" \
        && mv "$APP_DIR/repo/.env.tmp" "$APP_DIR/repo/.env"
    unset _ARCLANE_GEN_SECRET _ARCLANE_GEN_WEBHOOK
    chmod 600 "$APP_DIR/repo/.env"
    echo "  >> Edit $APP_DIR/repo/.env with your production values"
fi

echo "[6/8] Creating workspace and backup directories..."
mkdir -p "$WORKSPACES_DIR"
mkdir -p /var/arclane/backups
chown -R "$APP_USER:$APP_USER" /var/arclane

# Install daily database backup cron (03:00)
CRON_LINE="0 3 * * * /opt/arclane/repo/deploy/backup-db.sh >> /var/log/arclane-backup.log 2>&1"
( crontab -u "$APP_USER" -l 2>/dev/null | grep -v backup-db.sh; echo "$CRON_LINE" ) \
    | crontab -u "$APP_USER" -
echo "  >> Backup cron installed (daily at 03:00, 7-day retention)"

echo "[7/8] Checking DNS..."
RESOLVED="$(dig +short "$DOMAIN" 2>/dev/null || true)"
SERVER_IP="$(curl -s ifconfig.me 2>/dev/null || true)"
if [ "$RESOLVED" = "$SERVER_IP" ]; then
    echo "  >> DNS OK: $DOMAIN -> $SERVER_IP"
else
    echo "  >> WARNING: $DOMAIN resolves to '$RESOLVED' but this server is $SERVER_IP"
    echo "  >> Set these DNS records:"
    echo "     A    arclane.cloud      -> $SERVER_IP"
    echo "     A    *.arclane.cloud    -> $SERVER_IP"
fi

echo "[8/8] Building and starting services..."
cd "$APP_DIR/repo"
docker compose up -d --build
docker compose exec arclane alembic upgrade head || echo "  >> Migration skipped (tables may already exist)"

echo ""
echo "=== Setup Complete ==="
echo "Arclane: https://$DOMAIN"
echo "Health:  https://$DOMAIN/health"
echo "Live:    https://$DOMAIN/live"
echo ""
echo "Next steps:"
echo "  1. Edit $APP_DIR/repo/.env with real service tokens"
echo "  2. Set DNS: A record for arclane.cloud + *.arclane.cloud"
echo "  3. Run: bash deploy/smoke-test.sh arclane.cloud"
echo "  4. Watch: docker compose logs -f"
