# Ferramentas, recursos e prompts

Referência do que o servidor MCP da Aurora expõe a um assistente de IA: 15
ferramentas, 5 recursos e 6 prompts. Serve para quem quer entender o que a IA
consegue consultar, integrar outro cliente ou contribuir com o código.

O texto que a IA recebe sobre cada ferramenta está em
[`src/leis_mcp/descricoes.py`](../src/leis_mcp/descricoes.py).

## Convenções

- **Somente leitura.** Nenhuma ferramenta altera dados.
- **IDs internos.** `id_parlamentar` vem de `buscar_id_parlamentar`; os IDs de
  proposição vêm de `buscar_proposicao` ou das buscas.
- **Casa.** `casa` aceita `Câmara` ou `Senado`; omitido, vale para as duas.
- **Datas.** `data_inicio` e `data_fim` filtram pela data de apresentação, no
  formato `AAAA-MM-DD`.
- **Paginação.** Nenhum resultado é cortado. Quando a resposta não cabe em uma
  página, ela começa pelo campo `paginacao`, com `proxima_pagina`; basta repetir
  a chamada com `pagina`. Respostas paginadas trazem também um resumo de todos
  os itens.
- **Erros.** Entrada inválida devolve um código no topo da resposta (por
  exemplo `NOME_VAZIO`), nunca uma lista vazia que pareça "sem resultados".

Algumas ferramentas têm marcações:

| Marcação | Significado |
| :--- | :--- |
| pesada | faz busca vetorial ou varredura; disputa um número limitado de vagas simultâneas |
| livre | não consome a cota diária do usuário |
| admin | só aparece e só executa para administradores |

## Guia

### `guia_de_pesquisa` · livre

Sem parâmetros. Devolve as regras de interpretação, os fluxos de pesquisa e o
alcance da base medido no banco. A IA é instruída a chamá-la antes de qualquer
outra ferramenta.

## Parlamentares

### `buscar_id_parlamentar`

Encontra parlamentares pelo nome ou por parte dele, sem diferenciar acento.

- **Parâmetros:** `nome`, `casa?`, `pagina?`
- **Devolve:** `total` e `parlamentares`, cada um com `id_parlamentar`, nome,
  partido atual, UF e casa. Nomes idênticos ao pedido vêm primeiro.
- **Erros:** `NOME_VAZIO`

### `consultar_historico`

Lista as proposições em que o parlamentar atuou, com totais por papel.

- **Parâmetros:** `id_parlamentar`, `papel?` (`autor`, `relator` ou `votante`),
  `tipo_voto?` (`Sim`, `Não`, `Abstenção`, `Obstrução`…), `ano?`, `pagina?`
- **Devolve:** IDs das proposições por papel. Como votante, conta só votos com
  posição; ausências registradas não entram.
- **Erros:** `PAPEL_INVALIDO`, `TIPO_VOTO_INVALIDO` (com os valores aceitos)

Relatoria existe só para a Câmara.

## Proposições

### `buscar_proposicao`

Localiza uma proposição pela identificação oficial, como PL 2338/2023.

- **Parâmetros:** `sigla_tipo`, `numero`, `ano?`, `casa?`, `pagina?`
- **Devolve:** ID, casa, ementa, autores na ordem de assinatura,
  `url_inteiro_teor`, `votacoes_nominais` e `trechos_de_inteiro_teor_indexados`.
  Quando existem, também `autoria_de_iniciativa`, `mesma_materia_na_outra_casa`
  e `outras_identificacoes` (números anteriores e da outra casa).
- **Erros:** `SIGLA_VAZIA`, `NAO_ENCONTRADA`

Se não há registro com o número informado, a busca tenta as identificações
anteriores (o PL 3729/2004 hoje é o PL 2159/2021) e avisa. `NAO_ENCONTRADA`
significa que a proposição não está na base, não que ela não existe.

### `obter_detalhes_proposicoes`

Os mesmos campos de `buscar_proposicao` para uma lista de IDs.

- **Parâmetros:** `lista_ids`, `pagina?`
- **Devolve:** os detalhes de cada proposição e `ids_nao_encontrados`.

## Busca

### `busca_semantica_proposicoes` · pesada

