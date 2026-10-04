"""
Descrições das ferramentas, como o modelo do cliente as lê.

São o único texto do servidor que chega com certeza ao modelo em todos os
clientes: cada uma carrega as regras críticas da própria ferramenta, e a de
`guia_de_pesquisa` manda chamá-la primeiro. O Claude Code corta cada descrição
em ~2 KB, por isso o esquema do banco fica no recurso `leis://esquema` e no guia.
"""

LIMITE_CARACTERES = 2000

GUIA_DE_PESQUISA = (
    "LEIA PRIMEIRO. Chame esta ferramenta UMA vez no início de toda conversa sobre o Congresso, "
    "parlamentares, proposições, votações ou vetos, ANTES de qualquer outra ferramenta deste "
    "servidor. Ela devolve as regras obrigatórias de interpretação e os fluxos de pesquisa: como "
    "verificar se alguém votou a favor ou contra algo, como tratar partido e bancada, como "
    "descobrir quem defende um tema, como ler as várias votações de uma proposição, quando buscar "
    "no inteiro teor, como decompor a pergunta em termos de busca, como manter neutralidade e como "
    "cobrir lacunas com busca na web marcando o que veio dela. "
    "Também traz o alcance da base medido no banco. Sem essas regras, dados corretos são "
    "facilmente interpretados de forma errada (ex.: ausência de voto lida como voto contra). "
    "Não consome cota."
)

BUSCAR_ID_PARLAMENTAR = (
    "Busca parlamentares (deputados e senadores) pelo nome ou fragmento de nome. Retorna "
    "`parlamentares` (candidatos com 'id_parlamentar', 'nome', 'partido' (atual), 'uf' e 'casa'), "
    "`total` e `paginacao`; cada palavra informada precisa ser o início de uma palavra do nome.\n"
    "Use esta ferramenta como primeiro passo indispensável sempre que precisar consultar a atuação, "
    "relatorias, votos ou projetos de um parlamentar cujo ID numérico exato você não possua. Tolera "
    "acentos ('Tábata' encontra 'Tabata'). Se houver mais de um candidato real, peça ao usuário para "
    "escolher. Instituições (Presidência, Câmara dos Deputados, comissões) não estão aqui: use "
    "`proposicoes_por_autor_institucional`."
)

CONSULTAR_HISTORICO = (
    "Consulta o histórico de atuação de um parlamentar retornando os IDs das proposições nas quais "
    "ele atuou. Permite filtrar por papel ('autor', 'relator' — só Câmara — ou 'votante'), tipo de "
    "voto e ano.\n"
    "Ideal para coletar em lote os IDs das proposições relacionadas ao parlamentar para posterior "
    "cruzamento (`obter_detalhes_proposicoes`) ou busca semântica refinada (`lista_ids`). Para "
    "autoria de instituições (Presidência, Câmara dos Deputados, comissões), use "
    "`proposicoes_por_autor_institucional`.\n"
    "ATENÇÃO: devolve apenas IDs e NÃO distingue 'não votou' de 'não houve votação nominal'. Para "
    "responder se um parlamentar apoiou ou rejeitou algo, use `consultar_votos`. Resultado paginado."
)

AUTOR_INSTITUCIONAL = (
    "IDs das proposições de autoria de uma INSTITUIÇÃO: Presidência da República / Poder Executivo, "
    "Câmara dos Deputados (matérias aprovadas na Câmara e enviadas ao Senado), Senado Federal, "
    "comissões, tribunais. Passe o nome ou parte dele; se casar com mais de uma instituição, a resposta "
    "lista `entes` para escolher por `id_ente` e `casa_da_fonte` (a mesma instituição aparece uma vez "
    "por casa). Filtro opcional por `ano`.\n"
    "Use os IDs em `lista_ids` de `busca_semantica_proposicoes` para filtrar por tema, ou em "
    "`obter_detalhes_proposicoes`. Para autoria de parlamentares, use `consultar_historico`. "
    "Resultado paginado."
)

