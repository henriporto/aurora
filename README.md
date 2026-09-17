# leis-mcp

Servidor **MCP** (Model Context Protocol) com dados oficiais do processo legislativo
federal brasileiro: votos nominais, proposições, autoria, relatorias, vetos e
inteiro teor. Qualquer cliente MCP (claude.ai, Claude Desktop, Claude Code, Cursor)
se conecta por uma URL e ganha ferramentas para responder, com dados, perguntas
como *"como a deputada X votou sobre Y?"* ou *"quem defende Z?"*.

Na prática, o servidor distingue **não votou** de **não houve votação nominal**,
separa a votação do **texto** das de rito, emenda e destaque, e nunca poda
resultados: o que não cabe numa resposta vem em páginas, acompanhado de um
resumo completo.

| Base (medida) | |
| :--- | ---: |
| Proposições | 429.292 |
| Votos nominais individuais | 1.303.357 (3.773 votações, 2018-02-07 a 2026-09-03) |
| Trechos de inteiro teor indexados | 741.519 |
| Parlamentares | 1.630 |

## Stack

| Camada | Tecnologia |
| :--- | :--- |
| Protocolo | MCP, Streamable HTTP sem estado (spec 2026-07-28) · FastMCP 4 |
| Autenticação | OAuth com login Google (`GoogleProvider` do FastMCP) |
| Dados | SQLite somente leitura (`leis.db`, 9,4 GB): relacional + FTS5 + vetores |
| Busca | híbrida densa + BM25 fundida por RRF (k=60) · Qwen3-Embedding-0.6B em CPU · NumPy |
| Usuários e uso | SQLite (`usuarios.db`): papéis, cotas diárias, registro de cada chamada |
| Execução | Python 3.12 · `uv` · Docker Compose com Caddy (HTTPS automático) |
| Nuvem | GCP Compute Engine `e2-standard-2` (2 vCPU, 8 GB), scripts `gcloud` |

## Tools

`guia_de_pesquisa` (regras e fluxos, chamada primeiro) · `buscar_id_parlamentar` ·
`consultar_historico` · `buscar_proposicao` · `obter_detalhes_proposicoes` ·
`busca_semantica_proposicoes` · `busca_inteiro_teor` · `consultar_votos` ·
`votos_por_tema` · `placar_por_votacao` · `posicao_consolidada` ·
`mapear_autores_por_tema` · `proposicoes_por_autor_institucional` · `consultar_vetos_presidenciais` ·
`executar_consulta_sql` (só admin).

Detalhes em [`docs/ferramentas.md`](docs/ferramentas.md).

## Perguntas de exemplo

| Tipo | Exemplo |
| :--- | :--- |
| Voto de uma pessoa | "Como a deputada [nome] votou em proposições sobre apostas esportivas?" |
| Voto de um partido | "Como os deputados do [partido] votaram na reforma tributária?" |
| Quem votou de cada jeito | "Quais senadores votaram contra a reforma tributária?" |
| Quem propôs | "Quem apresentou proposições sobre saúde da mulher?" · "Quais propostas [nome] apresentou em 2025?" |
| Alinhamento | "Sou a favor de regulamentar a inteligência artificial; quais parlamentares se alinham comigo?" |
| Texto da proposição | "O que diz o artigo 3º do PL 2338/2023?" · "Há proposições que tratam de reconhecimento facial em escolas?" |
| Relatoria | "De quais proposições [nome] foi relator?" |
| Vetos | "Quais vetos o presidente apresentou sobre saneamento básico?" |

Perguntas que nomeiam a pessoa, o partido ou a proposição e dizem a casa e o
período, quando importam, rendem respostas mais precisas. O modelo também sabe
responder "como formulo uma pergunta?": o guia de pesquisa traz estes exemplos.

### Prompts prontos

Os modelos de pergunta acima também existem como prompts MCP, com campos para
preencher: `como_votou`, `como_votou_partido`, `quem_se_alinha`, `quem_propos`,
`vetos_do_presidente` e `assistente_legislativo` (anexa as regras).

| Cliente | Onde ficam |
| :--- | :--- |
| Claude Code | digite `/` → `/mcp__leis__como_votou` (o meio é o nome dado em `claude mcp add`) |
| claude.ai / Claude Desktop | menu **+** da caixa de mensagem → conector → prompt |
| MCP Inspector | aba *Prompts* |

## Como rodar o servidor localmente?

Pré-requisito: ter [uv](https://docs.astral.sh/uv/#installation) instalado.

Baixe o banco (~2 GB de download, 9,4 GB instalado). O script
[`scripts/baixar_banco.py`](scripts/baixar_banco.py) baixa do Hugging Face,
descomprime, confere o SHA-256 e salva em `dados/leis.db`:

```bash
uv run scripts/baixar_banco.py
```

Depois, suba o servidor:

```bash
uv sync
LEIS_AUTH=off uv run leis-mcp          # usa dados/leis.db
```

Com a mesma imagem de produção (Docker):

```bash
cd deploy
LEIS_BANCO_DIR=$PWD/../dados \
  docker compose -f compose.yaml -f compose.dev.yaml up --build leis-mcp
```

Sem login, o usuário local é administrador. O servidor se recusa a subir sem
login fora de `127.0.0.1`.

## Conectar um cliente

| Cliente | Como |
| :--- | :--- |
| Claude Code | `claude mcp add --transport http leis http://127.0.0.1:8000/mcp` |
| Cursor | `.cursor/mcp.json`: `{"mcpServers": {"leis": {"url": "http://127.0.0.1:8000/mcp"}}}` |
| claude.ai / Claude Desktop | só o servidor remoto: *Personalizar → Conectores → "+"* → `https://SEU-DOMINIO/mcp` → Entrar agora |
| MCP Inspector | `npx @modelcontextprotocol/inspector` → URL do servidor |

## Publicar no GCP

```bash
cp deploy/gcp/config.exemplo.sh deploy/gcp/config.sh   # projeto, região, bucket
cp deploy/exemplo.env deploy/.env                       # domínio, Google OAuth, admins
bash deploy/gcp/01_criar_infra.sh    # IP, firewall, VM, bucket
# aponte o DNS do domínio para o IP exibido
bash deploy/gcp/02_enviar_banco.sh   # prepara, envia e troca o leis.db
bash deploy/gcp/03_publicar_app.sh   # código + docker compose up (publica o último commit)
```

Passo a passo, custos e o cliente OAuth do Google:
[`docs/deploy_gcp.md`](docs/deploy_gcp.md) e [`docs/autenticacao.md`](docs/autenticacao.md).


## Administrar

```bash
# na VM: cd /srv/leis/app/deploy && docker compose exec leis-mcp leis-admin ...
leis-admin usuarios listar
leis-admin usuarios aprovar fulano@gmail.com
leis-admin usuarios cota fulano@gmail.com 100
leis-admin relatorio --desde 2026-10-01
leis-admin exportar chamadas.csv --anonimizar
```
