#!/usr/bin/env bash
# Prepara o GCP para receber deploys do GitHub Actions e imprime, no fim, os
# valores que você precisa colar no GitHub.
#
#   bash deploy/gcp/00_github_actions.sh
#
# Idempotente: rode de novo sem medo.
#
# POR QUE WORKLOAD IDENTITY FEDERATION, E NÃO UMA CHAVE JSON
# ----------------------------------------------------------
# A alternativa comum é criar uma chave de conta de serviço e guardá-la num
# secret. Essa chave é um segredo permanente: se vazar, vale até alguém
# revogá-la à mão. Aqui o GitHub troca o token OIDC daquela execução por um
# token do Google que dura minutos, e a troca só é aceita para ESTE repositório.
# Não há segredo de longa duração em lugar nenhum.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/config.sh"

: "${GITHUB_REPO:?defina GITHUB_REPO no config.sh (ex.: henriporto/aurora)}"
POOL="github"
PROVEDOR="github-oidc"
SA_NOME="leis-mcp-deploy"
SA="$SA_NOME@$PROJETO.iam.gserviceaccount.com"

gcloud config set project "$PROJETO" >/dev/null
NUMERO_PROJETO=$(gcloud projects describe "$PROJETO" --format='value(projectNumber)')

echo "==> Habilitando APIs (IAM Credentials, STS, IAP)"
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com \
  iap.googleapis.com compute.googleapis.com >/dev/null

echo "==> Conta de serviço do deploy: $SA"
if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NOME" \
    --display-name "Deploy do leis-mcp pelo GitHub Actions"
  sleep 10
fi

# Só o necessário para entrar na VM e rodar o deploy:
#   osAdminLogin        entra por SSH e pode usar sudo (o deploy roda docker)
#   iap.tunnelResourceAccessor  abre o túnel IAP (SSH sem expor a porta 22)
#   compute.viewer      descobre a instância, a zona e o estado
#   serviceAccountUser  necessário para operar uma VM que roda como outra conta
echo "==> Papéis"
for papel in roles/compute.osAdminLogin roles/iap.tunnelResourceAccessor roles/compute.viewer; do
  gcloud projects add-iam-policy-binding "$PROJETO" \
    --member "serviceAccount:$SA" --role "$papel" --condition=None >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding \
  "leis-mcp-vm@$PROJETO.iam.gserviceaccount.com" \
  --member "serviceAccount:$SA" --role roles/iam.serviceAccountUser >/dev/null 2>&1 \
  || echo "    (a conta da VM ainda não existe; rode o 01_criar_infra.sh e repita este script)"

echo "==> Pool de identidades $POOL"
if ! gcloud iam workload-identity-pools describe "$POOL" --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL" --location global \
    --display-name "GitHub Actions"
fi

echo "==> Provedor OIDC $PROVEDOR (restrito a $GITHUB_REPO)"
# A condição é a parte que importa: sem ela, QUALQUER repositório do GitHub
# conseguiria trocar seu token por credenciais deste projeto.
if ! gcloud iam workload-identity-pools providers describe "$PROVEDOR" \
      --location global --workload-identity-pool "$POOL" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$PROVEDOR" \
    --location global --workload-identity-pool "$POOL" \
    --display-name "GitHub OIDC" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '$GITHUB_REPO'"
else
  gcloud iam workload-identity-pools providers update-oidc "$PROVEDOR" \
    --location global --workload-identity-pool "$POOL" \
    --attribute-condition "assertion.repository == '$GITHUB_REPO'" >/dev/null
fi

echo "==> Autorizando $GITHUB_REPO a usar $SA_NOME"
PRINCIPAL="principalSet://iam.googleapis.com/projects/$NUMERO_PROJETO/locations/global/workloadIdentityPools/$POOL/attribute.repository/$GITHUB_REPO"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member "$PRINCIPAL" --role roles/iam.workloadIdentityUser >/dev/null

echo "==> Firewall para o túnel IAP (SSH sem abrir a porta 22 ao mundo)"
if ! gcloud compute firewall-rules describe leis-mcp-iap-ssh >/dev/null 2>&1; then
  gcloud compute firewall-rules create leis-mcp-iap-ssh \
    --direction INGRESS --action ALLOW --rules tcp:22 \
    --target-tags "$TAG_REDE" --source-ranges 35.235.240.0/20 \
    --description "SSH apenas pelo Identity-Aware Proxy" >/dev/null
fi

echo "==> Ligando OS Login na VM (é assim que a conta de serviço entra)"
gcloud compute instances add-metadata "$NOME_VM" --zone "$ZONA" \
  --metadata enable-oslogin=TRUE >/dev/null 2>&1 \
  || echo "    (VM ainda não existe; rode o 01_criar_infra.sh e repita este script)"

PROVEDOR_COMPLETO="projects/$NUMERO_PROJETO/locations/global/workloadIdentityPools/$POOL/providers/$PROVEDOR"

cat <<FIM

================================================================
Pronto. Configure no GitHub: repositório > Settings > Secrets and
variables > Actions.

--- Variables (aba "Variables", NÃO são segredos) --------------
GCP_PROJETO             $PROJETO
GCP_ZONA                $ZONA
GCP_VM                  $NOME_VM
GCP_WIF_PROVEDOR        $PROVEDOR_COMPLETO
GCP_SA_DEPLOY           $SA
GCP_BUCKET_BACKUP       $BUCKET_BACKUP
HF_REPO_BANCO           ${HF_REPO_BANCO:-<defina no config.sh>}

--- Secrets (aba "Secrets") ------------------------------------
Os mesmos valores do seu deploy/.env:

LEIS_DOMINIO            domínio, sem https://
LEIS_URL_PUBLICA        https://<domínio>
GOOGLE_CLIENT_ID        cliente OAuth "Aplicativo da Web"
GOOGLE_CLIENT_SECRET    idem
LEIS_JWT_CHAVE          32+ caracteres aleatórios
LEIS_ADMINS             e-mails separados por vírgula

Detalhes e o que fazer no console: docs/deploy_gcp.md, seção
"Deploy automático pelo GitHub Actions".
================================================================
FIM