BUSCAR_PROPOSICAO = (
    "Localiza proposições pela identificação oficial: sigla (PL, PEC, PLP, MPV, PDL, REQ, VET...), "
    "número e, de preferência, ano. Use SEMPRE que o usuário citar uma proposição pelo nome (ex.: 'PL "
    "4637/2025') — é exata e barata, e a busca semântica não encontra identificadores.\n"
    "Devolve o ID interno numérico (necessário para as demais ferramentas), casa, ementa, autores, "
    "link do inteiro teor, número de votações nominais e de trechos de inteiro teor indexados. A "
    "mesma proposição pode existir com IDs diferentes na Câmara e no Senado (`mesma_materia_na_outra_casa`); "
    "normalmente só uma tem votos — diga ao usuário de qual casa é o dado. Acha também por número "
    "anterior (a Câmara renumera projetos: `outras_identificacoes`)."
)

OBTER_DETALHES = (
    "Retorna os detalhes estruturados completos (tipo, número, ano, ementa, data, casa, autores, link "
    "do inteiro teor, votações nominais e trechos de inteiro teor indexados; quando houver, "
    "`autoria_de_iniciativa`, `mesma_materia_na_outra_casa` e `outras_identificacoes`) a partir de uma "
    "lista de IDs de proposições.\n"
    "Use para exibir informações limpas e oficiais sobre proposições específicas depois de descobrir "
    "seus IDs por `buscar_proposicao`, `consultar_historico` ou busca. Sem limite de IDs; resultado "
    "paginado."
)

BUSCA_SEMANTICA = (
    "Realiza busca híbrida (vetorial via embeddings + lexical via FTS5, fundidas por RRF) nas EMENTAS "
    "das proposições legislativas, anexando trechos do inteiro teor às mais relevantes.\n"
    "Passe em `termos` uma LISTA de 4 a 6 termos curtos e temáticos numa única chamada (ex.: "
    "['saneamento básico', 'tratamento de esgoto', 'abastecimento de água', 'marco do saneamento']); "
    "prefira termos de 2+ palavras. Filtros opcionais: 'data_inicio', 'data_fim' (só se o usuário citou "
    "período), 'casa' ('Câmara' ou 'Senado') e 'lista_ids' (IDs numéricos).\n"
    "FILTRO 'somente_votadas': PRIORIZA as proposições que TÊM votação nominal registrada. USE-O SEMPRE "
    "que a pergunta for sobre COMO alguém votou: apenas 0,3% das proposições têm voto nominal, e sem "
    "este filtro a busca devolve projetos que nunca foram votados. Com ele vem também "
    "`apendice_sem_votacao_nominal`: até 5 proposições relevantes SEM voto nominal de ninguém — servem "
    "para dizer ao usuário que existem, nunca para afirmar posicionamento.\n"
    "Cada resultado traz `confianca` (alta/moderada/indeterminada/baixa), `motivo_da_confianca` e "
    "`casamento_lexical`. `indeterminada` = os termos aparecem no texto mas a medida semântica "
    "discorda: leia o trecho antes de usar. Leia `avisos`: itens de "
    "confiança baixa podem não ser do tema. Resultado "
    "paginado: se houver `proxima_pagina`, os demais itens não vieram. Só busca nas ementas; para "
    "artigos e texto detalhado use `busca_inteiro_teor`."
)

BUSCA_INTEIRO_TEOR = (
    "Realiza busca híbrida de alta precisão no TEXTO COMPLETO (inteiro teor — artigos, parágrafos, "
    "incisos) das proposições e devolve o texto literal de cada dispositivo, com a ementa da "
    "proposição.\n"
    "Use para perguntas detalhadas sobre o texto (ex.: 'o que diz o artigo 3', 'quais as sanções no "
    "texto', 'o que fala sobre a profissão X') e sempre que as ementas forem vagas ou a busca de "
    "ementas vier com confiança baixa. Filtros opcionais: 'lista_ids' (apenas IDs numéricos internos, "
    "ex.: [12345], nunca strings como 'PL 123/2025' — obtenha o ID com `buscar_proposicao`), 'casa', "
    "'data_inicio' e 'data_fim'. Sem 'lista_ids', varre o acervo inteiro. Proposições de 'lista_ids' "
    "sem inteiro teor indexado são lidas na hora do documento oficial (trechos com "
    "`origem = documento_baixado_agora`); as que não puderem ser lidas vêm em `falhas_sob_demanda`: "
    "ausência de trecho nelas não é ausência de tema. Resultado paginado."
)

