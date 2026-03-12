#!/usr/bin/env bash
# Arclane VPS Setup Script
# Tested on Ubuntu 22.04+ / Debian 12+
# Run as root or with sudo
set -euo pipefail

DOMAIN="arclane.cloud"
APP_USER="arclane"
APP_DIR="/opt/arclane"

echo "=== Arclane VPS Setup ==="

# 1. System packages
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    docker.io docker-compose-plugin \
    git curl ufw fail2ban \
    > /dev/null

systemctl enable --now docker

# 2. Firewall
echo "[2/7] Configuring firewall..."
ufw --force reset > /dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# 3. App user
echo "[3/7] Creating app user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -m -d "$APP_DIR" -s /bin/bash "$APP_USER"
    usermod -aG docker "$APP_USER"
fi

# 4. Clone repo
echo "[4/7] Cloning Arclane..."
if [ ! -d "$APP_DIR/repo" ]; then
    sudo -u "$APP_USER" git clone https://github.com/chrisarseno/arclane.git "$APP_DIR/repo"
else
    cd "$APP_DIR/repo" && sudo -u "$APP_USER" git pull
fi

# 5. Environment file
echo "[5/7] Setting up environment..."
if [ ! -f "$APP_DIR/repo/.env" ]; then
    cp "$APP_DIR/repo/.env.example" "$APP_DIR/repo/.env"
    # Generate a random secret key
    SECRET=$(openssl rand -hex 32)
    sed -i "s/change-me-in-production/$SECRET/" "$APP_DIR/repo/.env"
    echo "  >> Edit $APP_DIR/repo/.env with your actual values"
fi

# 6. DNS check
echo "[6/7] Checking DNS..."
RESOLVED=$(dig +short "$DOMAIN" 2>/dev/null || true)
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || true)
if [ "$RESOLVED" = "$SERVER_IP" ]; then
    echo "  >> DNS OK: $DOMAIN -> $SERVER_IP"
else
    echo "  >> WARNING: $DOMAIN resolves to '$RESOLVED' but this server is $SERVER_IP"
    echo "  >> Set these DNS records:"
    echo "     A    arclane.cloud      -> $SERVER_IP"
    echo "     A    *.arclane.cloud    -> $SERVER_IP"
fi

# 7. Start services
echo "[7/8] Building and starting services..."
cd "$APP_DIR/repo"
docker compose up -d --build

# 8. Run migrations
echo "[8/8] Running database migrations..."
docker compose exec arclane python -m alembic upgrade head || echo "  >> Migration skipped (first run — tables created by init_db)"

echo ""
echo "=== Setup Complete ==="
echo "Arclane: https://$DOMAIN"
echo "Health:  https://$DOMAIN/health"
echo "Live:    https://$DOMAIN/live"
echo ""
echo "Next steps:"
echo "  1. Edit $APP_DIR/repo/.env with real service tokens"
echo "  2. Set DNS: A record for arclane.cloud + *.arclane.cloud"
echo "  3. docker compose logs -f  (to watch startup)"
