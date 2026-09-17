"""
Como parlamentares votaram.

Princípio que atravessa este módulo: **o código não decide o que foi votado.**
Uma proposição passa por várias votações — requerimento de urgência, texto,
emendas, destaques — e só a descrição oficial diz qual é qual. Um classificador
por expressão regular já existiu no projeto original e errava 44% do que
chamava de mérito ("Mantido o texto" é resultado de destaque, não do texto).
Por isso as ferramentas entregam a descrição crua de cada votação e nunca
colapsam várias votações num voto só.
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from typing import Any, Iterable, Optional

from leis_mcp import partidos

from leis_mcp.dados import exercicio
from leis_mcp.dados.banco import conexao
from leis_mcp.dados.cobertura import (
    ANO_INICIAL_VOTOS,
    MIN_VOTACOES_PARA_COBERTURA,
    cobertura_por_casa,
)
from leis_mcp.paginacao import paginar
from leis_mcp.texto import ids_inteiros, rotulo_proposicao

#: Rótulos de `votos.tipo_voto` que registram presença sem posição (o Senado os
#: grava; a Câmara omite a linha). "Artigo 17" é o presidente da sessão na
#: Câmara, que pelo art. 17 do RICD não vota nas deliberações ordinárias.
VOTOS_SEM_POSICIONAMENTO = {
    "Ausente",
    "Não registrou voto",
    "Presidente (não vota)",
    "Votou (secreta)",
    "Indeterminado",
    "Artigo 17",
}

#: Lote de parâmetros por consulta. O SQLite aceita 32.766 variáveis; lotes
#: menores mantêm os planos de consulta previsíveis.
LOTE = 900

#: Códigos oficiais do Senado para quem não expressou posição (a Câmara não
#: publica linha para quem não votou). Descrições da API do Senado.
MOTIVOS_SENADO = {
    "NCom": "não compareceu",
    "AP": "ausente em atividade parlamentar",
    "LA": "licença autorizada",
    "LS": "licença para tratamento de saúde",
    "MIS": "em missão oficial",
    "LP": "licença particular",
    "LG": "licença à gestante",
    "LAP": "licença paternidade ou ao adotante",
    "MERC": "ausente (código MERC; a API não descreve, provável missão no Parlamento do Mercosul)",
    "P-NRV": "presente, mas não registrou voto",
    "Presidente (art. 51 RISF)": "presidia a sessão (art. 51 do RISF: o presidente não vota)",
    "NA": "código NA, sem descrição na API",
    "Artigo 17": "presidia a sessão (art. 17 do RICD: o presidente da Câmara não vota)",
}

#: Linhas por votação a partir das quais uma votação do Senado está completa:
#: a API lista todos os senadores em exercício (81, ou 80 com cadeira vaga).
#: Medido depois de gravar as ausências: 585 votações com 81, 28 com 80 e 3 de
#: abril de 2019 com 52–68 linhas (incompletas na própria API).
MIN_LINHAS_VOTACAO_SENADO_COMPLETA = 80

AVISO_LEITURA_DA_DESCRICAO = (
    "Leia a descrição de CADA votação antes de concluir posição: só votação sobre o TEXTO "
    "(texto-base, substitutivo, redação final, turno de PEC) autoriza dizer que alguém foi a "
    "favor ou contra a proposição. Emenda, destaque e DVS valem só para aquele trecho. "
    "Requerimento de urgência, adiamento e questão de ordem são rito. 'Mantido o texto' e "
    "'Suprimido o texto' são RESULTADOS DE DESTAQUE, não votação do texto inteiro, e o que o Sim "
    "significa varia: compare o resultado oficial com `sim_superou_nao` e `quorum_qualificado` "
    "antes de dizer se o voto foi por manter ou por retirar o trecho."
)

#: Quórum para APROVAR (ou manter trecho de) PEC e PLP. Nesses tipos o trecho
#: destacado pode cair mesmo com mais Sim que Não: medido na base, as 10
#: votações "Suprimido o texto" com Sim > Não são todas de PEC ou PLP.
QUORUM_QUALIFICADO = {
    "PEC": "3/5 dos membros, em dois turnos: 308 deputados ou 49 senadores",
    "PLP": "maioria absoluta: 257 deputados ou 41 senadores",
}


def _lotes(valores: list[Any]) -> Iterable[list[Any]]:
    for i in range(0, len(valores), LOTE):
        yield valores[i : i + LOTE]


# ---------------------------------------------------------------------------
# consultar_votos
# ---------------------------------------------------------------------------


def _formatar_voto(r: sqlite3.Row) -> dict[str, Any]:
    tipo = r["tipo_voto"]
    base = {
        "id_proposicao": r["id_proposicao"],
        "proposicao": rotulo_proposicao(r["sigla_tipo"], r["numero"], r["ano"]),
        "id_votacao": r["id_votacao"],
        "data": r["data_voto"],
        "votacao": r["descricao_votacao"],
    }
    if tipo in VOTOS_SEM_POSICIONAMENTO:
        codigo = r["tipo_voto_original"]
        motivo = MOTIVOS_SENADO.get(codigo)
        return {
            **base,
            "status": "AUSENTE",
            "registro_oficial": tipo,
            **({"codigo_oficial": codigo, "motivo_oficial": motivo} if motivo else {}),
            "observacao": (
                f"Registro oficial '{tipo}'{f' ({motivo})' if motivo else ''}: o parlamentar consta na lista "
                "oficial desta votação e não expressou posição nela. Isso NÃO indica posicionamento contrário."
            ),
        }
    return {
        **base,
        "status": "VOTOU",
        "voto": tipo,
        "partido_na_epoca": r["partido_voto"],
    }


def _consolidar(linhas: list[sqlite3.Row], total_votacoes: int) -> dict[str, Any]:
    """
    Todas as votações de uma proposição em que o parlamentar registrou voto.

    Com mais de uma, NÃO há campo `voto` raso: eleger a votação "principal" é
    juízo legislativo. No PL 10.372/2018 a escolha automática afirmou "Não" no
    mérito para quem votou Sim no substitutivo e Não num destaque.
    """
    formatados = sorted(
        (_formatar_voto(r) for r in linhas), key=lambda v: str(v.get("data") or "")
    )
    if len(formatados) == 1:
        unico = dict(formatados[0])
        unico["total_de_votacoes_com_voto"] = 1
        unico["total_de_votacoes_da_proposicao"] = total_votacoes
        if total_votacoes > 1:
            unico["aviso_sistema"] = (
                f"Esta proposição teve {total_votacoes} votações nominais e o parlamentar registrou "
                "voto em apenas UMA delas, a descrita em `votacao`. O voto vale para o objeto "
                "daquela votação e para mais nada: leia a descrição antes de dizer que ele apoiou "
                "ou rejeitou a proposição."
            )
        return unico

    if all(v["status"] == "AUSENTE" for v in formatados):
        return {
            "id_proposicao": formatados[0]["id_proposicao"],
            "proposicao": formatados[0]["proposicao"],
            "status": "AUSENTE",
            "total_de_votacoes_da_proposicao": total_votacoes,
            "votacoes": formatados,
            "observacao": (
                f"O parlamentar consta na lista oficial de {len(formatados)} votação(ões) desta proposição "
                "com registro sem posição (motivo em cada item de `votacoes`). Isso NÃO indica posicionamento."
            ),
        }

    posicoes = sorted({v["voto"] for v in formatados if v.get("voto")})
    avisos = [
        f"Esta proposição teve {total_votacoes} votação(ões) nominal(is) e o parlamentar votou em "
        f"{len(formatados)}. NÃO existe 'o voto dele nesta proposição' — existe um voto por "
        "votação, e cada votação teve objeto próprio (o texto, um substitutivo, uma emenda, um "
        "destaque, um requerimento de rito). Por isso não há campo `voto` aqui: leia o campo "
        "`votacao` de CADA item de `votacoes` e relate votação por votação."
    ]
    if len(posicoes) > 1:
        avisos.append(
            f"ATENÇÃO: o parlamentar não votou sempre igual nesta proposição ({', '.join(posicoes)}). "
            "É proibido resumir num voto só — diga em que votação votou o quê, nomeando o objeto de cada uma."
        )
    return {
        "id_proposicao": formatados[0]["id_proposicao"],
        "proposicao": formatados[0]["proposicao"],
        "status": "VOTOU_EM_VARIAS_VOTACOES",
        "total_de_votacoes_com_voto": len(formatados),
        "total_de_votacoes_da_proposicao": total_votacoes,
        "votacoes": formatados,
        "aviso_sistema": " ".join(avisos),
    }


@lru_cache(maxsize=1)
def siglas_com_votos() -> frozenset[str]:
    """Siglas (maiúsculas) presentes em `votos.partido_voto`. O banco é somente leitura."""
    with conexao() as conn:
        return frozenset(
            r[0].upper()
            for r in conn.execute("SELECT DISTINCT partido_voto FROM votos")
            if r[0]
        )


def parlamentar_existe(id_parlamentar: int) -> bool:
    with conexao() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM parlamentares WHERE id_parlamentar = ?", (id_parlamentar,)
            ).fetchone()
            is not None
        )


_SELECT_VOTOS = """
    SELECT v.id_proposicao, v.tipo_voto, v.tipo_voto_original, v.id_votacao, v.partido_voto,
           v.data_voto, v.descricao_votacao, p.sigla_tipo, p.numero, p.ano
    FROM votos v JOIN proposicoes p ON p.id_proposicao = v.id_proposicao