CONSULTAR_VOTOS = (
    "Verifica COMO um parlamentar específico votou em uma lista de proposições, retornando um "
    "diagnóstico explícito para cada uma. Use-a (e não `consultar_historico`) sempre que a pergunta for "
    "'o deputado/senador X votou a favor ou contra Y?'.\n"
    "Uma proposição tem VÁRIAS votações nominais (urgência, adiamento, o texto, emendas, destaques). "
    "Quando ele votou em mais de uma, o item NÃO tem campo 'voto': tem 'status': "
    "'VOTOU_EM_VARIAS_VOTACOES' e a lista 'votacoes', cada uma com seu 'voto' e sua 'votacao' "
    "(descrição oficial). Relate votação por votação; só votação sobre o TEXTO autoriza dizer a favor "
    "ou contra a proposição.\n"
    "Status: 'VOTOU' (voto registrado); 'AUSENTE' (houve votação nominal na casa dele e ele não registrou "
    "voto; no Senado, com o motivo oficial); 'OUTRA_CASA' (as votações são da outra casa); "
    "'FORA_DE_EXERCICIO' (não exercia o mandato nas datas das votações); "
    "'SEM_VOTACAO_NOMINAL' (ninguém tem voto individual, votação simbólica); 'FORA_DA_BASE' (sem dados "
    "para a casa ou período).\n"
    "REGRA CRÍTICA: 'AUSENTE', 'OUTRA_CASA', 'FORA_DE_EXERCICIO', 'SEM_VOTACAO_NOMINAL' e 'FORA_DA_BASE' são AUSÊNCIA DE DADO e NUNCA "
    "devem ser apresentados como posicionamento contrário, apoio ou omissão política. Sem `lista_ids`, "
    "devolve o histórico completo de votos, paginado."
)

VOTOS_POR_TEMA = (
    "Como UM parlamentar votou nas proposições de um TEMA, numa única chamada: executa "
    "`busca_semantica_proposicoes(termos, somente_votadas=True)` e `consultar_votos` com os IDs "
    "encontrados (regra 2-B do guia). Use para 'o deputado X votou a favor de Y?' ou 'como a senadora X "
    "votou sobre Y?'. Passe de 4 a 6 termos do tema.\n"
    "Devolve `votou` (cada votação com a descrição oficial do que foi decidido), `ausente_ou_sem_voto` "
    "(AUSENTE, OUTRA_CASA, FORA_DE_EXERCICIO, SEM_VOTACAO_NOMINAL, FORA_DA_BASE — ausência de dado, nunca posição) e as proposições do "
    "tema sem votação nominal. Confira pela ementa se cada proposição é mesmo do tema. Só votação sobre o "
    "TEXTO autoriza dizer a favor ou contra. Resultado paginado."
)

PLACAR_POR_VOTACAO = (
    "Placar de CADA votação nominal de uma lista de proposições, com a descrição oficial do que foi "
    "votado, opcionalmente filtrado por partido e/ou UF NA DATA DO VOTO e opcionalmente com os nomes de "
    "quem votou em cada opção (`listar_parlamentares=true`).\n"
    "Use para perguntas sobre um COLETIVO (partido, bancada, estado) e para 'quem votou a favor/contra "
    "X' (regras 2-D e 2-E do guia). Nunca generalize a partir de alguns parlamentares. Nunca some "
    "placares de votações diferentes: cada item é UMA votação (urgência, texto, emenda, destaque), e só "
    "a votação do TEXTO revela posição sobre a proposição — identifique-a pela descrição. Informe o "
    "placar e o total do recorte, inclusive os divergentes. Resultado paginado."
)

