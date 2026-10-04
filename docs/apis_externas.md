# APIs externas

As fontes de dados e os serviços externos que a Aurora usa: o que cada um
fornece, quais endpoints são consultados e onde cada fonte costuma surpreender.
Serve para quem quer reproduzir a coleta dos dados, contribuir com a ingestão
ou construir algo sobre as mesmas APIs.

Nenhuma fonte legislativa exige chave, cadastro ou cota. As únicas credenciais
do projeto são as do Google, para o login dos usuários e para o deploy.

## Resumo

| Fonte | Endereço | Credencial | Uso |
| :--- | :--- | :--- | :--- |
| Dados Abertos da Câmara (API) | `dadosabertos.camara.leg.br/api/v2` | não | votações, autoria, relatorias, exercício do mandato, discursos |
| Dados Abertos da Câmara (arquivos) | `dadosabertos.camara.leg.br/arquivos` | não | proposições, em CSV anual |
| Dados Abertos do Senado | `legis.senado.leg.br/dadosabertos` | não | matérias, autoria, votações, discursos |
| Documentos de inteiro teor | `camara.leg.br`, `senado.leg.br` | não | texto integral das proposições |
| Google OAuth | `accounts.google.com`, `oauth2.googleapis.com` | cliente OAuth | login dos usuários |
| Google Cloud | `*.googleapis.com` | `gcloud auth` | deploy e monitoramento |
| Hugging Face Hub | `huggingface.co` | não | modelo de embeddings e banco publicado |
| Let's Encrypt | via Caddy | não | certificado HTTPS |

**Em execução, o servidor só faz duas chamadas externas:** o login no Google e
o download de documentos sob demanda (seção 3). Todo o resto acontece na
ingestão dos dados, antes de o servidor subir. O servidor não chama nenhum
modelo de linguagem: quem raciocina é a IA do usuário.

## 1. Dados Abertos da Câmara dos Deputados

Base: `https://dadosabertos.camara.leg.br/api/v2`. REST, JSON, paginada por
`pagina` e `itens` (máximo de 100 por página).

### Endpoints usados

| Endpoint | Fornece |
| :--- | :--- |
| `GET /votacoes` | lista de votações de um período, com `descricao`, `siglaOrgao` e `aprovacao` de todas, inclusive as simbólicas |
| `GET /votacoes/{id}` | `proposicoesAfetadas`, que liga a votação à proposição |
| `GET /votacoes/{id}/votos` | o voto de cada deputado |
| `GET /proposicoes/{id}` | dados de uma proposição e sua situação atual (`statusProposicao`) |
| `GET /proposicoes/{id}/autores` | autores, incluindo instituições |
| `GET /proposicoes/{id}/tramitacoes` | tramitação, de onde sai a relatoria |
| `GET /deputados` | deputados de uma legislatura |
| `GET /deputados/{id}` | cadastro de um deputado |
| `GET /deputados/{id}/historico` | posse, licença, suplência e fim de mandato |
| `GET /deputados/{id}/discursos` | discursos, já com a transcrição |

### Arquivos em lote

`https://dadosabertos.camara.leg.br/arquivos/` publica dumps anuais em CSV, bem
mais baratos do que consultar a API proposição por proposição:

- `proposicoes/csv/proposicoes-{ano}.csv`: as proposições do ano, com a
  situação atual nas colunas `ultimoStatus_*`;
- `proposicoesAutores/csv/proposicoesAutores-{ano}.csv`: os autores.

### Pontos de atenção

- **Intervalos longos devolvem HTTP 400.** `/votacoes` rejeita janelas grandes;
  a coleta é feita mês a mês.
- **Não existe endpoint de relator.** `/proposicoes/{id}/relatores` responde
  405. A relatoria só aparece na tramitação, em `codTipoTramitacao = 320`
  ("Designação de Relator(a)"), o que exige uma requisição por proposição.
- **`proposicaoObjeto` quase sempre vem nulo** na lista de votações. A ligação
  confiável com a proposição está em `proposicoesAfetadas`, no detalhe.
- **A Câmara só publica linha para quem votou.** Quem não votou não aparece, e
  por isso é preciso o histórico de exercício para distinguir ausência de quem
  nem era deputado na data.
- **Comissões votam de forma simbólica.** A coleta de votos considera só o
  plenário (`siglaOrgao = PLEN`).
- **Alguns discursos devolvem HTTP 500 permanentemente** e derrubam a página
  inteira de 100 itens. A coleta subdivide a faixa até isolar e pular só o
  registro quebrado.

## 2. Dados Abertos do Senado Federal

Base: `https://legis.senado.leg.br/dadosabertos`. Responde XML por padrão; para
JSON, use o sufixo `.json` na rota ou `Accept: application/json`. Não é
paginada: cada consulta devolve o conjunto inteiro.

### Endpoints usados

| Endpoint | Fornece |
| :--- | :--- |
| `GET /materia/pesquisa/lista?ano={ano}&sigla={sigla}` | matérias de um ano e tipo |
| `GET /materia/{codigo}` | dados de uma matéria |
| `GET /processo?sigla={sigla}&ano={ano}` | processos por tipo e ano |
| `GET /processo/{id}` | autoria estruturada, `outrosNumeros` e a situação atual |
| `GET /processo/documento?codigoMateria={codigo}` | URLs dos documentos da matéria |
| `GET /materia/textos/{codigo}` | URLs dos documentos (API antiga, ainda responde) |
| `GET /plenario/lista/votacao/{AAAAMMDD}/{AAAAMMDD}` | votações do período, já com os votos |
| `GET /senador/lista/legislatura/{de}/{ate}` | senadores de um intervalo de legislaturas |
| `GET /senador/{codigo}` | cadastro de um senador |
| `GET /senador/{codigo}/discursos?dataInicio=&dataFim=` | metadados dos pronunciamentos |
| `GET /discurso/texto-integral/{codigo}` | texto do pronunciamento |