"""


def status_por_proposicao(
    id_parlamentar: int, ids_alvo: list[int]
) -> list[dict[str, Any]]:
    """
    Status tipado de UM parlamentar em cada proposição, na ordem pedida.

    O chamador garante que o parlamentar existe (`parlamentar_existe`).
    """
    votos_do_parlamentar: dict[int, list[sqlite3.Row]] = {}
    votacoes_por_proposicao: dict[int, int] = {}
    casas_das_votacoes: dict[int, set[str]] = {}
    metadados: dict[int, sqlite3.Row] = {}
    #: proposição -> [(dia, linhas da votação)] das votações na casa do parlamentar
    votacoes_na_casa: dict[int, list[tuple[str, int]]] = {}
    with conexao(row_factory=True) as conn:
        linha_casa = conn.execute(
            "SELECT casa FROM parlamentares WHERE id_parlamentar = ?", (id_parlamentar,)
        ).fetchone()
        casa_do_parlamentar = linha_casa["casa"] if linha_casa else None
        # Dias em que ele tem QUALQUER linha na casa (voto ou ausência registrada):
        # prova de exercício que prevalece sobre o histórico.
        dias_com_registro = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT substr(data_voto, 1, 10) FROM votos WHERE id_parlamentar = ? AND casa = ?",
                (id_parlamentar, casa_do_parlamentar),
            )
        }
        for lote in _lotes(ids_alvo):
            marcadores = ",".join("?" * len(lote))
            for r in conn.execute(
                _SELECT_VOTOS
                + f" WHERE v.id_parlamentar = ? AND v.id_proposicao IN ({marcadores}) ORDER BY v.data_voto",
                (id_parlamentar, *lote),
            ):
                votos_do_parlamentar.setdefault(r["id_proposicao"], []).append(r)
            for r in conn.execute(
                f"SELECT id_proposicao, casa, COUNT(DISTINCT id_votacao) AS n FROM votos "
                f"WHERE id_proposicao IN ({marcadores}) GROUP BY id_proposicao, casa",
                lote,
            ):
                pid = r["id_proposicao"]
                votacoes_por_proposicao[pid] = votacoes_por_proposicao.get(pid, 0) + r["n"]
                casas_das_votacoes.setdefault(pid, set()).add(r["casa"])
            for r in conn.execute(
                f"SELECT id_proposicao, id_votacao, substr(MAX(data_voto), 1, 10) AS dia, COUNT(*) AS n "
                f"FROM votos WHERE casa = ? AND id_proposicao IN ({marcadores}) "
                "GROUP BY id_proposicao, id_votacao",
                (casa_do_parlamentar, *lote),
            ):
                votacoes_na_casa.setdefault(r["id_proposicao"], []).append((r["dia"], r["n"]))
            for r in conn.execute(
                f"SELECT id_proposicao, sigla_tipo, numero, ano, casa FROM proposicoes WHERE id_proposicao IN ({marcadores})",
                lote,
            ):
                metadados[r["id_proposicao"]] = r

    cobertura = cobertura_por_casa()
    resultados: list[dict[str, Any]] = []
    for pid in ids_alvo:
        meta = metadados.get(pid)
        rotulo = (
            rotulo_proposicao(meta["sigla_tipo"], meta["numero"], meta["ano"])
            if meta
            else str(pid)
        )

        if pid in votos_do_parlamentar:
            resultados.append(
                _consolidar(
                    votos_do_parlamentar[pid], votacoes_por_proposicao.get(pid, 0)
                )
            )
            continue
        casas = casas_das_votacoes.get(pid, set())
        if pid in votacoes_por_proposicao and casa_do_parlamentar not in casas:
            resultados.append(
                {
                    "id_proposicao": pid,
                    "proposicao": rotulo,
                    "status": "OUTRA_CASA",
                    "casa_das_votacoes": sorted(casas),
                    "casa_do_parlamentar": casa_do_parlamentar,
                    "observacao": (
                        f"As votações nominais desta proposição são da casa {', '.join(sorted(casas))}, e este "
                        f"parlamentar é da casa {casa_do_parlamentar}: ele não tinha como votar aqui. Isso "
                        "NÃO é ausência nem posicionamento. Se a matéria tramitou nas duas casas, procure o "
                        "registro dela na casa do parlamentar (`buscar_proposicao` pela sigla, número e ano)."
                    ),
                }
            )
            continue
        if pid in votacoes_por_proposicao:
            votacoes_dele = votacoes_na_casa.get(pid, [])

            def fora(dia: str, linhas: int) -> bool:
                if dia in dias_com_registro:
                    return False
                if casa_do_parlamentar == "Câmara":
                    return exercicio.fora_de_exercicio_no_dia(id_parlamentar, dia)
                return linhas >= MIN_LINHAS_VOTACAO_SENADO_COMPLETA

            n_fora = sum(1 for dia, linhas in votacoes_dele if fora(dia, linhas))
            if votacoes_dele and n_fora == len(votacoes_dele):
                fonte = (
                    "o histórico oficial de exercício da Câmara (licença, suplência, fim de mandato ou "
                    "ainda sem posse)"
                    if casa_do_parlamentar == "Câmara"
                    else "a lista oficial do Senado, que registra todos os senadores em exercício em cada "
                    "votação, e ele não consta"
                )
                resultados.append(
                    {
                        "id_proposicao": pid,
                        "proposicao": rotulo,
                        "status": "FORA_DE_EXERCICIO",
                        "observacao": (
                            f"Nas datas das votações nominais desta proposição o parlamentar não estava em "
                            f"exercício, segundo {fonte}. Ele não tinha como votar. Isso NÃO é ausência nem "
                            "posicionamento."
                        ),
                    }
                )
                continue
            resultados.append(
                {
                    "id_proposicao": pid,
                    "proposicao": rotulo,
                    "status": "AUSENTE",
                    "observacao": (
                        "Houve votação nominal nesta proposição na casa do parlamentar, mas ele não registrou "
                        "voto. Isso NÃO indica posicionamento contrário."
                        + (
                            f" Em {n_fora} das {len(votacoes_dele)} votações ele não estava em exercício."
                            if n_fora
                            else ""
                        )
                        + (
                            " Não há registro que permita afirmar se estava em exercício."
                            if casa_do_parlamentar == "Câmara" and not exercicio.tem_historico(id_parlamentar)
                            else ""
                        )
                    ),
                }
            )
            continue

        # Sem votação nominal: simbólica ou lacuna de cobertura. Só é lícito
        # dizer "foi simbólica" se a casa e o período estiverem cobertos.
        casa = meta["casa"] if meta else None
        casa_sem_dados = cobertura.get(casa, 0) < MIN_VOTACOES_PARA_COBERTURA
        fora_do_periodo = bool(meta) and (
            not meta["ano"] or meta["ano"] < ANO_INICIAL_VOTOS
        )
        if meta is None or casa_sem_dados or fora_do_periodo:
            motivo = (
                "Esta proposição não consta na base."
                if meta is None
                else f"Não há votações nominais ingeridas para a casa '{casa}' nesta base."
                if casa_sem_dados
                else f"A base de votações nominais cobre de {ANO_INICIAL_VOTOS} em diante; esta proposição está fora desse recorte."
            )
            resultados.append(
                {
                    "id_proposicao": pid,
                    "proposicao": rotulo,
                    "status": "FORA_DA_BASE",
                    "observacao": f"{motivo} Não é possível afirmar como o parlamentar votou, nem concluir que a votação foi simbólica.",
                }
            )
        else:
            resultados.append(
                {
                    "id_proposicao": pid,
                    "proposicao": rotulo,
                    "status": "SEM_VOTACAO_NOMINAL",
                    "observacao": (
                        "Nenhuma votação nominal foi registrada para esta proposição — tipicamente porque "
                        "foi apreciada de forma simbólica. NENHUM parlamentar possui voto individual registrado nela."
                    ),
                }
            )
    return resultados


def consultar_votos(
    id_parlamentar: int,
    lista_ids: Optional[list[int]] = None,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    id_parl = int(id_parlamentar)
    ids_alvo = ids_inteiros(lista_ids)
    if not parlamentar_existe(id_parl):
        return {
            "erro": "PARLAMENTAR_NAO_ENCONTRADO",
            "id_parlamentar": id_parl,
            "observacao": (
                "Não há parlamentar com este ID na base; nenhum voto foi consultado. Use "
                "`buscar_id_parlamentar` para obter o ID. NÃO trate isto como ausência."
            ),
        }

    # Lista FORNECIDA sem nenhum ID válido é erro de chamada. Antes caía no
    # caminho "histórico completo" e despejava centenas de votos justamente
    # quando o modelo se confundiu.
    if lista_ids is not None and not ids_alvo:
        return {
            "erro": "LISTA_IDS_SEM_ID_VALIDO",
            "recebido": lista_ids,
            "observacao": (
                "A lista de proposições foi fornecida, mas nenhum item é um ID numérico válido. "
                "Descubra os IDs primeiro (via `buscar_proposicao` ou `busca_semantica_proposicoes`) "
                "e chame novamente. Nenhum voto foi consultado."
            ),
        }

    if ids_alvo:
        todos = status_por_proposicao(id_parl, ids_alvo)
        # Resumo de TODAS as proposições: o status e os votos distintos de cada
        # uma cabem em pouco espaço e bastam para não concluir errado mesmo sem
        # ler as páginas seguintes; o detalhe (descrição de cada votação) é
        # paginado.
        resumo = [
            {
                "id_proposicao": r["id_proposicao"],
                "proposicao": r["proposicao"],
                "status": r["status"],
                "votos_registrados": sorted(
                    {v["voto"] for v in r.get("votacoes", []) if v.get("voto")}
                    | ({r["voto"]} if r.get("voto") else set())
                ),
            }
            for r in todos
        ]
        itens, paginacao = paginar(
            todos,
            pagina,
            reserva=len(str(resumo)) + 3_000,
            resumo="O campo `resumo_por_proposicao`",
        )
        saida_lista: dict[str, Any] = {
            "paginacao": paginacao,
            "id_parlamentar": id_parl,
            "total_proposicoes": len(todos),
            "contagem_por_status": {
                s: sum(1 for r in todos if r["status"] == s)
                for s in sorted({r["status"] for r in todos})
            },
        }
        if not paginacao["completo"]:
            saida_lista["resumo_por_proposicao"] = resumo
        saida_lista["resultados"] = itens
        if paginacao.get("aviso"):
            saida_lista["aviso_sistema"] = paginacao["aviso"]
        return saida_lista

    # Sem lista: histórico completo, do mais recente para o mais antigo, em
    # páginas — nenhum voto é omitido, e o retrato do total vai junto.
    with conexao(row_factory=True) as conn:
        linhas = conn.execute(
            _SELECT_VOTOS
            + " WHERE v.id_parlamentar = ? ORDER BY v.data_voto DESC, v.id_votacao",
            (id_parl,),
        ).fetchall()
        placar = {
            r["tipo_voto"]: r["n"]
            for r in conn.execute(
                "SELECT tipo_voto, COUNT(*) AS n FROM votos WHERE id_parlamentar = ? GROUP BY tipo_voto ORDER BY n DESC",
                (id_parl,),
            )
        }
    itens, paginacao = paginar(
        [_formatar_voto(r) for r in linhas],
        pagina,
        reserva=3_000,
        resumo="O campo `placar_sobre_o_total`",
    )
    datas = [str(r["data_voto"] or "")[:10] for r in linhas]
    saida: dict[str, Any] = {
        "paginacao": paginacao,
        "id_parlamentar": id_parl,
        "total_de_votos_na_base": len(linhas),
        "periodo_completo": f"{min(datas)} a {max(datas)}" if datas else None,
        "placar_sobre_o_total": placar,
        "resultados": itens,
    }
    if paginacao.get("aviso"):
        saida["aviso_sistema"] = (
            paginacao["aviso"]
            + " Os votos vêm do mais recente para o mais antigo. Para verificar uma proposição "
            "específica, passe `lista_ids` com o ID dela: a consulta cobre todo o histórico."
        )
    if not linhas:
        saida["aviso_sistema"] = (
            "Nenhum voto nominal registrado para este parlamentar na base. Isso não indica "
            "posicionamento: pode não ter exercido mandato no período coberto."
        )
    return saida


# ---------------------------------------------------------------------------
# placar_por_votacao
# ---------------------------------------------------------------------------


def placar_por_votacao(
    lista_ids: list[int],
    partido: Optional[str] = None,
    uf: Optional[str] = None,
    listar_parlamentares: bool = False,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    """
    Placar de cada votação nominal das proposições, nunca somado entre votações.

    Substitui o SQL que o modelo escrevia à mão para perguntas de bancada. Esse
    SQL já produziu "1.432 Sim x 1.794 Não" para o PL 3626/2023 somando oito
    votações diferentes — um placar que não corresponde a decisão nenhuma.
    """
    ids = ids_inteiros(lista_ids) or []
    if not ids:
        return {"erro": "LISTA_IDS_SEM_ID_VALIDO", "recebido": lista_ids}

    partido_n = partidos.legenda(partido) if partido and partido.strip() else None
    siglas = partidos.siglas_da_legenda(partido_n) if partido_n else ()
    if partido_n and not set(siglas) & siglas_com_votos():
        return {
            "erro": "PARTIDO_SEM_VOTOS",
            "recebido": partido,
            "siglas_na_base": sorted(siglas_com_votos()),
            "observacao": (
                "Nenhum voto na base foi dado sob esta sigla. Confira a grafia entre `siglas_na_base`; "
                "NÃO conclua que a bancada não votou."
            ),
        }
    uf_n = uf.strip().upper() if uf else None
    filtros = ""
    extra: list[Any] = []
    if partido_n:
        filtros += f" AND UPPER(v.partido_voto) IN ({','.join('?' * len(siglas))})"
        extra.extend(siglas)
    if uf_n:
        filtros += " AND UPPER(v.uf_voto) = ?"
        extra.append(uf_n)

    votacoes: list[sqlite3.Row] = []
    placar: dict[str, dict[str, int]] = {}
    placar_total: dict[str, dict[str, int]] = {}
    nomes: dict[str, dict[str, list[str]]] = {}
    with conexao(row_factory=True) as conn:
        for lote in _lotes(ids):
            marcadores = ",".join("?" * len(lote))
            votacoes += conn.execute(
                f"""
                SELECT vt.id_votacao, vt.id_proposicao, vt.data, vt.descricao, vt.casa,
                       p.sigla_tipo, p.numero, p.ano
                FROM votacoes vt JOIN proposicoes p ON p.id_proposicao = vt.id_proposicao
                WHERE vt.id_proposicao IN ({marcadores})
                """,
                lote,
            ).fetchall()
            for r in conn.execute(
                f"SELECT v.id_votacao, v.tipo_voto, COUNT(*) AS n FROM votos v "
                f"WHERE v.id_proposicao IN ({marcadores}) {filtros} GROUP BY v.id_votacao, v.tipo_voto",
                [*lote, *extra],
            ):
                placar.setdefault(r["id_votacao"], {})[r["tipo_voto"]] = r["n"]
            if filtros:
                for r in conn.execute(
                    f"SELECT v.id_votacao, v.tipo_voto, COUNT(*) AS n FROM votos v "
                    f"WHERE v.id_proposicao IN ({marcadores}) GROUP BY v.id_votacao, v.tipo_voto",
                    lote,
                ):
                    placar_total.setdefault(r["id_votacao"], {})[r["tipo_voto"]] = r["n"]
            if listar_parlamentares:
                for r in conn.execute(
                    f"""
                    SELECT v.id_votacao, v.tipo_voto, pa.nome, v.partido_voto, v.uf_voto
                    FROM votos v JOIN parlamentares pa ON pa.id_parlamentar = v.id_parlamentar
                    WHERE v.id_proposicao IN ({marcadores}) {filtros}
                    ORDER BY pa.nome
                    """,
                    [*lote, *extra],
                ):
                    nomes.setdefault(r["id_votacao"], {}).setdefault(
                        r["tipo_voto"], []
                    ).append(f"{r['nome']} ({r['partido_voto']}-{r['uf_voto']})")

    ordem = {pid: i for i, pid in enumerate(ids)}
    votacoes.sort(
        key=lambda vt: (
            ordem.get(vt["id_proposicao"], 0),
            str(vt["data"] or ""),
            vt["id_votacao"],
        )
    )

    # Os placares NUMÉRICOS de todas as votações vão sempre completos, em toda
    # página: são pequenos e são o que responde "como votou a bancada". O que
    # pagina são as listas de NOMES, que podem somar centenas de parlamentares
    # por votação. Assim uma resposta que pare na página 1 continua com os
    # números certos.
    placares: list[dict[str, Any]] = []
    listas_de_nomes: list[dict[str, Any]] = []
    for vt in votacoes:
        contagem = placar.get(vt["id_votacao"], {})
        com_posicao = {
            k: n for k, n in contagem.items() if k not in VOTOS_SEM_POSICIONAMENTO
        }
        sem_posicao = {
            k: n for k, n in contagem.items() if k in VOTOS_SEM_POSICIONAMENTO
        }
        placares.append(
            {
                "id_proposicao": vt["id_proposicao"],
                "proposicao": rotulo_proposicao(
                    vt["sigla_tipo"], vt["numero"], vt["ano"]
                ),
                "id_votacao": vt["id_votacao"],
                "data": vt["data"],
                "casa": vt["casa"],
                "descricao": vt["descricao"],
                "placar": dict(sorted(com_posicao.items(), key=lambda x: -x[1])),
                "sem_posicao_registrada": sem_posicao,
                "total_com_posicao": sum(com_posicao.values()),
                # Do plenário inteiro, mesmo com filtro de partido/UF: é o que
                # se compara com o resultado oficial da descrição.
                "sim_superou_nao": (
                    (placar_total if filtros else placar).get(vt["id_votacao"], {}).get("Sim", 0)
                    > (placar_total if filtros else placar).get(vt["id_votacao"], {}).get("Não", 0)
                ),
                "quorum_qualificado": QUORUM_QUALIFICADO.get(vt["sigla_tipo"]),
            }
        )
        if listar_parlamentares:
            for tipo, lista in sorted(nomes.get(vt["id_votacao"], {}).items()):
                listas_de_nomes.append(
                    {
                        "id_votacao": vt["id_votacao"],
                        "proposicao": rotulo_proposicao(
                            vt["sigla_tipo"], vt["numero"], vt["ano"]
                        ),
                        "voto": tipo,
                        "total": len(lista),
                        "parlamentares": lista,
                    }
                )

    com_votacao = {vt["id_proposicao"] for vt in votacoes}
    sem_votacao = [pid for pid in ids if pid not in com_votacao]
    if listar_parlamentares:
        pagina_nomes, paginacao = paginar(
            listas_de_nomes,
            pagina,
            reserva=len(str(placares)) + 4_000,
            resumo="O campo `votacoes` (placares numéricos)",
        )
        pagina_placares = placares
    else:
        pagina_placares, paginacao = paginar(placares, pagina, reserva=4_000)
        pagina_nomes = []

    avisos = [
        "Cada item de `votacoes` é UMA votação, com placar próprio. Nunca some placares de "
        "votações diferentes num placar só. " + AVISO_LEITURA_DA_DESCRICAO,
        "`sim_superou_nao` compara Sim e Não do plenário inteiro (sem o filtro de partido/UF). Se "
        "ele contradiz o resultado oficial da descrição, há três explicações possíveis, e é a "
        "descrição que diz qual vale: (1) quórum qualificado não atingido (`quorum_qualificado`, "
        "PEC e PLP): o trecho cai mesmo com mais Sim; (2) a votação era de uma EMENDA, e Sim "
        "aprovava a emenda (ex.: 'Mantido o texto aprovado pela Câmara. Rejeitada a Emenda', "
        "Sim 136 × Não 174: Sim era por alterar o texto); (3) a pergunta foi formulada ao "
        "contrário. Na dúvida, relate a descrição e o placar sem traduzir o voto em 'manter' ou "
        "'retirar'.",
    ]
    if partido_n and len(siglas) > 1:
        avisos.append(
            f"A legenda {partido_n} aparece na base com as siglas {', '.join(siglas)} (grafia de cada casa "
            "ou renomeação do mesmo partido), e todas foram consideradas (`siglas_consideradas`)."
        )
    if partido_n and partidos.aviso_de_sucessao(partido_n):
        avisos.append(
            partidos.aviso_de_sucessao(partido_n)
            + " Fusão e incorporação NÃO são somadas: votos dados sob as outras siglas não entram neste "
            "placar. Consulte-as à parte se a pergunta abranger o período anterior."
        )
    if partido_n or uf_n:
        avisos.append(
            "O filtro usa o partido e a UF NA DATA DO VOTO (`partido_voto`, `uf_voto`), não os "
            "atuais. Informe o placar e o total do recorte que votou, para mostrar se houve "
            "unanimidade ou divisão. Placar vazio numa votação significa que ninguém do recorte "
            "votou nela."
        )
    if sem_votacao:
        avisos.append(
            f"{len(sem_votacao)} proposição(ões) sem votação nominal na base: nenhum parlamentar "
            "tem voto individual nelas."
        )
    if paginacao.get("aviso"):
        avisos.insert(0, paginacao["aviso"])
    saida: dict[str, Any] = {
        "paginacao": paginacao,
        "filtros": {
            "partido": partido_n,
            "siglas_consideradas": list(siglas) if partido_n else None,
            "uf": uf_n,
        },
        "total_votacoes": len(placares),
        "aviso_sistema": " ".join(avisos),
        "votacoes": pagina_placares,
        "proposicoes_sem_votacao_nominal": sem_votacao,
    }
    if listar_parlamentares:
        saida["nomes_por_voto"] = pagina_nomes
    return saida


# ---------------------------------------------------------------------------
# posicao_consolidada
# ---------------------------------------------------------------------------


def posicao_consolidada(
    ids_parlamentares: list[int],
    lista_ids: list[int],
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    """
    O voto de cada parlamentar em cada votação de cada proposição, lado a lado.

    Existe para "quem se alinha comigo sobre o tema X": um tema tem várias
    proposições, e votar a favor de uma não é votar a favor da outra. Caso que
    motivou: senadores que votaram Sim no PL 2234/2022 e Não no PL 3626/2023
    foram apresentados como "favoráveis a bets". Aqui a divergência entre
    proposições aparece como fato, sem o código julgar qual votação é de mérito.
    """
    parlamentares = ids_inteiros(ids_parlamentares) or []
    ids = ids_inteiros(lista_ids) or []
    if not parlamentares or not ids:
        return {
            "erro": "LISTAS_VAZIAS",
            "observacao": "Informe IDs de parlamentares e de proposições.",
        }

    pessoas: dict[int, sqlite3.Row] = {}
    por_pessoa: dict[int, dict[int, list[sqlite3.Row]]] = {}
    com_votacao: set[int] = set()
    with conexao(row_factory=True) as conn:
        for lote_p in _lotes(parlamentares):
            mp = ",".join("?" * len(lote_p))
            for r in conn.execute(
                f"SELECT id_parlamentar, nome, partido, uf, casa FROM parlamentares WHERE id_parlamentar IN ({mp})",
                lote_p,
            ):
                pessoas[r["id_parlamentar"]] = r
            for lote_i in _lotes(ids):
                mi = ",".join("?" * len(lote_i))
                for r in conn.execute(
                    f"""
                    SELECT v.id_parlamentar, v.id_proposicao, v.id_votacao, v.tipo_voto,
                           v.partido_voto, v.data_voto, vt.descricao, p.sigla_tipo, p.numero, p.ano
                    FROM votos v
                    JOIN votacoes vt ON vt.id_votacao = v.id_votacao
                    JOIN proposicoes p ON p.id_proposicao = v.id_proposicao
                    WHERE v.id_parlamentar IN ({mp}) AND v.id_proposicao IN ({mi})
                    ORDER BY v.data_voto, v.id_votacao
                    """,
                    (*lote_p, *lote_i),
                ):
                    por_pessoa.setdefault(r["id_parlamentar"], {}).setdefault(
                        r["id_proposicao"], []
                    ).append(r)
        for lote_i in _lotes(ids):
            mi = ",".join("?" * len(lote_i))
            com_votacao |= {
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT id_proposicao FROM votacoes WHERE id_proposicao IN ({mi})",
                    lote_i,
                )
            }

    ordem = {pid: i for i, pid in enumerate(ids)}
    resultado = []
    for pid_parl in parlamentares:
        pessoa = pessoas.get(pid_parl)
        if pessoa is None:
            resultado.append(
                {"id_parlamentar": pid_parl, "erro": "PARLAMENTAR_NAO_ENCONTRADO"}
            )
            continue
        proposicoes = []
        tipos_no_conjunto: set[str] = set()
        votos_da_pessoa = por_pessoa.get(pid_parl, {})
        for id_prop in sorted(votos_da_pessoa, key=lambda i: ordem.get(i, 0)):
            linhas = votos_da_pessoa[id_prop]
            tipos = sorted(
                {
                    v["tipo_voto"]
                    for v in linhas
                    if v["tipo_voto"] not in VOTOS_SEM_POSICIONAMENTO
                }
            )
            if not tipos:
                # Só registros sem posição (ausência oficial): não votou.
                continue
            tipos_no_conjunto.update(tipos)
            proposicoes.append(
                {
                    "id_proposicao": id_prop,
                    "proposicao": rotulo_proposicao(
                        linhas[0]["sigla_tipo"], linhas[0]["numero"], linhas[0]["ano"]
                    ),
                    "votacoes": [
                        {
                            "id_votacao": v["id_votacao"],
                            "data": v["data_voto"],
                            "descricao": v["descricao"],
                            "voto": v["tipo_voto"],
                            "partido_na_epoca": v["partido_voto"],
                        }
                        for v in linhas
                    ],
                    "tipos_de_voto_com_posicao": tipos,
                    "votou_igual_em_todas_as_votacoes": len(tipos) <= 1,
                }
            )
        resultado.append(
            {
                "id_parlamentar": pid_parl,
                "nome": pessoa["nome"],
                "partido_atual": pessoa["partido"],
                "uf": pessoa["uf"],
                "casa": pessoa["casa"],
                "proposicoes": proposicoes,
                "tipos_de_voto_no_conjunto": sorted(tipos_no_conjunto),
                "votou_de_formas_diferentes_no_conjunto": len(tipos_no_conjunto) > 1,
                "proposicoes_com_votacao_em_que_nao_votou": [
                    i
                    for i in ids
                    if i in com_votacao
                    and i not in {p["id_proposicao"] for p in proposicoes}
                ],
            }
        )

    resumo = [
        {
            "id_parlamentar": p["id_parlamentar"],
            "nome": p.get("nome"),
            "partido_atual": p.get("partido_atual"),
            "uf": p.get("uf"),
            "tipos_de_voto_por_proposicao": {
                prop["proposicao"]: prop["tipos_de_voto_com_posicao"]
                for prop in p.get("proposicoes", [])
            },
            "votou_de_formas_diferentes_no_conjunto": p.get(
                "votou_de_formas_diferentes_no_conjunto"
            ),
            "erro": p.get("erro"),
        }
        for p in resultado
    ]
    itens, paginacao = paginar(
        resultado,
        pagina,
        reserva=len(str(resumo)) + 4_000,
        resumo="O campo `resumo_por_parlamentar`",
    )
    saida: dict[str, Any] = {
        "paginacao": paginacao,
        "total_parlamentares": len(resultado),
    }
    if not paginacao["completo"]:
        saida["resumo_por_parlamentar"] = resumo
    return {
        **saida,
        "parlamentares": itens,
        "proposicoes_sem_votacao_nominal": [i for i in ids if i not in com_votacao],
        "aviso_sistema": (
            (paginacao["aviso"] + " " if paginacao.get("aviso") else "")
            + "`votou_igual_em_todas_as_votacoes` e `votou_de_formas_diferentes_no_conjunto` são FATOS "
            "sobre os registros, não julgamento de coerência: votar Sim na urgência e Não no texto é "
            "comum. Antes de rotular alguém, identifique pela descrição quais votações são sobre o "
            "TEXTO de cada proposição e compare só essas. Quem votou a favor de uma proposição e "
            "contra outra do mesmo tema é DIVIDIDO: diga em qual foi a favor e em qual foi contra, "
            "com o que cada uma pede. " + AVISO_LEITURA_DA_DESCRICAO
        ),
    }
