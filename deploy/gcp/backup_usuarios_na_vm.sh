#!/usr/bin/env bash
# Roda na VM (cron diário): copia o usuarios.db e envia ao bucket de backup.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/config.sh" 2>/dev/null || { echo "config.sh ausente na VM; copie deploy/gcp/config.sh junto do app" >&2; exit 1; }

CARIMBO=$(date -u +%Y%m%d-%H%M%S)
cd /srv/leis/app/deploy
docker compose exec -T leis-mcp python -c "
import sqlite3
origem = sqlite3.connect('/dados/usuarios.db')
destino = sqlite3.connect('/dados/backup-usuarios.db')
origem.backup(destino)
destino.close()
"
docker compose cp leis-mcp:/dados/backup-usuarios.db "/srv/leis/backup/usuarios-$CARIMBO.db"
gcloud storage cp "/srv/leis/backup/usuarios-$CARIMBO.db" "$BUCKET_BACKUP/usuarios/"
ls -1t /srv/leis/backup/usuarios-*.db | tail -n +15 | xargs -r rm -f
echo "$(date -u) backup usuarios-$CARIMBO.db enviado"
