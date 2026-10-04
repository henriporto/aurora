#!/usr/bin/env bash
# Cria a infraestrutura no GCP: conta de serviço, bucket, IP, firewall e VM.
#   bash deploy/gcp/01_criar_infra.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/config.sh"

SA_NOME="leis-mcp-vm"
SA="$SA_NOME@$PROJETO.iam.gserviceaccount.com"

gcloud config set project "$PROJETO" >/dev/null
echo "==> Habilitando APIs (Compute, Storage, IAM, Logging, Monitoring)"
gcloud services enable compute.googleapis.com storage.googleapis.com iam.googleapis.com \
  logging.googleapis.com monitoring.googleapis.com

# Conta própria e mínima: a padrão do Compute tem papel Editor no projeto inteiro.
echo "==> Conta de serviço $SA"
if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NOME" --display-name "VM da Aurora"
  sleep 15
fi
for papel in roles/logging.logWriter roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "$PROJETO" \
    --member "serviceAccount:$SA" --role "$papel" --condition=None >/dev/null
done

echo "==> Bucket de backup $BUCKET_BACKUP"
if ! gcloud storage buckets describe "$BUCKET_BACKUP" >/dev/null 2>&1; then
  gcloud storage buckets create "$BUCKET_BACKUP" --location "$REGIAO" \
    --uniform-bucket-level-access --public-access-prevention
fi
# Cria e lê, mas não apaga: a VM não consegue destruir os backups.
for papel in roles/storage.objectCreator roles/storage.objectViewer; do
  gcloud storage buckets add-iam-policy-binding "$BUCKET_BACKUP" \
    --member "serviceAccount:$SA" --role "$papel" >/dev/null
done

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
    --service-account "$SA" --scopes cloud-platform \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata-from-file startup-script="$DIR/preparar_vm.sh"
else
  gcloud compute instances add-metadata "$NOME_VM" --zone "$ZONA" \
    --metadata-from-file startup-script="$DIR/preparar_vm.sh"
fi

cat <<FIM

Infraestrutura pronta.
  IP da VM: $IP
FIM
