#!/usr/bin/env bash
set -euo pipefail

ROOT=/srv/asistent-financiar
ENV_FILE=/etc/asistent-financiar/backup.env
STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
mkdir -p "$ROOT/data/backups"
chmod 700 "$ROOT/data/backups"
set -a
source "$ENV_FILE"
set +a
cd "$ROOT/app"
docker compose -f deploy/oracle/compose.yaml run --rm --no-deps \
  -e BACKUP_ENCRYPTION_KEY \
  app python /app/scripts/backup_database.py --create \
  --database /var/data/finante.sqlite3 \
  --backup "/var/data/backups/finante-$STAMP.afpb"
find "$ROOT/data/backups" -type f -name 'finante-*.afpb' -mtime +14 -delete
