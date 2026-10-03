"""
Montagem do servidor MCP: ferramentas, recursos, prompts, autenticação e rotas.

As funções de `ferramentas/` não conhecem MCP. Aqui elas ganham nome, descrição
para o modelo, esquema de parâmetros (via anotações de tipo), tags de controle
de acesso e anotações de comportamento (`readOnlyHint` etc.).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Optional

from fastmcp import FastMCP
from mcp.types import Icon
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from leis_mcp import descricoes as d
from leis_mcp.config import Config
from leis_mcp.dados.banco import conexao, verificar_banco
from leis_mcp.dados.buscador import obter_buscador
from leis_mcp.dados.cobertura import texto_de_alcance
from leis_mcp.ferramentas import (
    autoria,
    parlamentares,
    proposicoes,
    sql,
    tema,
    vetos,
    votos,
)
from leis_mcp.regras import INSTRUCOES, regras_completas
from leis_mcp.usuarios.controle import (
    TAG_ADMIN,
    TAG_LIVRE,
    TAG_PESADA,
    ControleDeAcesso,
)
from leis_mcp.usuarios.repositorio import Repositorio

logger = logging.getLogger(__name__)

SOMENTE_LEITURA = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

#: O Claude Code guarda em arquivo qualquer resultado acima de ~25 mil tokens.
#: Esta anotação eleva o teto por ferramenta (máximo aceito: 500 mil
#: caracteres). As páginas do servidor ficam abaixo de LEIS_MAX_CHARS_PAGINA.
META_RESULTADO = {"anthropic/maxResultSizeChars": 500_000}

#: claude.ai e Claude Desktop abandonam a chamada em 240 s.
TIMEOUT_FERRAMENTA_SEG = 235.0

IdParlamentar = Annotated[
    int,
    Field(description="ID interno do parlamentar, obtido com buscar_id_parlamentar."),
]
IdsProposicoes = Annotated[
    list[int], Field(description="IDs internos (numéricos) das proposições.")
]
Casa = Annotated[
    Optional[str], Field(description="'Câmara' ou 'Senado'. Omita para as duas casas.")
]
DataInicio = Annotated[
    Optional[str],
    Field(
        description="Data mínima de apresentação (AAAA-MM-DD). Só se o usuário citou período."
    ),
]
DataFim = Annotated[
    Optional[str],
    Field(
        description="Data máxima de apresentação (AAAA-MM-DD). Só se o usuário citou período."
    ),
]
Termos = Annotated[
    list[str],
    Field(
        description="Lista de 4 a 6 termos de busca do tema, de preferência com 2 ou mais palavras cada.",
        min_length=1,
    ),
]
Pagina = Annotated[
    Optional[int],
    Field(
        description="Página do resultado (1 = primeira). Use quando o retorno anterior trouxer `proxima_pagina`.",
        ge=1,
    ),
]


def _esquema_do_banco() -> str:
    """Esquema real, lido do banco, com as armadilhas conhecidas."""
    tabelas = (
        "parlamentares",
        "proposicoes",
        "autoria",
        "relatorias",
        "votos",
        "votacoes",
        "proposicoes_situacao",
        "proposicoes_chunks",
    )
    linhas = ["ESQUEMA DO BANCO LEGISLATIVO (lido do arquivo em uso)", ""]
    with conexao() as conn:
        for t in tabelas:
            colunas = ", ".join(
                f"{c[1]} {c[2]}".strip()
                for c in conn.execute(f"PRAGMA table_info({t})")
            )
            linhas.append(f"- {t}({colunas})")
    linhas += [
        "",
        "ARMADILHAS:",
        "- `casa` guarda 'Câmara' e 'Senado' POR EXTENSO. 'CD'/'SF' devolvem zero linhas sem erro nenhum.",
        "- `votos.partido_voto` é o partido NA DATA DO VOTO; `parlamentares.partido` é o atual. Para bancada, use sempre o primeiro. A sigla é a crua de cada fonte: a mesma legenda aparece como PODE (Câmara) e PODEMOS (Senado), e renomeações mudam a sigla (PR→PL, PRB→REPUBLICANOS, PPS→CIDADANIA, PMDB→MDB, SD→SOLIDARIEDADE, PEN/PATRI→PATRIOTA). Filtre com `IN` de todas.",
        "- Uma proposição tem várias votações: agrupe por `id_votacao` e traga `votacoes.descricao` no SELECT. "
        "Somar votações diferentes produz placar que não corresponde a decisão nenhuma.",
        "- `votacoes` inclui votação SIMBÓLICA, em que ninguém é registrado individualmente: "
        "`tem_voto_nominal = 0` e nenhuma linha em `votos`. Isso é decisão sem nomes, NUNCA "
        "'não foi votada'. Para contar votação nominal, filtre `tem_voto_nominal = 1`.",
        "- `votacoes.aprovacao` (1/0/NULL) é o campo cru da API da Câmara e vale para o OBJETO daquela "
        "votação — uma redação final, um requerimento de urgência, um destaque —, NÃO para a proposição. "
        "Leia sempre junto com `descricao`. NULL é ausência de dado, não reprovação.",
        "- `proposicoes_situacao` é o estado da matéria (1 linha por proposição), e não tem relação com "
        "voto de parlamentar: 'Aprovada pelo Plenário' ali é fato sobre a matéria, nunca sobre alguém. "
        "As strings são as oficiais de cada casa e NÃO são unificadas (a Câmara escreve 'Transformado em "
        "Norma Jurídica', o Senado 'TRANSFORMADA EM NORMA JURÍDICA'); compare com LIKE, não com igualdade. "
        "Colunas `tramitacao`/`orgao`/`despacho`/`apreciacao` só existem para a Câmara, e `tramitando`/"
        "`deliberacao`/`norma_gerada` só para o Senado.",
        "- A mesma proposição pode existir duas vezes, com IDs diferentes, quando tramita nas duas casas: "
        "`proposicoes_equivalentes(id_camara, id_senado)`. Números anteriores (a Câmara renumera) ficam em "
        "`proposicoes_identificacoes`.",
        "- `votos.tipo_voto` inclui registros sem posição: 'Ausente', 'Não registrou voto', 'Presidente (não vota)', "
        "'Votou (secreta)', 'Indeterminado', 'Artigo 17' (presidente da sessão na Câmara).",
        "- `autoria` liga proposições a parlamentares (pessoas). A autoria completa informada pela API, com "
        "instituições (Presidência, Câmara dos Deputados, comissões), está em `autores_proposicao` "
        "(`fonte` = 'documento' ou 'iniciativa'; `tipo` oficial, `id_ente`, `codigo_parlamentar_senado`).",
        "- O LIKE do SQLite é sensível a acento: '%saude%' e '%saúde%' dão resultados muito diferentes.",
    ]
    return "\n".join(linhas)


def criar_servidor(cfg: Config, repositorio: Repositorio) -> FastMCP:
    acima = d.verificar_tamanhos()
    if acima:
        logger.warning(
            "Descrições acima de %d caracteres serão cortadas pelo Claude Code: %s",
            d.LIMITE_CARACTERES,
            acima,
        )

    auth = None
    if cfg.auth_ligada:
        from fastmcp.server.auth.providers.google import GoogleProvider

        auth = GoogleProvider(
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret,
            base_url=cfg.url_publica,
            required_scopes=["openid", "email", "profile"],
            jwt_signing_key=cfg.jwt_chave,
        )

    tags: dict[str, set[str]] = {}
    mcp = FastMCP(
        name="Aurora",
        website_url="https://auroravoto.com.br",
        icons=[Icon(src="https://auroravoto.com.br/lobo.png", mimeType="image/png")],
        instructions=INSTRUCOES,
        version="0.1.0",
        auth=auth,
        mask_error_details=cfg.mascarar_erros,
    )

    def ferramenta(nome: str, descricao: str, *marcas: str):
        tags[nome] = set(marcas)
        return mcp.tool(
            name=nome,
            description=descricao,
            tags=set(marcas),
            annotations=SOMENTE_LEITURA,
            meta=META_RESULTADO,
            timeout=TIMEOUT_FERRAMENTA_SEG,
        )

    # ---- Guia ------------------------------------------------------------

    @ferramenta("guia_de_pesquisa", d.GUIA_DE_PESQUISA, TAG_LIVRE)
    def guia_de_pesquisa() -> str:
        return regras_completas(texto_de_alcance())

    # ---- Parlamentares ---------------------------------------------------

    @ferramenta("buscar_id_parlamentar", d.BUSCAR_ID_PARLAMENTAR)
    def buscar_id_parlamentar(
        nome: Annotated[
            str,
            Field(
                description="Nome ou fragmento de nome do parlamentar (ex.: 'Silva')."
            ),
        ],
        casa: Casa = None,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return parlamentares.buscar_id_parlamentar(nome, casa, pagina)

    @ferramenta("consultar_historico", d.CONSULTAR_HISTORICO)
    def consultar_historico(
        id_parlamentar: IdParlamentar,
        papel: Annotated[
            Optional[str],
            Field(description="'autor', 'relator' ou 'votante'. Omita para os três."),
        ] = None,
        tipo_voto: Annotated[
            Optional[str],
            Field(
                description="Rótulo exato de voto ('Sim', 'Não', 'Abstenção', 'Obstrução'...), para papel votante."
            ),
        ] = None,
        ano: Annotated[Optional[int], Field(description="Ano da proposição.")] = None,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return parlamentares.consultar_historico(
            id_parlamentar, papel, tipo_voto, ano, pagina
        )

    # ---- Proposições -----------------------------------------------------

    @ferramenta("buscar_proposicao", d.BUSCAR_PROPOSICAO)
    def buscar_proposicao(
        sigla_tipo: Annotated[
            str, Field(description="Sigla do tipo: PL, PEC, PLP, MPV, PDL, REQ, VET...")
        ],
        numero: Annotated[int, Field(description="Número da proposição.")],
        ano: Annotated[Optional[int], Field(description="Ano da proposição.")] = None,
        casa: Casa = None,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return proposicoes.buscar_proposicao(sigla_tipo, numero, ano, casa, pagina)

    @ferramenta("obter_detalhes_proposicoes", d.OBTER_DETALHES)
    def obter_detalhes_proposicoes(
        lista_ids: IdsProposicoes, pagina: Pagina = None
    ) -> dict[str, Any]:
        return proposicoes.obter_detalhes_proposicoes(lista_ids, pagina)

    @ferramenta("busca_semantica_proposicoes", d.BUSCA_SEMANTICA, TAG_PESADA)
    def busca_semantica_proposicoes(
        termos: Termos,
        somente_votadas: Annotated[
            bool, Field(description="true quando a pergunta é sobre COMO alguém votou.")
        ] = False,
        casa: Casa = None,
        data_inicio: DataInicio = None,
        data_fim: DataFim = None,
        lista_ids: Annotated[
            Optional[list[int]],
            Field(description="Restringe a busca a estes IDs de proposições."),
        ] = None,
        top_k: Annotated[
            Optional[int],
            Field(
                description="Quantas proposições considerar (padrão 40, máximo 500).",
                ge=1,
                le=500,
            ),
        ] = None,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return proposicoes.busca_semantica_proposicoes(
            termos,
            somente_votadas=somente_votadas,
            casa=casa,
            data_inicio=data_inicio,
            data_fim=data_fim,
            lista_ids=lista_ids,
            top_k=top_k,
            pagina=pagina,
        )

    @ferramenta("busca_inteiro_teor", d.BUSCA_INTEIRO_TEOR, TAG_PESADA)
    def busca_inteiro_teor(
        termo: Annotated[
            str,
            Field(
                description="Conceito, artigo ou trecho a localizar no texto das proposições."
            ),
        ],
        lista_ids: Annotated[
            Optional[list[int]],
            Field(
                description="Restringe a estas proposições (IDs internos numéricos)."
            ),
        ] = None,
        casa: Casa = None,
        data_inicio: DataInicio = None,
        data_fim: DataFim = None,
        top_k: Annotated[
            Optional[int],
            Field(description="Quantos trechos (padrão 8, máximo 100).", ge=1, le=100),
        ] = None,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return proposicoes.busca_inteiro_teor(
            termo, lista_ids, casa, data_inicio, data_fim, top_k, pagina
        )

    # ---- Votos -----------------------------------------------------------

    @ferramenta("consultar_votos", d.CONSULTAR_VOTOS)
    def consultar_votos(
        id_parlamentar: IdParlamentar,
        lista_ids: Annotated[
            Optional[list[int]],
            Field(
                description="IDs das proposições a verificar. Omita para o histórico completo."
            ),
        ] = None,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return votos.consultar_votos(id_parlamentar, lista_ids, pagina)

    @ferramenta("votos_por_tema", d.VOTOS_POR_TEMA, TAG_PESADA)
    def votos_por_tema(
        id_parlamentar: IdParlamentar,
        termos: Termos,
        casa: Casa = None,
        data_inicio: DataInicio = None,
        data_fim: DataFim = None,
        top_k: Annotated[
            Optional[int],
            Field(
                description="Quantas proposições do tema considerar (padrão 40, máximo 500).",
                ge=1,
                le=500,
            ),
        ] = None,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return tema.votos_por_tema(
            id_parlamentar, termos, casa, data_inicio, data_fim, top_k, pagina
        )

    @ferramenta("placar_por_votacao", d.PLACAR_POR_VOTACAO)
    def placar_por_votacao(
        lista_ids: IdsProposicoes,
        partido: Annotated[
            Optional[str],
            Field(description="Sigla do partido NA DATA DO VOTO (ex.: PT, PL, UNIÃO). Grafias e renomeações da mesma legenda entram juntas (PODE = PODEMOS, PR = PL)."),
        ] = None,
        uf: Annotated[
            Optional[str],
            Field(description="UF do parlamentar na data do voto (ex.: SP)."),
        ] = None,
        listar_parlamentares: Annotated[
            bool, Field(description="Inclui os nomes de quem votou em cada opção.")
        ] = False,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return votos.placar_por_votacao(
            lista_ids, partido, uf, listar_parlamentares, pagina
        )

    @ferramenta("posicao_consolidada", d.POSICAO_CONSOLIDADA)
    def posicao_consolidada(
        ids_parlamentares: Annotated[
            list[int], Field(description="IDs internos dos parlamentares.")
        ],
        lista_ids: Annotated[
            list[int], Field(description="IDs internos das proposições do tema.")
        ],
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return votos.posicao_consolidada(ids_parlamentares, lista_ids, pagina)

    # ---- Autoria e vetos -------------------------------------------------

    @ferramenta("mapear_autores_por_tema", d.MAPEAR_AUTORES, TAG_PESADA)
    def mapear_autores_por_tema(
        termo: Annotated[
            str,
            Field(
                description="Radical do tema, sem '%' (ex.: 'aposta', 'licenciament')."
            ),
        ],
        termo_secundario: Annotated[
            Optional[str],
            Field(
                description="Segundo radical para desambiguar (ex.: termo='intelig', termo_secundario='artificial')."
            ),
        ] = None,
        confirmar_radical: Annotated[
            bool,
            Field(
                description="Radical com menos de 8 letras devolve PREVIA_DO_RADICAL (o que ele alcança, "
                "com ementas de exemplo) em vez de nomes. Leia, e repita com true se as famílias forem "
                "todas do seu assunto."
            ),
        ] = False,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return autoria.mapear_autores_por_tema(
            termo, termo_secundario, confirmar_radical, pagina
        )

    @ferramenta("proposicoes_por_autor_institucional", d.AUTOR_INSTITUCIONAL)
    def proposicoes_por_autor_institucional(
        autor: Annotated[
            str,
            Field(
                description="Nome ou parte do nome da instituição (ex.: 'Presidência da República', 'Câmara dos Deputados', 'Comissão de Assuntos Econômicos')."
            ),
        ],
        id_ente: Annotated[
            Optional[int],
            Field(description="ID oficial do ente, quando o nome casou com mais de um."),
        ] = None,
        casa_da_fonte: Annotated[
            Optional[str],
            Field(description="'Câmara' ou 'Senado': a casa cuja API informou o ente (vem em `entes`)."),
        ] = None,
        ano: Annotated[Optional[int], Field(description="Ano da proposição.")] = None,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return autoria.proposicoes_por_autor_institucional(autor, id_ente, casa_da_fonte, ano, pagina)

    @ferramenta("consultar_vetos_presidenciais", d.CONSULTAR_VETOS, TAG_PESADA)
    def consultar_vetos_presidenciais(
        presidente: Annotated[
            str, Field(description="'Lula', 'Bolsonaro', 'Dilma' ou 'Temer'.")
        ],
        termo: Annotated[
            Optional[str], Field(description="Tema para filtrar os vetos.")
        ] = None,
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return vetos.consultar_vetos_presidenciais(presidente, termo, pagina)

    # ---- Administração ---------------------------------------------------

    @ferramenta("executar_consulta_sql", d.EXECUTAR_SQL, TAG_ADMIN, TAG_PESADA)
    def executar_consulta_sql(
        consulta: Annotated[
            str, Field(description="Uma única instrução SELECT ou WITH.")
        ],
        pagina: Pagina = None,
    ) -> dict[str, Any]:
        return sql.executar_consulta_sql(consulta, pagina)

    # ---- Recursos --------------------------------------------------------

    @mcp.resource(
        "leis://alcance",
        name="alcance",
        description="O que a base cobre: períodos e volumes medidos no banco.",
        mime_type="text/plain",
    )
    def recurso_alcance() -> str:
        return texto_de_alcance()

    @mcp.resource(
        "leis://regras",
        name="regras",
        description="Regras completas de pesquisa e interpretação (o mesmo conteúdo de guia_de_pesquisa).",
        mime_type="text/plain",
    )
    def recurso_regras() -> str:
        return regras_completas(texto_de_alcance())

    @mcp.resource(
        "leis://esquema",
        name="esquema",
        description="Esquema das tabelas do banco, com as armadilhas conhecidas.",
        mime_type="text/plain",
    )
    def recurso_esquema() -> str:
        return _esquema_do_banco()

    @mcp.resource(
        "leis://proposicao/{id_proposicao}",
        name="proposicao",
        description="Detalhes e placar de cada votação nominal de uma proposição.",
        mime_type="application/json",
    )
    def recurso_proposicao(id_proposicao: int) -> str:
        detalhes = proposicoes.obter_detalhes_proposicoes([int(id_proposicao)])[
            "proposicoes"
        ]
        placar = votos.placar_por_votacao([int(id_proposicao)])
        return json.dumps(
            {
                "proposicao": detalhes[0] if detalhes else None,
                "votacoes": placar.get("votacoes", []),
            },
            ensure_ascii=False,
        )

    @mcp.resource(
        "leis://parlamentar/{id_parlamentar}",
        name="parlamentar",
        description="Cadastro e totais de atuação de um parlamentar.",
        mime_type="application/json",
    )
    def recurso_parlamentar(id_parlamentar: int) -> str:
        with conexao(row_factory=True) as conn:
            r = conn.execute(
                "SELECT * FROM parlamentares WHERE id_parlamentar = ?",
                (int(id_parlamentar),),
            ).fetchone()
        historico = parlamentares.consultar_historico(int(id_parlamentar))
        return json.dumps(
            {
                "parlamentar": dict(r) if r else None,
                "totais_por_papel": historico.get("totais_por_papel"),
            },
            ensure_ascii=False,
        )

    # ---- Prompts ---------------------------------------------------------
    # Textos neutros: descrevem o método, nunca sugerem conclusão.

    @mcp.prompt(
        name="assistente_legislativo",
        title="Assistente legislativo: regras de pesquisa",
        description="Inicia a conversa com as regras completas de pesquisa e o alcance da base.",
    )
    def prompt_assistente() -> str:
        return (
            regras_completas(texto_de_alcance())
            + "\nSiga estas regras em todas as respostas desta conversa."
        )

    @mcp.prompt(
        name="como_votou",
        title="Como [parlamentar] votou sobre [tema]?",
        description="Como um parlamentar votou em proposições sobre um tema. "
        "Ex.: parlamentar = nome do deputado ou senador; tema = 'apostas esportivas'.",
    )
    def prompt_como_votou(
        parlamentar: Annotated[str, Field(description="Nome do deputado ou senador.")],
        tema: Annotated[str, Field(description="Ex.: 'apostas esportivas'.")],
    ) -> str:
        return (
            f"Como {parlamentar} votou em proposições sobre {tema}? Siga as regras 1, 2-B e 2-C do "
            "guia_de_pesquisa."
        )

    @mcp.prompt(
        name="como_votou_partido",
        title="Os parlamentares do [partido] votaram a favor de [tema]?",
        description="Como os parlamentares de um partido votaram sobre um tema, com o placar "
        "por votação. Ex.: tema = 'reforma tributária'.",
    )
    def prompt_como_votou_partido(
        partido: Annotated[str, Field(description="Sigla do partido.")],
        tema: Annotated[str, Field(description="Ex.: 'reforma tributária'.")],
    ) -> str:
        return f"Os parlamentares do {partido} votaram a favor de {tema}? Siga as regras 2-C e 2-D do guia_de_pesquisa."

    @mcp.prompt(
        name="quem_se_alinha",
        title="Sou [a favor de / contra] [tema]: quem se alinha comigo?",
        description="Quais parlamentares se alinham a uma posição sobre um tema, por voto "
        "de mérito e por autoria. Ex.: posicao = 'a favor de'; tema = 'regulamentar a "
        "inteligência artificial'.",
    )
    def prompt_quem_se_alinha(
        tema: Annotated[
            str, Field(description="Ex.: 'regulamentar a inteligência artificial'.")
        ],
        posicao: Annotated[str, Field(description="'a favor de' ou 'contra'.")],
    ) -> str:
        return f"Sou {posicao} {tema}. Quais parlamentares se alinham comigo? Siga a regra 2-E do guia_de_pesquisa."

    @mcp.prompt(
        name="quem_propos",
        title="Quem apresentou proposições sobre [tema]?",
        description="Quem apresentou proposições sobre um tema. Ex.: tema = 'saúde da mulher'.",
    )
    def prompt_quem_propos(
        tema: Annotated[str, Field(description="Ex.: 'saúde da mulher'.")],
    ) -> str:
        return f"Quem apresentou proposições sobre {tema}? Siga as regras 2-E e 9 do guia_de_pesquisa."

    @mcp.prompt(
        name="vetos_do_presidente",
        title="Quais vetos [presidente] apresentou sobre [tema]?",
        description="Vetos de um presidente, opcionalmente sobre um tema. "
        "Ex.: tema = 'saneamento básico'.",
    )
    def prompt_vetos(
        presidente: Annotated[
            str, Field(description="Nome do presidente da República.")
        ],
        tema: Annotated[
            str, Field(description="Opcional. Ex.: 'saneamento básico'.")
        ] = "",
    ) -> str:
        sobre = f" relacionados a {tema}" if tema else ""
        return f"Quais vetos {presidente} apresentou{sobre}? Siga a regra 3 do guia_de_pesquisa."

    # ---- Saúde -----------------------------------------------------------

    @mcp.custom_route("/saude", methods=["GET"], include_in_schema=False)
    async def saude(_: Request) -> JSONResponse:
        banco = verificar_banco()
        buscador = obter_buscador()
        pronto = banco["ok"] and buscador.pronto
        return JSONResponse(
            {
                "status": "ok" if pronto else "aquecendo",
                "banco": banco,
                "buscador_pronto": buscador.pronto,
            },
            status_code=200 if pronto else 503,
        )

    mcp.add_middleware(ControleDeAcesso(cfg, repositorio, tags))
    return mcp
