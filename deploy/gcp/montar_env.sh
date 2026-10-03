#!/usr/bin/env bash
# Monta deploy/.env a partir de variáveis de ambiente, usando deploy/exemplo.env
# como molde.
#
#   LEIS_DOMINIO=... GOOGLE_CLIENT_ID=... bash deploy/gcp/montar_env.sh
#
# Existe para o GitHub Actions, que tem os segredos no ambiente e não pode
# guardar um .env no repositório. O molde é o exemplo.env justamente para não
# haver duas listas de variáveis: uma chave nova entra lá e chega aqui sozinha,
# com o valor padrão que o exemplo já traz.
#
# Só as variáveis presentes no ambiente são substituídas; o resto fica como o
# exemplo define. Recusa terminar se faltar uma das obrigatórias.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$DIR/../.." && pwd)"
MOLDE="$RAIZ/deploy/exemplo.env"
DESTINO="${1:-$RAIZ/deploy/.env}"

OBRIGATORIAS=(LEIS_DOMINIO LEIS_URL_PUBLICA GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET LEIS_JWT_CHAVE LEIS_ADMINS)
faltando=()
for variavel in "${OBRIGATORIAS[@]}"; do
  [ -n "${!variavel:-}" ] || faltando+=("$variavel")
done
if [ ${#faltando[@]} -gt 0 ]; then
  echo "Faltam variáveis no ambiente: ${faltando[*]}" >&2
  echo "No GitHub elas são secrets do repositório; localmente, exporte antes de chamar." >&2
  exit 1
fi

umask 077   # o arquivo nasce 600: contém segredos
: > "$DESTINO"
while IFS= read -r linha; do
  if [[ "$linha" =~ ^([A-Z_][A-Z0-9_]*)= ]]; then
    chave="${BASH_REMATCH[1]}"
    if [ -n "${!chave:-}" ]; then
      printf '%s=%s\n' "$chave" "${!chave}" >> "$DESTINO"
      continue
    fi
  fi
  printf '%s\n' "$linha" >> "$DESTINO"
done < "$MOLDE"

# Conferências que o 03_publicar_app.sh repete antes de publicar; falhar aqui
# dá uma mensagem melhor e mais cedo.
dominio=$(grep -E '^LEIS_DOMINIO=' "$DESTINO" | cut -d= -f2-)
url=$(grep -E '^LEIS_URL_PUBLICA=' "$DESTINO" | cut -d= -f2-)
if [ "${url%/}" != "https://$dominio" ]; then
  echo "LEIS_URL_PUBLICA ($url) precisa ser https://$dominio, senão o login quebra." >&2
  exit 1
fi
if [ "$(printf '%s' "$(grep -E '^LEIS_JWT_CHAVE=' "$DESTINO" | cut -d= -f2-)" | wc -c)" -lt 32 ]; then
  echo "LEIS_JWT_CHAVE precisa de 32+ caracteres." >&2
  exit 1
fi

echo "$DESTINO escrito ($(grep -cE '^[A-Z]' "$DESTINO") variáveis)."
