#!/usr/bin/env bash
# Publica (ou atualiza) o código na VM e sobe os contêineres.
#
#   bash deploy/gcp/03_publicar_app.sh
#
# Envia o conteúdo do último commit (git archive), não arquivos soltos da sua
# máquina. Envia também deploy/.env, que contém segredos, por SSH.
set -euo pipefail
# Com IAP=1 no ambiente, o SSH/SCP passa pelo túnel do Identity-Aware Proxy
# (é assim que o GitHub Actions entra na VM, sem porta 22 aberta ao mundo).
# Sem isso, segue o SSH direto de sempre.
DIR="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$DIR/../.." && pwd)"
source "$DIR/config.sh"
TUNEL=${IAP:+--tunnel-through-iap}

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
DOMINIO=$(grep -E '^LEIS_DOMINIO=' "$RAIZ/deploy/.env" | cut -d= -f2-)
URL=$(grep -E '^LEIS_URL_PUBLICA=' "$RAIZ/deploy/.env" | cut -d= -f2-)
if [ "${URL%/}" != "https://$DOMINIO" ]; then
  echo "deploy/.env: LEIS_URL_PUBLICA ($URL) deve ser https://$DOMINIO; senão o redirecionamento do login quebra." >&2
  exit 1
fi
if [ "$(grep -E '^LEIS_JWT_CHAVE=' "$RAIZ/deploy/.env" | cut -d= -f2- | tr -d '\n' | wc -c)" -lt 32 ]; then
  echo "deploy/.env: LEIS_JWT_CHAVE precisa de 32+ caracteres." >&2
  exit 1
fi
# scp preserva as permissões: o .env chega à VM legível só pelo dono.
chmod 600 "$RAIZ/deploy/.env"
if [ -n "$(git -C "$RAIZ" status --porcelain -- src deploy pyproject.toml uv.lock)" ]; then
  echo "Aviso: há mudanças não commitadas em src/, deploy/ ou dependências; elas NÃO serão publicadas." >&2
fi

PACOTE="$(mktemp -d)/leis-mcp.tar.gz"
git -C "$RAIZ" archive --format=tar.gz -o "$PACOTE" HEAD

echo "==> Enviando código e configuração"
gcloud compute scp --zone "$ZONA" $TUNEL --quiet \
  "$PACOTE" "$RAIZ/deploy/.env" "$DIR/config.sh" "$NOME_VM":/tmp/

echo "==> Construindo e subindo na VM"
gcloud compute ssh "$NOME_VM" --zone "$ZONA" $TUNEL --quiet -- bash -s <<'REMOTO'
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
# Remove imagens antigas que ficaram sem uso (cada build deixa uma).
sudo docker image prune -f >/dev/null
echo "Aguardando o servidor ficar saudável (aquecimento: 1 a 5 min)..."
estado=""
for i in $(seq 1 72); do
  estado=$(sudo docker inspect --format '{{.State.Health.Status}}' "$(sudo docker compose ps -q leis-mcp)")
  [ "$estado" = "healthy" ] && break
  sleep 5
done
sudo docker compose ps
if [ "$estado" != "healthy" ]; then
  echo "O servidor NÃO ficou saudável (estado: $estado). Últimas linhas do log:" >&2
  sudo docker compose logs --tail 40 leis-mcp >&2
  exit 1
fi
REMOTO

echo "Publicado. Teste: curl -s https://$DOMINIO/saude"