### Pontos de atenção

- **Um endpoint traz a votação inteira.** `/plenario/lista/votacao/...` devolve
  a votação e os votos juntos: a coleta do Senado custa dezenas de requisições,
  contra milhares na Câmara.
- **O Senado declara as ausências** com códigos próprios (`NCom`, `AP`, `LA`,
  `MIS`…); a Câmara não. A base guarda o voto normalizado e o código original.
- **Votação secreta não revela posição individual** e não é registrada como
  voto.
- **Objeto único no lugar de lista.** Com um só resultado, a API devolve um
  objeto onde normalmente viria uma lista.
- **`/senador/{id}/discursos` sem período devolve só os últimos 30 dias.** A
  coleta é feita ano a ano.
- **`texto-integral` é texto puro** e responde 406 se for pedido em JSON. É
  comum faltar (404) em pronunciamentos recentes.
- **Respostas longas de `/processo` às vezes vêm cortadas** (JSON truncado); é
  preciso tentar de novo.
- **A URL de inteiro teor do Senado aponta para metadados.**
  `dadosabertos/materia/{id}` é um XML descritivo, não o documento. A URL real
  precisa ser resolvida em `/processo/documento`.

Os identificadores do Senado são usados diretamente na base: `CodigoMateria` é
o ID da proposição e `CodigoParlamentar`, o do parlamentar.

## 3. Documentos de inteiro teor

São downloads de arquivo, não chamadas de API.

- **Câmara:** `https://www.camara.leg.br/proposicoesWeb/prop_mostrarintegra?codteor=...`.
  Pode devolver PDF ou DOCX; os dois formatos são lidos.
- **Senado:** a URL do documento é resolvida pela API (seção 2). Como uma
  matéria tem vários documentos, o texto principal tem prioridade sobre
  avulsos, razões de veto e requerimentos.

Há dois usos:

- **Na ingestão**, os documentos são baixados em lote, segmentados e
  vetorizados para a busca no texto integral.
- **No servidor, sob demanda**, quando a proposição pedida ainda não tem o
  texto indexado. O documento é baixado, segmentado e pesquisado em memória;
  nada é gravado no banco.

A leitura sob demanda tem limites:

| Limite | Valor | Configuração |
| :--- | :--- | :--- |
| Hosts aceitos | `camara.leg.br` e `senado.leg.br`, inclusive em redirecionamentos | fixo |
| Tamanho do documento | 30 MB | `LEIS_SOB_DEMANDA_MAX_MB` |
| Tempo por download | 20 s | `LEIS_SOB_DEMANDA_TIMEOUT_SEG` |
| Proposições por chamada | 5 | `LEIS_SOB_DEMANDA_MAX_PROPOSICOES` |
| Trechos por documento | 3.000 | fixo |

Quando a leitura falha, o motivo é devolvido à IA; uma falha nunca é
apresentada como "o texto não trata do tema". O recurso pode ser desligado com
`LEIS_SOB_DEMANDA=0`.

## 4. Google OAuth

Em produção, o login usa o `GoogleProvider` do FastMCP, que funciona como um
proxy OAuth: o Google não oferece o registro dinâmico de clientes que o MCP
espera, então o servidor expõe `/register`, `/authorize`, `/token` e os
metadados em `/.well-known/` e, por trás, usa um cliente OAuth do Google Cloud.

- **Escopos:** `openid`, `email` e `profile`. O servidor recebe só o nome e o
  e-mail da conta.
- **Configuração:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` e
  `LEIS_JWT_CHAVE`.
- **Redirecionamento:** `https://<domínio>/auth/callback`. O Google exige um
  domínio público; endereço IP não é aceito.

O login só identifica a pessoa. O que ela pode fazer (papel e cota diária) é
decidido pelo próprio servidor.

## 5. Google Cloud

Os scripts de `deploy/gcp/` usam as APIs de Compute Engine, Cloud Storage, IAM,
Cloud Logging e Cloud Monitoring, sempre com o login do `gcloud`. Nenhuma
chave de conta de serviço é gerada: o deploy pelo GitHub Actions usa Workload
Identity Federation.

O certificado HTTPS é obtido e renovado pelo Caddy na Let's Encrypt, sem
configuração além do domínio.

## 6. Hugging Face Hub e pacotes

- **Modelo de embeddings:** `Qwen/Qwen3-Embedding-0.6B` (1024 dimensões),
  baixado na ingestão e durante o build da imagem Docker. Em produção o
  contêiner roda com `HF_HUB_OFFLINE=1`, então nada é buscado em tempo de
  execução. Trocar o modelo exige gerar todos os vetores de novo.
- **Banco de dados:** o `leis.db` é publicado comprimido como dataset no Hub e
  baixado por `scripts/baixar_banco.py`.
- **Dependências:** PyPI e o índice de CPU do PyTorch
  (`download.pytorch.org/whl/cpu`).

## Boas práticas na coleta

- **Nenhuma fonte legislativa publica limite de taxa.** Ainda assim, a coleta
  usa concorrência baixa (de 2 a 6 conexões) e espera crescente entre
  tentativas em respostas 429 e 5xx.
- **Coletas longas são retomáveis.** A escrita é atômica e cada unidade
  concluída é marcada, então dá para interromper e continuar depois.
- **400 e 404 são respostas legítimas,** não falhas de rede: não se insiste
  nelas.
- **As APIs mudam sem aviso.** Endpoints descontinuados continuam respondendo,
  campos somem e alguns registros ficam quebrados para sempre. Ao atualizar a
  base, vale comparar as contagens antes e depois.
