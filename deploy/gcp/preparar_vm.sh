#!/usr/bin/env bash
# Script de inicialização da VM (roda como root a cada boot; é idempotente).
# Passado em `--metadata-from-file startup-script=` por 01_criar_infra.sh.
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y ca-certificates curl sqlite3
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

mkdir -p /srv/leis/app /srv/leis/banco /srv/leis/backup

# Usuários que entram por `gcloud compute ssh` usam docker sem sudo.
for usuario in $(ls /home); do
  id "$usuario" >/dev/null 2>&1 && usermod -aG docker "$usuario" || true
done

# Backup diário do usuarios.db às 03:30 (horário da VM, UTC).
cat > /etc/cron.d/leis-backup <<'CRON'
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
30 3 * * * root /srv/leis/app/deploy/gcp/backup_usuarios_na_vm.sh >> /var/log/leis-backup.log 2>&1
CRON
