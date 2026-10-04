#!/usr/bin/env bash
# Instala na VM o banco publicado no Hugging Face.
#   bash deploy/gcp/02_banco_do_hf.sh [--forcar]
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/config.sh"
TUNEL=${IAP:+--tunnel-through-iap}

FORCAR="nao"
[ "${1:-}" = "--forcar" ] && FORCAR="sim"

: "${HF_REPO_BANCO:?defina HF_REPO_BANCO no config.sh}"
BASE_URL="https://huggingface.co/datasets/$HF_REPO_BANCO/resolve/main"

echo "==> Instalando o banco de $HF_REPO_BANCO na VM $NOME_VM"
gcloud compute ssh "$NOME_VM" --zone "$ZONA" $TUNEL --quiet \
  -- bash -s -- "$BASE_URL" "$FORCAR" <<'REMOTO'
set -euo pipefail
BASE_URL="$1"
FORCAR="$2"
BANCO_DIR=/srv/leis/banco
MARCA="$BANCO_DIR/.sha256_em_uso"

command -v zstd >/dev/null || { sudo apt-get update -y && sudo apt-get install -y zstd; }

echo "--> Lendo o manifesto"
MANIFESTO=$(curl -fsSL "$BASE_URL/leis.db.json")
SHA_NOVO=$(printf '%s' "$MANIFESTO" | grep -o '"sha256": *"[^"]*"' | cut -d'"' -f4)
BYTES=$(printf '%s' "$MANIFESTO" | grep -o '"bytes": *[0-9]*' | grep -o '[0-9]*')
ARQUIVO=$(printf '%s' "$MANIFESTO" | grep -o '"arquivo": *"[^"]*"' | cut -d'"' -f4)
[ -n "$SHA_NOVO" ] && [ -n "$BYTES" ] && [ -n "$ARQUIVO" ] || {
  echo "Manifesto inválido em $BASE_URL/leis.db.json" >&2; exit 1; }
echo "    publicado: $ARQUIVO -> $(( BYTES / 1000000000 )) GB descomprimido"
echo "    sha256:    $SHA_NOVO"

if [ "$FORCAR" != "sim" ] && [ -f "$MARCA" ] && [ -L "$BANCO_DIR/leis.db" ] \
   && [ "$(cat "$MARCA")" = "$SHA_NOVO" ]; then
  echo "O banco em uso já é este. Nada a fazer."
  exit 0
fi

PRECISA_GB=$(( BYTES / 1000000000 + 6 ))
LIVRE_GB=$(df -BG --output=avail /srv/leis | tail -1 | tr -dc 0-9)
if [ "$LIVRE_GB" -lt "$PRECISA_GB" ]; then
  echo "Espaço insuficiente: ${LIVRE_GB} GB livres, precisa de ${PRECISA_GB} GB." >&2
  echo "Apague o banco anterior (sudo rm \$(readlink -f $BANCO_DIR/leis.anterior.db)) ou aumente DISCO_GB." >&2
  exit 1
fi

VERSAO="leis-$(date +%Y%m%d-%H%M%S).db"
DESTINO="$BANCO_DIR/$VERSAO"
trap 'sudo rm -f "$DESTINO"' ERR INT TERM
echo "--> Baixando e descomprimindo em streaming ($VERSAO)"
# Pipe neste shell (com pipefail): download cortado falha em vez de passar por bom.
curl -fsSL "$BASE_URL/$ARQUIVO" | zstd -dc | sudo tee "$DESTINO" > /dev/null

echo "--> Conferindo SHA-256"
SHA_BAIXADO=$(sudo sha256sum "$DESTINO" | cut -d' ' -f1)
if [ "$SHA_BAIXADO" != "$SHA_NOVO" ]; then
  echo "SHA-256 não confere. Esperado $SHA_NOVO, veio $SHA_BAIXADO." >&2
  exit 1
fi
TAMANHO=$(stat -c %s "$DESTINO")
if [ "$TAMANHO" != "$BYTES" ]; then
  echo "Tamanho não confere. Esperado $BYTES, veio $TAMANHO." >&2
  exit 1
fi
echo "    confere."
trap - ERR INT TERM

# Links relativos: a pasta é montada em /banco dentro do contêiner.
cd "$BANCO_DIR"
if [ -L leis.db ]; then
  sudo ln -sfn "$(readlink leis.db)" leis.anterior.db
fi
sudo ln -sfn "$VERSAO" leis.db.tmp
sudo mv -Tf leis.db.tmp leis.db
printf '%s' "$SHA_NOVO" | sudo tee "$MARCA" >/dev/null

ATUAL="$(readlink leis.db)"
ANTERIOR="$(readlink leis.anterior.db 2>/dev/null || true)"
for arquivo in leis-*.db; do
  if [ "$arquivo" != "$ATUAL" ] && [ "$arquivo" != "$ANTERIOR" ]; then
    sudo rm -f "$arquivo"
  fi
done

if [ -f /srv/leis/app/deploy/compose.yaml ] \
   && sudo docker compose -f /srv/leis/app/deploy/compose.yaml ps -q leis-mcp | grep -q .; then
  echo "--> Reiniciando o servidor para carregar o banco novo"
  cd /srv/leis/app/deploy && sudo docker compose restart leis-mcp
fi
ls -lh "$BANCO_DIR"
REMOTO

echo "Banco do Hugging Face instalado."
