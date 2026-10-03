#!/usr/bin/env bash
# Monitoramento no Cloud Monitoring: verificação de disponibilidade de /saude,
# métricas de chamadas (a partir dos logs), alertas por e-mail e um painel.
# Idempotente: o que já existe (pelo nome) é mantido.
#
#   bash deploy/gcp/04_monitoramento.sh
#
# Pré-requisitos: 03_publicar_app.sh já rodou (o domínio responde em HTTPS) e a
# VM tem o Ops Agent (instalado por preparar_vm.sh).
# Usa a API REST com o token do `gcloud auth login`: não precisa dos
# componentes alpha/beta do gcloud.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$DIR/../.." && pwd)"
source "$DIR/config.sh"

if [ -z "${EMAIL_ALERTAS:-}" ]; then
  echo "Defina EMAIL_ALERTAS em deploy/gcp/config.sh." >&2
  exit 1
fi
DOMINIO=$(grep -E '^LEIS_DOMINIO=' "$RAIZ/deploy/.env" | cut -d= -f2-)
if [ -z "$DOMINIO" ]; then
  echo "deploy/.env: LEIS_DOMINIO está vazia." >&2
  exit 1
fi

gcloud config set project "$PROJETO" >/dev/null
gcloud services enable logging.googleapis.com monitoring.googleapis.com

PROJETO="$PROJETO" DOMINIO="$DOMINIO" EMAIL_ALERTAS="$EMAIL_ALERTAS" \
TOKEN="$(gcloud auth print-access-token)" python3 - <<'PY'
import json
import os
import urllib.error
import urllib.request

P = os.environ["PROJETO"]
DOMINIO = os.environ["DOMINIO"]
EMAIL = os.environ["EMAIL_ALERTAS"]
TOKEN = os.environ["TOKEN"]
MON = f"https://monitoring.googleapis.com/v3/projects/{P}"
LOG = f"https://logging.googleapis.com/v2/projects/{P}"
DASH = f"https://monitoring.googleapis.com/v1/projects/{P}/dashboards"


