#!/usr/bin/env bash
# Publica (ou atualiza) o código na VM e sobe os contêineres.
#
#   bash deploy/gcp/03_publicar_app.sh
#
# Envia o conteúdo do último commit (git archive), não arquivos soltos da sua
# máquina. Envia também deploy/.env, que contém segredos, por SSH.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$DIR/../.." && pwd)"
source "$DIR/config.sh"

if [ ! -f "$RAIZ/deploy/.env" ]; then
  echo "Crie deploy/.env a partir de deploy/exemplo.env antes de publicar." >&2
  exit 1
fi
for variavel in LEIS_DOMINIO LEIS_URL_PUBLICA GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET LEIS_JWT_CHAVE LEIS_ADMINS; do
  if ! grep -Eq "^${variavel}=.+" "$RAIZ/deploy/.env"; then
    echo "deploy/.env: $variavel está vazia." >&2
    exit 1
  fi
done
if [ -n "$(git -C "$RAIZ" status --porcelain -- src deploy pyproject.toml uv.lock)" ]; then
  echo "Aviso: há mudanças não commitadas em src/, deploy/ ou dependências; elas NÃO serão publicadas." >&2
fi

PACOTE="$(mktemp -d)/leis-mcp.tar.gz"
git -C "$RAIZ" archive --format=tar.gz -o "$PACOTE" HEAD

echo "==> Enviando código e configuração"
gcloud compute scp --zone "$ZONA" "$PACOTE" "$RAIZ/deploy/.env" "$DIR/config.sh" "$NOME_VM":/tmp/

echo "==> Construindo e subindo na VM"
gcloud compute ssh "$NOME_VM" --zone "$ZONA" -- bash -s <<'REMOTO'
set -euo pipefail
sudo rm -rf /srv/leis/app.novo && sudo mkdir -p /srv/leis/app.novo
sudo tar -xzf /tmp/leis-mcp.tar.gz -C /srv/leis/app.novo
sudo install -m 600 /tmp/.env /srv/leis/app.novo/deploy/.env
sudo install -m 644 /tmp/config.sh /srv/leis/app.novo/deploy/gcp/config.sh
rm -f /tmp/leis-mcp.tar.gz /tmp/.env /tmp/config.sh
sudo rm -rf /srv/leis/app.anterior
[ -d /srv/leis/app ] && sudo mv /srv/leis/app /srv/leis/app.anterior
sudo mv /srv/leis/app.novo /srv/leis/app
sudo chmod +x /srv/leis/app/deploy/gcp/*.sh
cd /srv/leis/app/deploy
sudo docker compose up -d --build
echo "Aguardando o servidor ficar saudável (aquecimento ~20-40 s)..."
for i in $(seq 1 60); do
  estado=$(sudo docker inspect --format '{{.State.Health.Status}}' "$(sudo docker compose ps -q leis-mcp)")
  [ "$estado" = "healthy" ] && break
  sleep 5
done
sudo docker compose ps
REMOTO

echo "Publicado. Teste: curl -s https://<seu domínio>/saude"