Busca proposições por tema. Combina busca por significado e por palavra-chave
nas ementas e anexa trechos do texto integral aos primeiros resultados.

- **Parâmetros:** `termos` (lista de 4 a 6 termos), `somente_votadas?`, `casa?`,
  `data_inicio?`, `data_fim?`, `lista_ids?`, `top_k?` (padrão 40, máximo 500),
  `pagina?`
- **Erros:** `SEM_TERMOS`

| Campo | Conteúdo |
| :--- | :--- |
| `avisos` | alertas sobre a confiança geral dos resultados e sobre a paginação |
| `resultados` | ementa, autores, pontuações, `confianca`, `motivo_da_confianca` e trechos do texto integral |
| `indice_completo` | em respostas paginadas: posição, ID e confiança de todos os resultados |
| `apendice_sem_votacao_nominal` | com `somente_votadas`: até 5 proposições relevantes que não tiveram votação nominal |

Com `somente_votadas=true`, a busca se restringe às proposições com voto
nominal. É o modo indicado quando a pergunta é sobre como alguém votou.

Cada resultado traz um nível de `confianca`:

| Valor | Quando |
| :--- | :--- |
| `alta` | o resultado se destaca semanticamente, ou o termo aparece no texto com destaque ao menos moderado |
| `moderada` | destaque semântico moderado, sem o termo no texto |
| `baixa` | o termo não aparece no texto e o destaque semântico é baixo |
| `indeterminada` | os sinais se contradizem: o termo aparece no texto, mas a medida semântica indica que o documento não trata do tema |

O caso `indeterminada` é típico de menção de passagem ou citação de outra lei.
O campo `motivo_da_confianca` explica de onde o nível veio, e o resultado deve
ser conferido pelo trecho antes de ser usado como fonte.

### `busca_inteiro_teor` · pesada

Busca trechos literais no texto integral das proposições.

- **Parâmetros:** `termo`, `lista_ids?`, `casa?`, `data_inicio?`, `data_fim?`,
  `top_k?` (padrão 8, máximo 100), `pagina?`
- **Devolve:** os trechos, com o texto do dispositivo e a ementa da proposição.
  Sem `lista_ids`, busca no acervo inteiro.
- **Erros:** `SEM_TERMO`

**Leitura sob demanda.** Se uma proposição de `lista_ids` ainda não tem o texto
integral indexado, o servidor baixa o documento oficial da Câmara ou do Senado
na hora e busca nele. Nada é gravado no banco. Nesses casos:

- os trechos vêm marcados com `origem = documento_baixado_agora`;
- `proposicoes_lidas_sob_demanda` informa a URL lida e se o texto veio
  incompleto (`texto_incompleto`, comum em PDF digitalizado como imagem);
- `falhas_sob_demanda` traz o motivo de cada documento que não pôde ser lido.

Quando o texto vem incompleto ou a leitura falha, não encontrar trechos não
significa que a proposição não trate do tema.

## Votos

### `consultar_votos`

Mostra como um parlamentar votou em cada proposição de uma lista, ou o
histórico completo dele.

- **Parâmetros:** `id_parlamentar`, `lista_ids?`, `pagina?`
- **Devolve:** com lista, um status por proposição e `contagem_por_status`. Sem
  lista, o histórico do voto mais recente ao mais antigo, com
  `placar_sobre_o_total` e `periodo_completo`.
- **Erros:** `PARLAMENTAR_NAO_ENCONTRADO`, `LISTA_IDS_SEM_ID_VALIDO`

| Status | Significado |
| :--- | :--- |
| `VOTOU` | há um voto registrado na proposição |
| `VOTOU_EM_VARIAS_VOTACOES` | votou em mais de uma votação; a resposta lista cada uma, com a descrição oficial, sem eleger um voto "principal" |
| `AUSENTE` | ausência registrada; no Senado vem com `motivo_oficial` (licença, missão, não compareceu) |
| `OUTRA_CASA` | a proposição só teve votações na outra casa |
| `FORA_DE_EXERCICIO` | não exercia o mandato na data da votação |
| `SEM_VOTACAO_NOMINAL` | não há voto individual registrado para a proposição |
| `FORA_DA_BASE` | a proposição não está na base ou é anterior ao período coberto pelas votações |

