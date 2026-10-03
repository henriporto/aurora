#!/usr/bin/env bash
# Instala na VM o banco publicado no Hugging Face.
#
#   bash deploy/gcp/02_banco_do_hf.sh [--forcar]
#
# Este é o caminho normal, e o que o GitHub Actions usa: a VM baixa direto do
# Hugging Face, sem passar pela sua máquina nem pelo runner. São 4 GB de
# download que viram 12 GB de banco — trafegar isso por um runner do Actions
# (14 GB de disco) não caberia, e pelo seu PC seria um upload de 12 GB.
#
# Para instalar um banco que você AINDA NÃO publicou, use 02_enviar_banco.sh.
#
# É idempotente e barato de repetir: compara o SHA-256 do manifesto do Hugging
# Face com o do banco em uso e não baixa nada se forem iguais. Por isso o
# workflow pode chamá-lo a cada deploy.
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

# Precisa caber o banco novo ao lado do atual e do anterior, mais folga para
# imagens e logs. O download é em streaming, então o .zst nunca ocupa disco.
PRECISA_GB=$(( BYTES / 1000000000 + 6 ))
LIVRE_GB=$(df -BG --output=avail /srv/leis | tail -1 | tr -dc 0-9)
if [ "$LIVRE_GB" -lt "$PRECISA_GB" ]; then
  echo "Espaço insuficiente: ${LIVRE_GB} GB livres, precisa de ${PRECISA_GB} GB." >&2
  echo "Apague o banco anterior (sudo rm \$(readlink -f $BANCO_DIR/leis.anterior.db)) ou aumente DISCO_GB." >&2
  exit 1
fi

VERSAO="leis-$(date +%Y%m%d-%H%M%S).db"
DESTINO="$BANCO_DIR/$VERSAO"
# Download interrompido não pode deixar um arquivo pela metade ocupando 12 GB.
trap 'sudo rm -f "$DESTINO"' ERR INT TERM
echo "--> Baixando e descomprimindo em streaming ($VERSAO)"
# O pipe evita guardar os 4 GB comprimidos: sai direto no arquivo final.
#
# Duas escolhas de forma, as duas por segurança e não por estilo:
#   - o pipe fica NESTE shell, que tem `pipefail`, e só o `tee` roda com sudo
#     (é quem escreve em /srv). Com `sudo bash -c "curl | zstd"`, o pipefail
#     não valeria dentro do shell novo e um download cortado passaria por bom;
#   - `zstd -dc` para stdout mais `tee`, em vez de `zstd -o`, porque essa é a
#     forma que funciona em qualquer versão do zstd; `-o` lendo de stdin varia
#     entre versões.
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
# A partir daqui o arquivo é bom: sair por erro não deve mais apagá-lo.
trap - ERR INT TERM

cd "$BANCO_DIR"
# Links RELATIVOS: a pasta é montada em /banco dentro do contêiner, e um link
# absoluto para /srv/leis/banco/... não existiria lá.
if [ -L leis.db ]; then
  sudo ln -sfn "$(readlink leis.db)" leis.anterior.db
fi
sudo ln -sfn "$VERSAO" leis.db.tmp
sudo mv -Tf leis.db.tmp leis.db
printf '%s' "$SHA_NOVO" | sudo tee "$MARCA" >/dev/null

# Mantém só a versão em uso e a anterior.
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
