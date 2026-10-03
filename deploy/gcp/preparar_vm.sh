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

# Fora do bloco acima de propósito: numa VM que já tinha Docker, aquele `if`
# nunca roda, e o zstd (que o 02_banco_do_hf.sh usa para descomprimir o banco
# vindo do Hugging Face) nunca seria instalado.
if ! command -v zstd >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y zstd
fi

mkdir -p /srv/leis/app /srv/leis/banco /srv/leis/backup

# Usuários que entram por `gcloud compute ssh` usam docker sem sudo. Só vale
# para contas que já existiam neste boot; antes disso, use sudo.
for usuario in $(ls /home); do
  id "$usuario" >/dev/null 2>&1 && usermod -aG docker "$usuario" || true
done

# Backup diário do usuarios.db às 03:30 (horário da VM, UTC).
cat > /etc/cron.d/leis-backup <<'CRON'
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
30 3 * * * root /srv/leis/app/deploy/gcp/backup_usuarios_na_vm.sh >> /var/log/leis-backup.log 2>&1
CRON

# Ops Agent: memória e disco no Cloud Monitoring (sem ele o GCP só vê CPU e
# rede) e os logs dos contêineres no Cloud Logging, de onde saem as métricas
# de chamadas do painel (04_monitoramento.sh). Falha aqui não impede o servidor.
if ! dpkg -s google-cloud-ops-agent >/dev/null 2>&1; then
  (cd /tmp && curl -fsSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh \
    && bash add-google-cloud-ops-agent-repo.sh --also-install) \
    || echo "Aviso: instalação do Ops Agent falhou; rode de novo com: sudo google_metadata_script_runner startup" >&2
fi
if [ -d /etc/google-cloud-ops-agent ]; then
  cat > /etc/google-cloud-ops-agent/config.yaml.novo <<'OPS'
logging:
  receivers:
    docker_leis:
      type: files
      include_paths:
        - /var/lib/docker/containers/*/*-json.log
  processors:
    docker_json:
      type: parse_json
  service:
    pipelines:
      docker_leis:
        receivers: [docker_leis]
        processors: [docker_json]
OPS
  if ! cmp -s /etc/google-cloud-ops-agent/config.yaml.novo /etc/google-cloud-ops-agent/config.yaml; then
    mv /etc/google-cloud-ops-agent/config.yaml.novo /etc/google-cloud-ops-agent/config.yaml
    systemctl restart google-cloud-ops-agent || true
  else
    rm -f /etc/google-cloud-ops-agent/config.yaml.novo
  fi
fi
