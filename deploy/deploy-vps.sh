#!/usr/bin/env bash
# Deploy Arclane to the GozerAI Hostinger VPS (72.61.76.32)
# Prerequisites: Docker, shandor_caddy on webproxy network
# Run as root: bash /opt/arclane/repo/deploy/deploy-vps.sh
set -euo pipefail

APP_DIR="/opt/arclane"
REPO_DIR="$APP_DIR/repo"
CADDY_FILE="/opt/shandorcode/deployment/Caddyfile"
DOMAIN="arclane.cloud"

echo "=== Arclane VPS Deploy ==="

# ── 1. Clone or pull ─────────────────────────────────────────
echo "[1/7] Fetching latest code..."
if [ ! -d "$REPO_DIR" ]; then
    mkdir -p "$APP_DIR"
    git clone https://github.com/chrisarseno/arclane.git "$REPO_DIR"
else
    cd "$REPO_DIR" && git pull
fi
cd "$REPO_DIR"

# ── 2. Environment file ─────────────────────────────────────
echo "[2/7] Checking environment..."
if [ ! -f "$REPO_DIR/.env" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    # Generate secrets via environment variables so they never appear in
    # the process list (ps/proc). awk reads ENVIRON[], not argv.
    export _ARCLANE_GEN_SECRET
    _ARCLANE_GEN_SECRET="$(openssl rand -hex 32)"
    awk '{
        gsub(/change-me-in-production/, ENVIRON["_ARCLANE_GEN_SECRET"]);
        print
    }' "$REPO_DIR/.env" > "$REPO_DIR/.env.tmp" \
        && mv "$REPO_DIR/.env.tmp" "$REPO_DIR/.env"
    unset _ARCLANE_GEN_SECRET
    chmod 600 "$REPO_DIR/.env"
    echo ""
    echo "  !! IMPORTANT: Edit $REPO_DIR/.env before continuing:"
    echo "     - ARCLANE_LLM_BASE_URL (OpenAI-compatible endpoint)"
    echo "     - ARCLANE_LLM_API_KEY"
    echo "     - ARCLANE_LLM_MODEL"
    echo "     - ARCLANE_RESEND_API_KEY (for email notifications)"
    echo ""
    echo "  Run this script again after editing .env"
    exit 0
fi

# ── 3. Ensure webproxy network exists ────────────────────────
echo "[3/7] Checking Docker network..."
if ! docker network inspect webproxy > /dev/null 2>&1; then
    echo "  >> ERROR: 'webproxy' Docker network not found."
    echo "  >> This network should already exist from the shandor_caddy setup."
    exit 1
fi

# ── 4. Stop existing container if running ────────────────────
echo "[4/7] Stopping existing container..."
docker stop arclane 2>/dev/null && docker rm arclane 2>/dev/null || true

# ── 5. Build and start ──────────────────────────────────────
echo "[5/7] Building and starting Arclane..."
cd "$REPO_DIR/deploy"
docker compose -f docker-compose.vps.yml up -d --build

# ── 6. Run migrations ───────────────────────────────────────
echo "[6/7] Running database migrations..."
docker exec arclane python -m alembic upgrade head 2>/dev/null \
    || echo "  >> Migrations skipped (tables created by init_db on first start)"

# ── 7. Update Caddy routing ─────────────────────────────────
echo "[7/7] Updating Caddy routing..."
if [ -f "$CADDY_FILE" ]; then
    # Check if arclane.cloud block already exists
    if grep -q "arclane.cloud" "$CADDY_FILE"; then
        echo "  >> arclane.cloud already in Caddyfile — verify it points to arclane:8012"
    else
        # Append Arclane routing blocks
        cat >> "$CADDY_FILE" <<'CADDY'

# Arclane — standalone autonomous business engine
arclane.cloud {
    reverse_proxy arclane:8012
}

*.arclane.cloud {
    reverse_proxy arclane:8012
}
CADDY
        echo "  >> Added arclane.cloud routing to Caddyfile"
        echo "  >> Reloading Caddy..."
        docker exec shandor_caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null \
            || docker restart shandor_caddy
    fi
else
    echo "  >> WARNING: Caddyfile not found at $CADDY_FILE"
    echo "  >> Manually add these blocks to your Caddyfile:"
    echo "     arclane.cloud { reverse_proxy arclane:8012 }"
    echo "     *.arclane.cloud { reverse_proxy arclane:8012 }"
fi

# ── Health check ─────────────────────────────────────────────
echo ""
echo "Waiting for health check..."
for i in 1 2 3 4 5; do
    sleep 3
    if docker exec arclane curl -sf http://localhost:8012/health > /dev/null 2>&1; then
        echo ""
        echo "=== Deploy Complete ==="
        echo "  Site:   https://$DOMAIN"
        echo "  Health: https://$DOMAIN/health"
        echo "  Live:   https://$DOMAIN/live"
        echo "  Logs:   docker logs -f arclane"
        exit 0
    fi
    echo "  Attempt $i/5..."
done

echo ""
echo "Health check failed. Check logs:"
echo "  docker logs arclane"
exit 1