POSICAO_CONSOLIDADA = (
    "Votos de vários parlamentares em várias proposições, lado a lado, votação por votação, com a "
    "descrição oficial de cada uma. Use para 'sou a favor de X, quem se alinha comigo?' e para "
    "verificar se alguém votou de forma consistente entre proposições do mesmo tema (regra 2-E do "
    "guia).\n"
    "Os campos `votou_igual_em_todas_as_votacoes` e `votou_de_formas_diferentes_no_conjunto` são fatos "
    "sobre os registros, não julgamento de coerência: compare apenas as votações sobre o TEXTO. "
    "Proposições do mesmo tema não são intercambiáveis: quem votou a favor de uma e contra outra é "
    "DIVIDIDO — diga em qual foi a favor e em qual foi contra. Resultado paginado."
)

MAPEAR_AUTORES = (
    "Mapeia QUEM assinou proposições sobre um tema, com a ementa de cada proposição. Varre as 429 mil "
    "ementas — não só as mais similares da busca.\n"
    "USE-A SEMPRE que a pergunta pedir NOMES de quem defende, apoia, se opõe ou se alinha a um tema.\n"
    "'termo' é o RADICAL do tema — a palavra sem a terminação, para pegar as flexões ('licenciament' "
    "pega licenciamento/licenciamentos). Sem '%'. Radical com menos de 8 letras devolve "
    "'formas_que_o_radical_casou' com o que ele alcançou no acervo: CONFIRA essa lista antes de citar "
    "nomes — 'apost' casa 'aposta' e também 'aposto' (de APOR), e uma família só pode ter assuntos "
    "distintos ('inteligência' cobre IA e a ABIN). A ferramenta não julga isso por você. 'termo_secundario' "
    "desambigua tema genérico: termo='intelig' + termo_secundario='artificial'; termo='saude' + "
    "termo_secundario='mulher'.\n"
    "Devolve os autores com todas as proposições e ementas, e as votações nominais do tema. NÃO "
    "classifica o que as proposições pedem: leia a ementa e diga você o que cada uma pede (restringir, "
    "incentivar, regulamentar...) antes de afirmar a posição de alguém, sem atribuir rótulo à "
    "ferramenta. Assinar é INTENÇÃO DECLARADA, nunca voto — não misture com placar de votação. "
    "Resultado paginado."
)

CONSULTAR_VETOS = (
    "Consulta mensagens de vetos presidenciais (proposições com sigla_tipo = 'VET') de autoria da "
    "Presidência da República no mandato de um presidente ('Lula', 'Bolsonaro', 'Dilma', 'Temer'). "
    "Aceita um 'termo' opcional para filtrar os vetos relacionados a um tema (busca semântica nas "
    "ementas dos vetos). A base de vetos começa em 2018: mandato anterior volta como FORA_DA_COBERTURA "
    "e mandato parcialmente coberto vem com aviso de recorte. Nome não reconhecido é erro explícito. "
    "Resultado paginado."
)

EXECUTAR_SQL = (
    "[ADMIN] Executa uma consulta SQL SELECT (ou WITH) somente leitura no banco legislativo. Use para "
    "contagens, agregações, ordenações complexas e cruzamentos que nenhuma outra ferramenta faz. O "
    "resultado vem inteiro, em páginas (sem limite de linhas); use ORDER BY para páginas estáveis.\n"
    "Esquema completo em `leis://esquema` e no guia. Armadilhas: `casa` guarda 'Câmara'/'Senado' POR "
    "EXTENSO (nunca 'CD'/'SF', que devolvem zero linhas sem erro); use `votos.partido_voto` (partido na "
    "data do voto), nunca `parlamentares.partido`; placares SEMPRE agrupados por `id_votacao` e com "
    "`votacoes.descricao` no SELECT — somar votações diferentes produz número que não corresponde a "
    "decisão nenhuma. Para ID de proposição citada pelo nome, prefira `buscar_proposicao`."
)


def verificar_tamanhos() -> dict[str, int]:
    """Descrições acima do limite do Claude Code (~2 KB) seriam cortadas."""
    return {
        nome: len(valor)
        for nome, valor in globals().items()
        if nome.isupper() and isinstance(valor, str) and len(valor) > LIMITE_CARACTERES
    }
