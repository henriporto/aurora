#!/usr/bin/env bash
# Cria a infraestrutura no GCP: IP fixo, firewall, VM e bucket de backup.
# Idempotente: o que já existe é mantido.
#
#   cp deploy/gcp/config.exemplo.sh deploy/gcp/config.sh   # e ajuste
#   bash deploy/gcp/01_criar_infra.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/config.sh"

gcloud config set project "$PROJETO" >/dev/null
echo "==> Habilitando APIs (Compute e Storage)"
gcloud services enable compute.googleapis.com storage.googleapis.com

echo "==> IP externo fixo"
if ! gcloud compute addresses describe "$NOME_IP" --region "$REGIAO" >/dev/null 2>&1; then
  gcloud compute addresses create "$NOME_IP" --region "$REGIAO"
fi
IP=$(gcloud compute addresses describe "$NOME_IP" --region "$REGIAO" --format='value(address)')

echo "==> Firewall: HTTP e HTTPS para a tag $TAG_REDE"
if ! gcloud compute firewall-rules describe leis-mcp-web >/dev/null 2>&1; then
  gcloud compute firewall-rules create leis-mcp-web \
    --direction INGRESS --action ALLOW --rules tcp:80,tcp:443,udp:443 \
    --target-tags "$TAG_REDE" --source-ranges 0.0.0.0/0
fi

echo "==> VM $NOME_VM ($TIPO_MAQUINA, ${DISCO_GB} GB)"
if ! gcloud compute instances describe "$NOME_VM" --zone "$ZONA" >/dev/null 2>&1; then
  gcloud compute instances create "$NOME_VM" \
    --zone "$ZONA" \
    --machine-type "$TIPO_MAQUINA" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size "${DISCO_GB}GB" --boot-disk-type pd-balanced \
    --address "$IP" \
    --tags "$TAG_REDE" \
    --scopes default,storage-rw \
    --metadata-from-file startup-script="$DIR/preparar_vm.sh"
fi

echo "==> Bucket de backup $BUCKET_BACKUP"
if ! gcloud storage buckets describe "$BUCKET_BACKUP" >/dev/null 2>&1; then
  gcloud storage buckets create "$BUCKET_BACKUP" --location "$REGIAO" --uniform-bucket-level-access
fi

cat <<FIM

Infraestrutura pronta.
  IP da VM: $IP

Próximos passos:
  1. No seu provedor de DNS, crie um registro A: <seu domínio> -> $IP
  2. Aguarde o Docker ser instalado pela VM (~2 min) e envie o banco:
       bash deploy/gcp/02_enviar_banco.sh
  3. Preencha deploy/.env (a partir de deploy/exemplo.env) e publique:
       bash deploy/gcp/03_publicar_app.sh
FIM