Uma proposição costuma passar por várias votações (urgência, texto principal,
emendas, destaques). O servidor entrega a descrição oficial de cada uma e não
decide qual delas é a "de mérito".

### `votos_por_tema` · pesada

Como um parlamentar votou nas proposições de um tema, em uma única chamada:
busca o tema entre as proposições votadas e consulta o voto em cada uma.

- **Parâmetros:** `id_parlamentar`, `termos`, `casa?`, `data_inicio?`,
  `data_fim?`, `top_k?`, `pagina?`
- **Devolve:** as proposições separadas em `votou` e `ausente_ou_sem_voto`, e
  `proposicoes_do_tema_sem_votacao_nominal`.
- **Erros:** `PARLAMENTAR_NAO_ENCONTRADO`

Sem `casa`, a busca se limita à casa do parlamentar.

### `placar_por_votacao`

Placar de cada votação nominal das proposições, com a descrição oficial. Os
placares nunca são somados entre votações diferentes.

- **Parâmetros:** `lista_ids`, `partido?`, `uf?`, `listar_parlamentares?`,
  `pagina?`
- **Devolve:** `votacoes`, com os números de cada votação, `sim_superou_nao` e
  `quorum_qualificado` (PEC e PLP). Com `listar_parlamentares`, os nomes vêm em
  `nomes_por_voto`.
- **Erros:** `PARTIDO_SEM_VOTOS`

`partido` e `uf` filtram pelos valores **na data do voto**. O filtro por partido
reúne as siglas da mesma legenda: grafias diferentes entre as casas (PODE e
PODEMOS) e renomeações (PR e PL, PRB e REPUBLICANOS). As siglas consideradas
vêm em `filtros.siglas_consideradas`. Fusões e incorporações não são somadas; a
resposta apenas avisa.

### `posicao_consolidada`

O voto de vários parlamentares em cada votação de várias proposições, lado a
lado. Serve para perguntas de alinhamento sobre um tema.

- **Parâmetros:** `ids_parlamentares`, `lista_ids`, `pagina?`
- **Devolve:** os votos por parlamentar e os campos
  `votou_igual_em_todas_as_votacoes` e `votou_de_formas_diferentes_no_conjunto`.
- **Erros:** `LISTAS_VAZIAS`

As proposições em que a pessoa não tem voto saem em dois campos distintos:

- `proposicoes_com_votacao_em_que_nao_votou`: houve votação na casa dela, no
  mandato dela, e não há voto registrado;
- `proposicoes_sem_voto_por_impedimento`: ela não tinha como votar (votação da
  outra casa, fora do mandato, sem votação nominal ou ausência com motivo
  oficial). Cada item traz `status` e `observacao`.

## Autoria e vetos

### `mapear_autores_por_tema` · pesada

Quem apresentou proposições sobre um tema. Varre todas as ementas pelo radical
informado e devolve os autores com as respectivas proposições e ementas, do
que mais assinou ao que menos assinou.

- **Parâmetros:** `termo` (radical, como `aposta` ou `licenciament`),
  `termo_secundario?`, `confirmar_radical?`, `pagina?`
- **Devolve:** `parlamentares`, `total_parlamentares`, `com_votacao_nominal`
  (as votações do tema) e, em respostas paginadas, `todos_os_autores`.
- **Erros:** `TERMO_MUITO_CURTO`

A ferramenta não classifica o que cada proposição pede (se restringe ou
incentiva, por exemplo). Ela entrega as ementas, e a leitura fica com a IA.

**Radicais com menos de 8 letras respondem em dois passos.** Um radical curto
pode casar palavras de assuntos diferentes: `apost` alcança "aposta" e "veto
aposto". Por isso a primeira chamada devolve `etapa: PREVIA_DO_RADICAL`, sem
nenhum nome, mostrando em `formas_que_o_radical_casou` as palavras alcançadas e
ementas de exemplo. A partir daí há três caminhos, descritos em
`como_continuar`:

- repetir com `confirmar_radical=true`, se tudo for do mesmo assunto;
- usar um radical mais específico;
- informar `termo_secundario` para separar o assunto (por exemplo,
  `termo=intelig` e `termo_secundario=artificial`).

### `proposicoes_por_autor_institucional`