def api(metodo, url, corpo=None, aceitar_404=False):
    req = urllib.request.Request(
        url,
        method=metodo,
        data=None if corpo is None else json.dumps(corpo).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "x-goog-user-project": P,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if aceitar_404 and e.code == 404:
            return None
        raise SystemExit(f"{metodo} {url}\n{e.code}: {e.read().decode()[:800]}")


def listar(url, chave):
    itens, pagina = [], ""
    while True:
        sep = "&" if "?" in url else "?"
        r = api("GET", f"{url}{sep}pageSize=100" + (f"&pageToken={pagina}" if pagina else ""))
        itens += r.get(chave, [])
        pagina = r.get("nextPageToken")
        if not pagina:
            return itens


# ---- Canal de notificação -------------------------------------------------
canais = [
    c for c in listar(f"{MON}/notificationChannels", "notificationChannels")
    if c.get("type") == "email" and c.get("labels", {}).get("email_address") == EMAIL
]
if canais:
    canal = canais[0]["name"]
else:
    canal = api("POST", f"{MON}/notificationChannels", {
        "type": "email",
        "displayName": f"leis-mcp: {EMAIL}",
        "labels": {"email_address": EMAIL},
    })["name"]
    print(f"Canal de e-mail criado: {EMAIL}")

# ---- Verificação de disponibilidade ---------------------------------------
NOME_UPTIME = "leis-mcp /saude"
existentes = [
    u for u in listar(f"{MON}/uptimeCheckConfigs", "uptimeCheckConfigs")
    if u.get("displayName") == NOME_UPTIME
]
if existentes:
    uptime = existentes[0]["name"]
else:
    # /saude devolve 503 enquanto aquece e 200 quando banco e buscador estão prontos.
    uptime = api("POST", f"{MON}/uptimeCheckConfigs", {
        "displayName": NOME_UPTIME,
        "monitoredResource": {"type": "uptime_url", "labels": {"project_id": P, "host": DOMINIO}},
        "httpCheck": {"path": "/saude", "port": 443, "useSsl": True, "validateSsl": True,
                      "requestMethod": "GET"},
        "period": "60s",
        "timeout": "10s",
    })["name"]
    print(f"Verificação de disponibilidade criada: https://{DOMINIO}/saude")
check_id = uptime.rsplit("/", 1)[-1]

# ---- Métricas a partir dos logs --------------------------------------------
# Cada chamada gera uma linha "chamada ferramenta=X status=Y duracao_ms=N
# espera_ms=N usuario_id=N" (usuarios/controle.py), lida pelo Ops Agent dos
# arquivos do Docker para o log "docker_leis".
FILTRO_CHAMADA = f'logName="projects/{P}/logs/docker_leis" AND jsonPayload.log:"chamada ferramenta="'
ROTULO = {"key": "ferramenta", "valueType": "STRING"}
EXTRAI_FERRAMENTA = r'REGEXP_EXTRACT(jsonPayload.log, "ferramenta=([^ ]+)")'
metricas = {
    "leis_chamadas": {
        "description": "Chamadas ao leis-mcp por ferramenta e status (ok, erro, negado_*, ocupado)",
        "filter": FILTRO_CHAMADA,
        "metricDescriptor": {"metricKind": "DELTA", "valueType": "INT64", "unit": "1",
                             "labels": [ROTULO, {"key": "status", "valueType": "STRING"}]},
        "labelExtractors": {"ferramenta": EXTRAI_FERRAMENTA,
                            "status": r'REGEXP_EXTRACT(jsonPayload.log, "status=([^ ]+)")'},
    },
    "leis_duracao_ms": {
        "description": "Duração das chamadas ao leis-mcp (ms), incluindo a espera na fila",
        "filter": FILTRO_CHAMADA + ' AND jsonPayload.log=~"duracao_ms=[0-9]+"',
        "metricDescriptor": {"metricKind": "DELTA", "valueType": "DISTRIBUTION", "unit": "ms",
                             "labels": [ROTULO]},
        "valueExtractor": 'REGEXP_EXTRACT(jsonPayload.log, "duracao_ms=([0-9]+)")',
        "labelExtractors": {"ferramenta": EXTRAI_FERRAMENTA},
        "bucketOptions": {"exponentialBuckets": {"numFiniteBuckets": 20, "growthFactor": 2, "scale": 10}},
    },
    "leis_espera_ms": {
        "description": "Espera por vaga das buscas pesadas do leis-mcp (ms)",
        "filter": FILTRO_CHAMADA + ' AND jsonPayload.log=~"espera_ms=[1-9][0-9]*"',
        "metricDescriptor": {"metricKind": "DELTA", "valueType": "DISTRIBUTION", "unit": "ms",
                             "labels": [ROTULO]},
        "valueExtractor": 'REGEXP_EXTRACT(jsonPayload.log, "espera_ms=([0-9]+)")',
        "labelExtractors": {"ferramenta": EXTRAI_FERRAMENTA},
        "bucketOptions": {"exponentialBuckets": {"numFiniteBuckets": 20, "growthFactor": 2, "scale": 10}},
    },
}
for nome, corpo in metricas.items():
    if api("GET", f"{LOG}/metrics/{nome}", aceitar_404=True) is None:
        api("POST", f"{LOG}/metrics", {"name": nome, **corpo})
        print(f"Métrica de log criada: {nome}")


def metrica(nome):
    # Os logs chegam pelo Ops Agent da VM; a API de alertas exige o resource.type.
    return f'metric.type="logging.googleapis.com/user/{nome}" AND resource.type="gce_instance"'


# ---- Alertas ---------------------------------------------------------------
def limiar(filtro, alinhamento, aligner, comparacao, valor, duracao, redutor=None, grupos=None):
    agregacao = {"alignmentPeriod": alinhamento, "perSeriesAligner": aligner}
    if redutor:
        agregacao["crossSeriesReducer"] = redutor
        agregacao["groupByFields"] = grupos or []
    return {"conditionThreshold": {
        "filter": filtro, "aggregations": [agregacao], "comparison": comparacao,
        "thresholdValue": valor, "duration": duracao, "trigger": {"count": 1},
    }}


VM = 'resource.type="gce_instance"'
alertas = [
    ("leis-mcp: fora do ar",
     "https://" + DOMINIO + "/saude falhou em 2 ou mais regiões por 5 minutos. Veja "
     "`sudo docker compose ps` e `sudo docker compose logs --tail 100 leis-mcp` na VM.",
     limiar('metric.type="monitoring.googleapis.com/uptime_check/check_passed" AND '
            f'resource.type="uptime_url" AND metric.label.check_id="{check_id}"',
            "300s", "ALIGN_NEXT_OLDER", "COMPARISON_GT", 1, "300s",
            "REDUCE_COUNT_FALSE", ["resource.label.host"])),
    ("leis-mcp: memória acima de 90%",
     "Memória da VM acima de 90% por 10 minutos. O contêiner tem limite de 6 GB; perto "
     "dele o processo é morto e reinicia. Veja `docker stats --no-stream`.",
     limiar(f'metric.type="agent.googleapis.com/memory/percent_used" AND {VM} AND metric.label.state="used"',
            "300s", "ALIGN_MEAN", "COMPARISON_GT", 90, "600s")),
    ("leis-mcp: CPU acima de 90% por 15 min",
     "CPU saturada por 15 minutos: uso intenso ou sobrecarga. Compare com o gráfico de "
     "chamadas e de espera no painel leis-mcp.",
     limiar(f'metric.type="compute.googleapis.com/instance/cpu/utilization" AND {VM}',
            "300s", "ALIGN_MEAN", "COMPARISON_GT", 0.9, "900s")),
    ("leis-mcp: disco acima de 85%",
     "Disco da VM acima de 85%. Libere com `sudo docker image prune -a` ou apague a versão "
     "anterior do banco em /srv/leis/banco.",
     limiar(f'metric.type="agent.googleapis.com/disk/percent_used" AND {VM} AND metric.label.state="used"',
            "300s", "ALIGN_MEAN", "COMPARISON_GT", 85, "600s")),
    ("leis-mcp: servidor ocupado (sobrecarga)",
     "Mais de 3 chamadas recusadas por falta de vaga em 10 minutos: há mais buscas "
     "simultâneas do que a VM atende. Veja docs/deploy_gcp.md, seção Capacidade.",
     limiar(metrica("leis_chamadas") + ' AND metric.label.status="ocupado"',
            "600s", "ALIGN_SUM", "COMPARISON_GT", 3, "0s", "REDUCE_SUM")),
    ("leis-mcp: acesso recusado (conta pendente ou bloqueada)",
     "Alguém sem acesso liberado tentou usar o servidor. Contas pendentes: "
     "`sudo docker compose exec leis-mcp leis-admin usuarios listar --papel pendente`.",
     limiar(metrica("leis_chamadas") + ' AND metric.label.status="negado_papel"',
            "600s", "ALIGN_SUM", "COMPARISON_GT", 0, "0s", "REDUCE_SUM")),
]
nomes = {a.get("displayName") for a in listar(f"{MON}/alertPolicies", "alertPolicies")}
for titulo, texto, condicao in alertas:
    if titulo in nomes:
        continue
    api("POST", f"{MON}/alertPolicies", {
        "displayName": titulo,
        "combiner": "OR",
        "conditions": [{"displayName": titulo, **condicao}],
        "notificationChannels": [canal],
        "documentation": {"content": texto, "mimeType": "text/markdown"},
        "alertStrategy": {"autoClose": "1800s"},
    })
    print(f"Alerta criado: {titulo}")

# ---- Painel ----------------------------------------------------------------
def grafico(titulo, filtro, aligner, redutor=None, grupos=None, tipo="LINE", periodo="60s"):
    agregacao = {"alignmentPeriod": periodo, "perSeriesAligner": aligner}
    if redutor:
        agregacao["crossSeriesReducer"] = redutor
        agregacao["groupByFields"] = grupos or []
    return {"title": titulo, "xyChart": {"dataSets": [{
        "timeSeriesQuery": {"timeSeriesFilter": {"filter": filtro, "aggregation": agregacao}},
        "plotType": tipo, "minAlignmentPeriod": periodo,
    }]}}


graficos = [
    grafico("Chamadas por status (por minuto)", metrica("leis_chamadas"), "ALIGN_SUM",
            "REDUCE_SUM", ["metric.label.status"], "STACKED_BAR"),
    grafico("Chamadas por ferramenta (por minuto)", metrica("leis_chamadas"), "ALIGN_SUM",
            "REDUCE_SUM", ["metric.label.ferramenta"], "STACKED_BAR"),
    grafico("Duração p95 por ferramenta (ms)", metrica("leis_duracao_ms"), "ALIGN_DELTA",
            "REDUCE_PERCENTILE_95", ["metric.label.ferramenta"], periodo="300s"),
    grafico("Espera na fila p95 — buscas pesadas (ms)", metrica("leis_espera_ms"), "ALIGN_DELTA",
            "REDUCE_PERCENTILE_95", ["metric.label.ferramenta"], periodo="300s"),
    grafico("CPU da VM", f'metric.type="compute.googleapis.com/instance/cpu/utilization" AND {VM}',
            "ALIGN_MEAN"),
    grafico("Memória usada da VM (%)",
            f'metric.type="agent.googleapis.com/memory/percent_used" AND {VM} AND metric.label.state="used"',
            "ALIGN_MEAN"),
    grafico("Disco usado (%)",
            f'metric.type="agent.googleapis.com/disk/percent_used" AND {VM} AND metric.label.state="used"',
            "ALIGN_MEAN"),
    grafico("Disponibilidade de /saude (fração de verificações OK)",
            'metric.type="monitoring.googleapis.com/uptime_check/check_passed" AND '
            f'resource.type="uptime_url" AND metric.label.check_id="{check_id}"',
            "ALIGN_FRACTION_TRUE", "REDUCE_MEAN", [], periodo="300s"),
]
NOME_PAINEL = "leis-mcp"
if not any(d.get("displayName") == NOME_PAINEL for d in listar(DASH, "dashboards")):
    api("POST", DASH, {
        "displayName": NOME_PAINEL,
        "mosaicLayout": {"columns": 12, "tiles": [
            {"xPos": (i % 2) * 6, "yPos": (i // 2) * 4, "width": 6, "height": 4, "widget": g}
            for i, g in enumerate(graficos)
        ]},
    })
    print("Painel criado: leis-mcp")

print(f"""
Monitoramento pronto.
  Painel:  https://console.cloud.google.com/monitoring/dashboards?project={P}
  Alertas: https://console.cloud.google.com/monitoring/alerting?project={P}
  Logs:    https://console.cloud.google.com/logs/query;query=logName%3D%22projects%2F{P}%2Flogs%2Fdocker_leis%22?project={P}
As métricas de chamadas só começam a aparecer depois da primeira chamada feita após este script.""")
PY
