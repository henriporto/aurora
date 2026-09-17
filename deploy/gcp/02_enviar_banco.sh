#!/usr/bin/env bash
# Prepara o leis.db para produção, envia para a VM, confere o SHA-256 e troca
# o banco em uso de forma atômica. Use também para ATUALIZAR o banco depois de
# rodar a ingestão.
#
#   bash deploy/gcp/02_enviar_banco.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$DIR/../.." && pwd)"
source "$DIR/config.sh"

VERSAO="leis-$(date +%Y%m%d-%H%M%S).db"
LOCAL="$RAIZ/dados/$VERSAO"

echo "==> Preparando cópia de produção ($VERSAO)"
case "$BANCO_LOCAL" in /*) ORIGEM="$BANCO_LOCAL" ;; *) ORIGEM="$RAIZ/$BANCO_LOCAL" ;; esac
python3 "$RAIZ/scripts/preparar_banco.py" "$ORIGEM" "$LOCAL"

echo "==> Enviando para a VM (5,5 GB; demora conforme sua conexão)"
gcloud compute scp --zone "$ZONA" --compress "$LOCAL" "$LOCAL.sha256" "$NOME_VM":/tmp/

echo "==> Conferindo integridade e trocando o banco"
gcloud compute ssh "$NOME_VM" --zone "$ZONA" -- bash -s <<REMOTO
set -euo pipefail
cd /tmp
sha256sum -c "$VERSAO.sha256"
sudo mv "/tmp/$VERSAO" "/srv/leis/banco/$VERSAO"
sudo rm -f "/tmp/$VERSAO.sha256"
cd /srv/leis/banco
# Links RELATIVOS: dentro do contêiner a pasta é montada em /banco, e um link
# absoluto para /srv/leis/banco/... não existiria lá.
if [ -L leis.db ]; then
  sudo ln -sfn "\$(readlink leis.db)" leis.anterior.db
fi
sudo ln -sfn "$VERSAO" leis.db.tmp
sudo mv -Tf leis.db.tmp leis.db
# Remove versões antigas: mantém só a atual e a anterior.
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
