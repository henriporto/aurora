"""
Quem assinou proposições sobre um tema, com a ementa de cada proposição.

Varre as ~429 mil ementas por `LIKE`, e não o top-k da busca semântica: uma
proposição que cita o tema de passagem fica fora do top-k. Caso medido no
projeto original — o PL 897/2025 ("Incentivo à Pesquisa em IA com Recursos
das Apostas") pontua 0,47 numa busca por apostas, abaixo de quarenta outras, e
era a ÚNICA proposta de fomento do conjunto.

A ferramenta NÃO classifica o que cada proposição pede. Já classificou
(`restringe` / `fomenta` / `outra`, por substring de verbos na ementa), e o
rótulo errava por construção — "irrestrito" e "delimitar" caíam em
`restringe` — e chegava ao usuário como fato ("a varredura marcou 4
parlamentares como fomenta"). Foi o mesmo julgamento que removeu a
`natureza` das votações: entregar o texto oficial e deixar a leitura com o
modelo, que tem a pergunta em mãos.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Optional

from leis_mcp.dados.banco import conexao
from leis_mcp.paginacao import cache, chave_de, paginar
from leis_mcp.texto import normalizar_casa, palavras, rotulo_proposicao, sem_acento

#: Quão perto a segunda família de palavras pode chegar da primeira antes de o
#: radical ser ambíguo. Medido em 16 temas: recusa 'apost' (aposta × aposto) e
#: 'idos' (idoso × idosa); aprova 'mulher', 'arma', 'trabalh'.
EMPATE_MAXIMO = 0.80

def _formas_do_radical(conn: sqlite3.Connection, radical: str) -> dict[str, int]:
    """Quais palavras o radical casa nas ementas, e com que frequência."""
    formas: dict[str, int] = {}
    padrao = re.compile(rf"\b{re.escape(radical)}\w*", re.IGNORECASE)
    for (ementa,) in conn.execute(
        "SELECT ementa FROM proposicoes WHERE ementa LIKE ?", (f"%{radical}%",)
    ):
        for palavra in padrao.findall(ementa or ""):
            formas[palavra.lower()] = formas.get(palavra.lower(), 0) + 1
    return formas


def _familias(radical: str, formas: dict[str, int]) -> list[tuple[str, int]]:
    """Agrupa as formas pela primeira letra após o radical, sem decidir qual é o tema."""
    grupos: dict[str, int] = {}
    for forma, n in formas.items():
        resto = forma[len(radical) :]
        chave = radical + (resto[0] if resto else "")
        grupos[chave] = grupos.get(chave, 0) + n
    return sorted(grupos.items(), key=lambda x: -x[1])


def _calcular(
    radical: str, secundario: str, aceitar_radical_amplo: bool
) -> dict[str, Any]:
    with conexao(row_factory=True) as conn:
        # A guarda vale MESMO com termo_secundario: ela é sobre o radical
        # principal. Condicionada a "sem secundário", bastou o modelo chamar
        # termo='apost' + 'bet' para passar por cima dela.
        if len(radical) < 8 and not aceitar_radical_amplo:
            familias = _familias(radical, _formas_do_radical(conn, radical))
            total = sum(n for _, n in familias)
            segunda = familias[1][1] if len(familias) > 1 else 0
            empate = segunda / familias[0][1] if familias and familias[0][1] else 0
            if total >= 20 and empate >= EMPATE_MAXIMO:
                return {
                    "erro": "RADICAL_AMBIGUO",
                    "observacao": (
                        f"O radical '{radical}' abre em duas famílias de palavras de tamanho parecido — "
                        f"'{familias[0][0]}*' ({familias[0][1]}) e '{familias[1][0]}*' ({familias[1][1]}) —, "
                        "o que costuma significar duas leituras diferentes da mesma raiz. Nenhum dado foi "
                        "consultado. Escolha a família que corresponde à SUA pergunta e refaça (ex.: "
                        f"termo='{familias[0][0]}'). Se as famílias forem do mesmo tema (masculino e feminino "
                        "da mesma palavra, por exemplo), repita com aceitar_radical_amplo=true. Escolher errado "
                        "atribui posição política a quem não a tem: em 'apost', a família mais frequente é "
                        "'aposto*' — o particípio de APOR, como em 'assinatura aposta'."
                    ),
                    "familias": [
                        {
                            "prefixo": f"{f}*",
                            "ocorrencias": n,
                            "fatia_do_radical": f"{100 * n / total:.0f}%",
                        }
                        for f, n in familias[:6]
                    ],
                    "escala": (
                        "O acervo tem ~429 mil ementas. Um tema comum aparece em algumas centenas a alguns "
                        "milhares delas: centenas de ocorrências é tamanho NORMAL de tema."
                    ),
                }

        condicoes = ["p.ementa LIKE ?"]
        valores: list[Any] = [f"%{radical}%"]
        if secundario:
            condicoes.append("p.ementa LIKE ?")
            valores.append(f"%{secundario}%")

        def buscar(expressao: str, params: list[Any]):
            return conn.execute(
                f"""
                SELECT pa.nome, pa.partido, pa.uf, p.id_proposicao, p.sigla_tipo, p.numero, p.ano, p.ementa
                FROM autoria a
                JOIN parlamentares pa ON pa.id_parlamentar = a.id_parlamentar
                JOIN proposicoes p ON p.id_proposicao = a.id_proposicao
                WHERE {expressao}
                ORDER BY p.ano DESC, p.id_proposicao DESC
                """,
                params,
            ).fetchall()

        linhas = buscar(" AND ".join(condicoes), valores)
        # O LIKE é sensível a acento ('saude' acha 1 proposição, 'saúde' acha
        # 426). A passada sem acento custa ~7 s contra 0,1 s, por isso só roda
        # quando a primeira volta praticamente vazia.
        if len(linhas) < 10:
            conn.create_function("sem_acento", 1, sem_acento, deterministic=True)
            expr = " AND ".join("sem_acento(p.ementa) LIKE ?" for _ in condicoes)
            alternativa = buscar(expr, [sem_acento(v) for v in valores])
            if len(alternativa) > len(linhas):
                linhas = alternativa

        # Votações do tema. Filtra pela EMENTA, não pelos IDs de autoria: as
        # proposições que vão a voto costumam ter autoria institucional, excluída
        # acima. Usa SÓ o radical principal: com 'apost' + 'bet' o filtro
        # combinado apagou as oito votações do PL 3626/2023, cuja ementa não
        # contém "bet". Uma linha por VOTAÇÃO, nunca somada por proposição.
        votadas = conn.execute(
            """
            SELECT p.id_proposicao, p.sigla_tipo, p.numero, p.ano, vt.id_votacao,
                   vt.descricao, vt.data, vt.casa, vt.total_votos
            FROM votacoes vt JOIN proposicoes p ON p.id_proposicao = vt.id_proposicao
            WHERE p.ementa LIKE ?
            ORDER BY vt.total_votos DESC, vt.data
            """,
            (f"%{radical}%",),
        ).fetchall()

        # Radical curto demais casa palavras de outro assunto e infla o total em
        # silêncio ('apost' casa "veto aposto ao Projeto"). Medido comparando
        # com o radical uma letra mais longo.
        aviso_radical = None
        if len(radical) < 8 and not secundario:
            curto, longo = conn.execute(
                "SELECT (SELECT COUNT(*) FROM proposicoes WHERE ementa LIKE ?), "
                "(SELECT COUNT(*) FROM proposicoes WHERE ementa LIKE ?)",
                (f"%{radical}%", f"%{radical}a%"),
            ).fetchone()
            if curto and longo and curto > longo * 1.5:
                aviso_radical = (
                    f"ATENÇÃO AO RADICAL: '{radical}' casa {curto} ementas, mas '{radical}a' casa {longo} — "
                    "a diferença sugere que ele está pegando palavras de outro assunto. Se os resultados "
                    "parecerem fora do tema, repita com o radical mais longo ou use `termo_secundario`."
                )

    if not linhas:
        return {
            "tema": radical,
            "total_proposicoes": 0,
            "observacao": (
                f"Nenhuma proposição com '{radical}' na ementa tem autor parlamentar individual. Tente um "
                "radical mais curto ou outra palavra do tema."
            ),
        }

    autores: dict[tuple, dict[str, Any]] = {}
    for r in linhas:
        pessoa = autores.setdefault(
            (r["nome"], r["partido"], r["uf"]),
            {
                "nome": r["nome"],
                "partido": r["partido"],
                "uf": r["uf"],
                "proposicoes": [],
            },
        )
        # Todas as proposições e a ementa inteira: a ementa é a única evidência
        # do que a pessoa propõe, e é o modelo quem a lê.
        pessoa["proposicoes"].append(
            {
                "id_proposicao": r["id_proposicao"],
                "proposicao": rotulo_proposicao(r["sigla_tipo"], r["numero"], r["ano"]),
                "ementa": r["ementa"] or "",
            }
        )
    parlamentares = sorted(
        autores.values(), key=lambda p: (-len(p["proposicoes"]), p["nome"])
    )

    avisos = [
        "Esta ferramenta NÃO classifica o que as proposições pedem: não há rótulo de direção, e nenhum "
        "deve ser atribuído à ferramenta na resposta. Leia a EMENTA de cada proposição e diga, com suas "
        "palavras, o que ela pede — restringir, proibir, regulamentar, incentivar, tributar, isentar. "
        "Cuidado com a negação: 'redução de incentivos' não é incentivo; 'acesso irrestrito' não é restrição.",
        "OBRIGATÓRIO ANTES DE CITAR UM NOME: confirme pela ementa (1) que ela trata mesmo do tema, porque o "
        "radical pode ter casado outra palavra ('assinatura aposta' não é aposta), e (2) o que ela pede. "
        "Se a ementa não permitir dizer, não atribua posição à pessoa.",
        "Assinar uma proposição é INTENÇÃO LEGISLATIVA DECLARADA, não voto. Rotule assim ao apresentar, "
        "e não misture com placar de votação.",
    ]
    if votadas:
        avisos.append(
            f"EXISTE VOTAÇÃO NOMINAL sobre este tema: {len(votadas)} votação(ões) em `com_votacao_nominal`, "
            "cada uma com `id_votacao` e `descricao`. É a evidência MAIS FORTE. Consulte com "
            "`placar_por_votacao` (placar e nomes de cada votação) e `posicao_consolidada` (cada pessoa em "
            "todas as proposições do tema). Leia a descrição para saber qual é a votação do TEXTO, e NÃO "
            "afirme que o tema não teve votação nominal."
        )
    else:
        avisos.append(
            "Nenhuma das proposições com este radical na ementa tem votação nominal registrada — aqui sim "
            "cabe dizer ao usuário que não há voto individual a consultar."
        )
    if aviso_radical:
        avisos.append(aviso_radical)

    return {
        "tema": radical + (f" + {secundario}" if secundario else ""),
        "total_proposicoes": len({r["id_proposicao"] for r in linhas}),
        "total_parlamentares": len(parlamentares),
        "com_votacao_nominal": [
            {
                "id_proposicao": v["id_proposicao"],
                "proposicao": rotulo_proposicao(v["sigla_tipo"], v["numero"], v["ano"]),
                "id_votacao": v["id_votacao"],
                "data": v["data"],
                "casa": v["casa"],
                "descricao": v["descricao"],
                "votos_registrados": v["total_votos"],
            }
            for v in votadas
        ],
        "avisos": avisos,
        "parlamentares": parlamentares,
    }


def mapear_autores_por_tema(
    termo: str,
    termo_secundario: Optional[str] = None,
    aceitar_radical_amplo: bool = False,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    radical = (termo or "").strip().strip("%")
    if len(radical) < 3:
        return {
            "erro": "TERMO_MUITO_CURTO",
            "observacao": (
                "Informe o radical do tema com pelo menos 3 letras, sem '%' (ex: 'aposta', 'intelig'). "
                "Radical curto demais varre a base inteira."
            ),
        }
    secundario = (termo_secundario or "").strip().strip("%")
    completo = cache.obter_ou_calcular(
        chave_de("autores", radical, secundario, bool(aceitar_radical_amplo)),
        lambda: _calcular(radical, secundario, bool(aceitar_radical_amplo)),
    )
    if "parlamentares" not in completo:
        return completo

    cabecalho = {k: v for k, v in completo.items() if k != "parlamentares"}
    # Lista compacta de TODOS os nomes (nome, partido, UF e quantas proposições
    # assinou). Vai em toda página quando há mais de uma, para o modelo saber
    # quem existe sem depender da página 6 — mas não traz ementa, então não diz
    # nada sobre o que a pessoa propõe.
    resumo = [
        f"{p['nome']} ({p['partido']}-{p['uf']}): {len(p['proposicoes'])} proposição(ões)"
        for p in completo["parlamentares"]
    ]
    itens, paginacao = paginar(
        completo["parlamentares"],
        pagina,
        reserva=len(str(cabecalho)) + len(str(resumo)) + 3_000,
        resumo="O campo `todos_os_autores`",
    )
    avisos = list(completo["avisos"])
    if paginacao.get("aviso"):
        avisos.insert(
            0,
            paginacao["aviso"]
            + " `todos_os_autores` diz QUEM assinou, não O QUE a pessoa propõe: isso só se sabe pela ementa, "
            "que está no detalhe paginado. Antes de citar alguém, leia a ementa dele na página em que aparece; "
            "antes de afirmar que ninguém propõe algo, leia TODAS as páginas.",
        )
    saida: dict[str, Any] = {"paginacao": paginacao, **cabecalho, "avisos": avisos}
    if not paginacao["completo"]:
        saida["todos_os_autores"] = resumo
    saida["parlamentares"] = itens
    return saida


# ---------------------------------------------------------------------------
# proposicoes_por_autor_institucional
# ---------------------------------------------------------------------------


def proposicoes_por_autor_institucional(
    autor: str,
    id_ente: Optional[int] = None,
    casa_da_fonte: Optional[str] = None,
    ano: Optional[int] = None,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    """
    Proposições de autoria de uma instituição: Presidência da República / Poder
    Executivo, Câmara dos Deputados, Senado Federal, comissões, tribunais…

    A autoria vem estruturada das APIs (`autores_proposicao`). Cada casa tem seu
    próprio ID de instituição (o órgão 4 da Câmara é a Mesa Diretora; o ente 4
    do Senado é o TCU), por isso o ente é (casa da fonte, id). O nome é comparado
    palavra a palavra, sem acento: cada palavra pedida precisa ser o início de
    uma palavra do nome. Mais de um ente compatível devolve a lista para escolher.
    """
    procuradas = palavras(autor or "")
    casa = normalizar_casa(casa_da_fonte)
    if not procuradas and id_ente is None:
        return {
            "erro": "AUTOR_VAZIO",
            "observacao": "Informe o nome da instituição (ex.: 'Presidência da República') ou o `id_ente`.",
        }
    with conexao() as conn:
        # Um mesmo ente aparece com nomes diferentes (o órgão 78 da Câmara vem como
        # "Senado Federal" e "Senado Federal - <senador>"): casa se QUALQUER nome
        # casar, e o exibido é o mais frequente.
        nomes: dict[tuple[str, int], dict[str, int]] = {}
        tipos: dict[tuple[str, int], str] = {}
        for origem, ente_id, nome, tipo, n in conn.execute(
            """
            SELECT origem, id_ente, nome, MAX(tipo), COUNT(*)
            FROM autores_proposicao
            WHERE fonte = 'documento' AND id_parlamentar IS NULL AND id_ente IS NOT NULL
              AND codigo_parlamentar_senado IS NULL
            GROUP BY origem, id_ente, nome
            """
        ):
            nomes.setdefault((origem, ente_id), {})[nome] = n
            tipos[(origem, ente_id)] = tipo
        compativeis = [
            chave
            for chave, variantes in nomes.items()
            if (casa is None or chave[0] == casa)
            and (id_ente is None or chave[1] == int(id_ente))
            and any(
                all(any(w.startswith(p) for w in palavras(nome)) for p in procuradas)
                for nome in variantes
            )
        ]
        totais = dict(
            conn.execute(
                "SELECT origem || ':' || id_ente, COUNT(DISTINCT id_proposicao) FROM autores_proposicao "
                "WHERE fonte = 'documento' AND id_parlamentar IS NULL AND id_ente IS NOT NULL "
                "AND codigo_parlamentar_senado IS NULL GROUP BY origem, id_ente"
            )
        )
        candidatos = [
            {
                "casa_da_fonte": origem,
                "id_ente": ente_id,
                "nome": max(nomes[(origem, ente_id)].items(), key=lambda x: x[1])[0],
                "tipo": tipos[(origem, ente_id)],
                "proposicoes": totais.get(f"{origem}:{ente_id}", 0),
            }
            for origem, ente_id in compativeis
        ]
        # Nome idêntico ao pedido vem primeiro; depois, o ente com mais proposições.
        identicos = {
            chave
            for chave in compativeis
            if any(palavras(nome) == procuradas for nome in nomes[chave])
        }
        candidatos.sort(
            key=lambda e: ((e["casa_da_fonte"], e["id_ente"]) not in identicos, -e["proposicoes"])
        )
        if len(candidatos) != 1:
            return {
                "total_entes": len(candidatos),
                "entes": candidatos,
                "observacao": (
                    "Nenhuma instituição com esse nome tem autoria registrada na base."
                    if not candidatos
                    else "Mais de uma instituição compatível: chame de novo com `id_ente` e `casa_da_fonte`. "
                    "A mesma instituição pode aparecer uma vez por casa (ex.: Presidência da República no "
                    "Senado e Poder Executivo na Câmara)."
                ),
            }
        ente = candidatos[0]
        filtro_ano = " AND p.ano = ?" if ano else ""
        ids = [
            r[0]
            for r in conn.execute(
                f"""
                SELECT p.id_proposicao FROM autores_proposicao ap
                JOIN proposicoes p ON p.id_proposicao = ap.id_proposicao
                WHERE ap.fonte = 'documento' AND ap.origem = ? AND ap.id_ente = ?
                  AND ap.id_parlamentar IS NULL AND ap.codigo_parlamentar_senado IS NULL{filtro_ano}
                GROUP BY p.id_proposicao
                ORDER BY MAX(p.data_apresentacao) DESC
                """,
                [ente["casa_da_fonte"], ente["id_ente"], *([int(ano)] if ano else [])],
            )
        ]
    itens, paginacao = paginar(ids, pagina, reserva=2_000)
    return {
        "paginacao": paginacao,
        "ente": ente,
        "total": len(ids),
        "ids_proposicoes": itens,
        "aviso_sistema": (
            "Autoria institucional informada pela API da casa em `casa_da_fonte`. Para ver as "
            "proposições, use `obter_detalhes_proposicoes`; para filtrar por tema, passe estes IDs em "
            "`lista_ids` de `busca_semantica_proposicoes`. Autoria é intenção declarada, não voto."
        ),
    }
