#!/usr/bin/env bash
# Arclane PostgreSQL backup script
# Dumps the database and retains the last 7 days of backups.
#
# Usage:
#   bash deploy/backup-db.sh
#
# Cron (daily at 03:00):
#   0 3 * * * /opt/arclane/repo/deploy/backup-db.sh >> /var/log/arclane-backup.log 2>&1
set -euo pipefail

BACKUP_DIR="${ARCLANE_BACKUP_DIR:-/var/arclane/backups}"
RETAIN_DAYS="${ARCLANE_BACKUP_RETAIN_DAYS:-7}"
CONTAINER="${ARCLANE_PG_CONTAINER:-arclane-postgres-1}"
DB_NAME="${POSTGRES_DB:-arclane}"
DB_USER="${POSTGRES_USER:-arclane}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/arclane-${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[backup] $(date '+%F %T') Starting database backup..."

# Dump via docker exec, compress on the host side
docker exec "$CONTAINER" \
    pg_dump -U "$DB_USER" -d "$DB_NAME" --no-owner --no-acl \
    | gzip > "$BACKUP_FILE"

SIZE="$(du -h "$BACKUP_FILE" | cut -f1)"
echo "[backup] $(date '+%F %T') Backup complete: $BACKUP_FILE ($SIZE)"

# Prune backups older than RETAIN_DAYS
PRUNED=0
find "$BACKUP_DIR" -name "arclane-*.sql.gz" -mtime "+${RETAIN_DAYS}" -print -delete | while read -r f; do
    PRUNED=$((PRUNED + 1))
done

REMAINING="$(find "$BACKUP_DIR" -name "arclane-*.sql.gz" | wc -l)"
echo "[backup] $(date '+%F %T') Retained $REMAINING backups (pruned files older than ${RETAIN_DAYS}d)"

# Verify the backup is not empty
if [ ! -s "$BACKUP_FILE" ]; then
    echo "[backup] WARNING: Backup file is empty — check database connectivity" >&2
    exit 1
fi

echo "[backup] $(date '+%F %T') Done"