Proposições apresentadas por uma instituição: Presidência da República,
Câmara dos Deputados, Senado Federal, comissões ou tribunais.

- **Parâmetros:** `autor`, `id_ente?`, `casa_da_fonte?`, `ano?`, `pagina?`
- **Devolve:** os IDs das proposições. Se o nome casar com mais de uma
  instituição, devolve `entes` para escolher por `id_ente` e `casa_da_fonte`.
- **Erros:** `AUTOR_VAZIO`

### `consultar_vetos_presidenciais` · pesada

Vetos de um presidente, com filtro opcional por tema.

- **Parâmetros:** `presidente` (`Lula`, `Bolsonaro`, `Dilma` ou `Temer`),
  `termo?`, `pagina?`
- **Erros:** `PRESIDENTE_NAO_RECONHECIDO`, `FORA_DA_COBERTURA` (mandato anterior
  ao período da base)

Com `termo`, todos os vetos do período são ordenados por relevância e separados
em três grupos, sem descartar nenhum:

| Campo | Conteúdo |
| :--- | :--- |
| `vetos` | relação alta ou moderada com o tema |
| `vetos_de_relacao_indefinida` | o termo aparece no texto, mas a medida semântica não confirma o tema |
| `vetos_sem_relacao_aparente` | sem relação aparente; a lista completa vem só na página 1, e as demais trazem `total_sem_relacao_aparente` |

## Administração

### `executar_consulta_sql` · admin, pesada

Executa uma consulta SQL de leitura diretamente no banco.

- **Parâmetros:** `consulta` (uma única instrução `SELECT` ou `WITH`), `pagina?`
- **Devolve:** todas as linhas, paginadas por tamanho, com `total_de_linhas`.

A conexão é somente leitura, um autorizador bloqueia qualquer escrita,
`PRAGMA` e `ATTACH`, e há limite de tempo de execução (`LEIS_SQL_TIMEOUT_SEG`,
padrão 230 s).

## Recursos

| URI | Conteúdo |
| :--- | :--- |
| `leis://alcance` | cobertura da base medida no banco: período, totais de votações nominais e simbólicas e de proposições |
| `leis://regras` | o mesmo texto de `guia_de_pesquisa` |
| `leis://esquema` | esquema do banco, com as armadilhas conhecidas |
| `leis://proposicao/{id_proposicao}` | detalhes e placares de uma proposição |
| `leis://parlamentar/{id_parlamentar}` | cadastro e totais por papel de um parlamentar |

Os dois recursos por ID contam na cota do usuário como uma chamada.

## Prompts

Modelos de pergunta que o usuário escolhe no cliente e preenche:

| Prompt | Título no menu |
| :--- | :--- |
| `como_votou(parlamentar, tema)` | Como [parlamentar] votou sobre [tema]? |
| `como_votou_partido(partido, tema)` | Os parlamentares do [partido] votaram a favor de [tema]? |
| `quem_se_alinha(tema, posicao)` | Sou [a favor de / contra] [tema]: quem se alinha comigo? |
| `quem_propos(tema)` | Quem apresentou proposições sobre [tema]? |
| `vetos_do_presidente(presidente, tema?)` | Quais vetos [presidente] apresentou sobre [tema]? |
| `assistente_legislativo` | Assistente legislativo: regras de pesquisa |

Onde aparecem:

- **claude.ai e Claude Desktop:** menu **+** da caixa de mensagem → nome do
  conector → prompt.
- **Claude Code:** digite `/` e procure pelo nome. O comando é
  `/mcp__<nome do servidor>__<prompt>`.
- **MCP Inspector:** aba *Prompts*.

## Limitações conhecidas

- **Votação simbólica.** `SEM_VOTACAO_NOMINAL` não distingue a proposição que
  nunca foi votada da que foi aprovada por acordo, sem voto individual. O banco
  registra as votações simbólicas, mas as ferramentas de voto ainda não usam
  essa informação.
- **Relatoria.** Só há dados de relatoria da Câmara.
- **Discursos.** A base guarda os discursos em plenário das duas casas, mas
  ainda não há ferramenta que os consulte.
- **Período.** A cobertura começa em fevereiro de 2018; o alcance exato está em
  `leis://alcance`.
