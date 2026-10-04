#!/usr/bin/env bash
# Envia o leis.db local para a VM e troca o banco em uso.
#   bash deploy/gcp/02_enviar_banco.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$DIR/../.." && pwd)"
source "$DIR/config.sh"
TUNEL=${IAP:+--tunnel-through-iap}

VERSAO="leis-$(date +%Y%m%d-%H%M%S).db"
LOCAL="$RAIZ/dados/$VERSAO"

echo "==> Preparando cópia de produção ($VERSAO)"
case "$BANCO_LOCAL" in /*) ORIGEM="$BANCO_LOCAL" ;; *) ORIGEM="$RAIZ/$BANCO_LOCAL" ;; esac
python3 "$RAIZ/scripts/preparar_banco.py" "$ORIGEM" "$LOCAL"

TAMANHO_GB=$(( $(stat -c %s "$LOCAL") / 1000000000 + 1 ))
echo "==> Conferindo espaço na VM (${TAMANHO_GB} GB em /tmp e depois em /srv/leis/banco)"
LIVRE_GB=$(gcloud compute ssh "$NOME_VM" --zone "$ZONA" $TUNEL --quiet -- df -BG --output=avail /srv/leis | tail -1 | tr -dc 0-9)
if [ "$LIVRE_GB" -lt $(( TAMANHO_GB + 5 )) ]; then
  echo "Espaço insuficiente na VM: ${LIVRE_GB} GB livres, precisa de $(( TAMANHO_GB + 5 )) GB." >&2
  echo "Apague a versão anterior (sudo rm /srv/leis/banco/leis.anterior.db e o arquivo para onde ele aponta) ou aumente DISCO_GB." >&2
  rm -f "$LOCAL" "$LOCAL.sha256"
  exit 1
fi

echo "==> Enviando para a VM (${TAMANHO_GB} GB; demora conforme sua conexão)"
gcloud compute scp --zone "$ZONA" $TUNEL --quiet --compress "$LOCAL" "$LOCAL.sha256" "$NOME_VM":/tmp/

echo "==> Conferindo integridade e trocando o banco"
gcloud compute ssh "$NOME_VM" --zone "$ZONA" $TUNEL --quiet -- bash -s <<REMOTO
set -euo pipefail
cd /tmp
sha256sum -c "$VERSAO.sha256"
sudo mv "/tmp/$VERSAO" "/srv/leis/banco/$VERSAO"
sudo rm -f "/tmp/$VERSAO.sha256"
# Links relativos: a pasta é montada em /banco dentro do contêiner.
cd /srv/leis/banco
if [ -L leis.db ]; then
  sudo ln -sfn "\$(readlink leis.db)" leis.anterior.db
fi
sudo ln -sfn "$VERSAO" leis.db.tmp
sudo mv -Tf leis.db.tmp leis.db
ATUAL="\$(readlink leis.db)"
ANTERIOR="\$(readlink leis.anterior.db 2>/dev/null || true)"
for arquivo in leis-*.db; do
  if [ "\$arquivo" != "\$ATUAL" ] && [ "\$arquivo" != "\$ANTERIOR" ]; then
    sudo rm -f "\$arquivo"
  fi
done
if [ -f /srv/leis/app/deploy/compose.yaml ] && sudo docker compose -f /srv/leis/app/deploy/compose.yaml ps -q leis-mcp | grep -q .; then
  echo "Reiniciando o servidor para carregar o banco novo..."
  cd /srv/leis/app/deploy && sudo docker compose restart leis-mcp
fi
ls -lh /srv/leis/banco/
REMOTO

rm -f "$LOCAL" "$LOCAL.sha256"
echo "Banco $VERSAO em uso."
