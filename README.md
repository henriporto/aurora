<p align="center">
  <a href="http://auroravoto.com.br/"><img src="assets/lobo.png" alt="Aurora" width="120"></a>
</p>

<h1 align="center">Aurora</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python_3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/MCP-000000?style=for-the-badge&logo=modelcontextprotocol&logoColor=white" alt="MCP">
  <img src="https://img.shields.io/badge/uv-DE5FE9?style=for-the-badge&logo=uv&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/NumPy-013243?style=for-the-badge&logo=numpy&logoColor=white" alt="NumPy">
  <img src="https://img.shields.io/badge/PyTorch-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white" alt="PyTorch">
  <img src="https://img.shields.io/badge/Hugging_Face-FFD21E?style=for-the-badge&logo=huggingface&logoColor=black" alt="Hugging Face">
  <img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/Caddy-1F88C0?style=for-the-badge&logo=caddy&logoColor=white" alt="Caddy">
  <img src="https://img.shields.io/badge/Google_Cloud-4285F4?style=for-the-badge&logo=googlecloud&logoColor=white" alt="Google Cloud">
</p>

**As eleições estão chegando, e perguntar a uma IA sobre política é um tiro no escuro.**
Ela não sabe como cada parlamentar votou, não leu os projetos de lei e, sem esse
contexto, inventa respostas ou entrega só metade da história.

Este projeto dá à IA acesso aos dados oficiais da Câmara e do Senado: cada voto
nominal, cada proposição com o texto na íntegra, quem apresentou, quem relatou e
o que o presidente vetou. Com isso, dá para perguntar:

- *"O deputado [nome] tem votado a favor das bets?"*
- *"Como a bancada do [partido] votou na reforma tributária?"*
- *"Quais senadores votaram contra o marco temporal?"*
- *"Meu candidato já propôs alguma coisa sobre segurança pública?"*
- *"Quais parlamentares defendem a regulamentação da inteligência artificial?"*
- *"Existe algum projeto que proíba celular nas escolas? O que ele diz?"*
- *"O que o presidente vetou na lei do saneamento básico?"*

A resposta sai dos dados, com a votação, a data e o placar, e não da memória do modelo.

## Como funciona

**O diferencial é a busca (RAG) sobre o conteúdo das leis.** Mais de 740 mil trechos
de inteiro teor estão indexados numa busca híbrida, semântica e por palavra-chave.
Por isso, uma pergunta sobre "apostas esportivas" encontra o projeto que fala em
"loteria de quota fixa", mesmo sem nenhuma palavra em comum. É assim que a IA liga
um tema a proposições e, delas, aos votos de cada parlamentar.

Funciona em qualquer cliente MCP (claude.ai, Claude Desktop, Claude Code, Cursor, VS Code):
basta conectar a URL do servidor.


## Como rodar o servidor localmente

>Pré-requisito: ter [uv](https://docs.astral.sh/uv/#installation) instalado.

Rode o script abaixo para baixar o banco (~2 GB de download, 9,4 GB instalado) direto do Hugging Face,
descomprimir, e salvar em `dados/leis.db`:

```bash
uv run scripts/baixar_banco.py
```

Depois, suba o servidor:

```bash
uv sync
LEIS_AUTH=off uv run leis-mcp
```

## Conectar um cliente

Para que sua IA preferida tenha acesso as ferramentas, conecte dessa forma:
| Cliente | Como |
| :--- | :--- |
| Claude Code | `claude mcp add --transport http leis http://127.0.0.1:8000/mcp` |
| Cursor | `.cursor/mcp.json`: `{"mcpServers": {"leis": {"url": "http://127.0.0.1:8000/mcp"}}}` |
| VS Code | Command Palette → **MCP: Add Server** → HTTP → cole a URL, ou `.vscode/mcp.json`: `{"servers": {"leis": {"type": "http", "url": "http://127.0.0.1:8000/mcp"}}}` |


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

## Mais exemplos de perguntas

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

### Base de dados

A base possui dados de `2018-02-07` a `2026-09-03`.

| Proposições | 429.292 |
| :--- | ---: |
| Votos nominais individuais | 1.303.357 (3.773 votações) |
| Trechos de inteiro teor indexados | 741.519 |
| Parlamentares | 1.630 |
