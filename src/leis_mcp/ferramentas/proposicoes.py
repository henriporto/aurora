"""Localização, detalhes e busca de proposições (ementas e inteiro teor)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from leis_mcp.config import obter_config
from leis_mcp.dados.banco import conexao
from leis_mcp.dados import sob_demanda
from leis_mcp.dados.buscador import obter_buscador
from leis_mcp.dados.cobertura import ids_votados, tipos_deliberaveis
from leis_mcp.paginacao import cache, chave_de, paginar
from leis_mcp.texto import ano_da_data, ementa_sem_profissoes, ids_inteiros, normalizar_casa

logger = logging.getLogger(__name__)

#: Proposições sem votação nominal que acompanham uma busca `somente_votadas`.
MAX_APENDICE_SEM_VOTACAO = 5

#: Trechos de inteiro teor anexados a cada proposição da busca de ementas.
MAX_TRECHOS_POR_PROPOSICAO = 3

#: Quantas proposições do topo recebem trechos de inteiro teor quando a busca
#: nos trechos não as trouxe.
TOP_ENRIQUECIDAS = 10

LOTE = 900

#: Mesmo k do RRF do buscador.
K_RRF = 60.0

#: Proposições que cada lista (ementas, trechos) leva à fusão por termo.
CANDIDATOS_POR_LISTA = 200

#: Trechos pedidos por proposição candidata, para a agregação por proposição.
TRECHOS_POR_PROPOSICAO_CANDIDATA = 5

#: Faixas de `destaque_semantico` (ver `_confianca`).
LIMIAR_DESTAQUE_ALTO = 2.0
LIMIAR_DESTAQUE_MODERADO = 1.5


def _rotulo_autor(nome: str, partido: Optional[str], uf: Optional[str]) -> str:
    detalhe = "-".join(x for x in (partido, uf) if x)
    return f"{nome} ({detalhe})" if detalhe else nome


def _autores(conn, ids: list[int]) -> dict[int, str]:
    """
    Autores de cada proposição, na ordem oficial.

    Vale a autoria estruturada (`autores_proposicao`, fonte 'documento') quando
    existe; nas demais, os parlamentares de `autoria`.
    """
    saida: dict[int, list[str]] = {}
    for i in range(0, len(ids), LOTE):
        lote = ids[i : i + LOTE]
        marcadores = ",".join("?" * len(lote))
        for pid, nome, partido, uf in conn.execute(
            f"""
            SELECT id_proposicao, nome, partido, uf FROM autores_proposicao
            WHERE fonte = 'documento' AND id_proposicao IN ({marcadores})
            ORDER BY id_proposicao, ordem
            """,
            lote,
        ):
            saida.setdefault(pid, []).append(_rotulo_autor(nome, partido, uf))
        estruturadas = set(saida)
        for pid, nome, partido, uf in conn.execute(
            f"""
            SELECT a.id_proposicao, pa.nome, pa.partido, pa.uf
            FROM autoria a JOIN parlamentares pa ON pa.id_parlamentar = a.id_parlamentar
            WHERE a.id_proposicao IN ({marcadores})
            """,
            lote,
        ):
            if pid not in estruturadas:
                saida.setdefault(pid, []).append(_rotulo_autor(nome, partido, uf))
    return {pid: ", ".join(nomes) for pid, nomes in saida.items()}


def _limpar_ementa(ementa: Optional[str]) -> str:
    if not ementa:
        return ""
    return ementa_sem_profissoes(ementa)


# ---------------------------------------------------------------------------
# buscar_proposicao / obter_detalhes_proposicoes
# ---------------------------------------------------------------------------

_SELECT_PROPOSICAO = "SELECT id_proposicao, sigla_tipo, numero, ano, ementa, data_apresentacao, casa, url_inteiro_teor FROM proposicoes"


SEM_AUTORIA = "Autoria não registrada na base"


def _detalhar(conn, linhas) -> list[dict[str, Any]]:
    ids = [r["id_proposicao"] for r in linhas]
    autores = _autores(conn, ids)
    votacoes: dict[int, int] = {}
    trechos: dict[int, int] = {}
    equivalentes: dict[int, list[dict[str, Any]]] = {}
    identificacoes: dict[int, list[str]] = {}
    iniciativa: dict[int, list[str]] = {}
    for i in range(0, len(ids), LOTE):
        lote = ids[i : i + LOTE]
        marcadores = ",".join("?" * len(lote))
        # Mesma matéria na outra casa, declarada pelo Senado (`outrosNumeros`).
        for pid, outro, sigla, numero, ano, casa in conn.execute(
            f"""
            SELECT e.id_camara, p.id_proposicao, p.sigla_tipo, p.numero, p.ano, p.casa
            FROM proposicoes_equivalentes e JOIN proposicoes p ON p.id_proposicao = e.id_senado
            WHERE e.id_camara IN ({marcadores})
            UNION ALL
            SELECT e.id_senado, p.id_proposicao, p.sigla_tipo, p.numero, p.ano, p.casa
            FROM proposicoes_equivalentes e JOIN proposicoes p ON p.id_proposicao = e.id_camara
            WHERE e.id_senado IN ({marcadores})
            """,
            [*lote, *lote],
        ):
            equivalentes.setdefault(pid, []).append(
                {"id_proposicao": outro, "proposicao": f"{sigla} {numero}/{ano}", "casa": casa}
            )
        for pid, sigla, numero, ano, casa_id in conn.execute(
            f"""
            SELECT id_proposicao, sigla, numero, ano, casa_identificadora
            FROM proposicoes_identificacoes WHERE id_proposicao IN ({marcadores})
            ORDER BY ano, numero
            """,
            lote,
        ):
            identificacoes.setdefault(pid, []).append(
                f"{sigla} {numero}/{ano} ({'Câmara' if casa_id == 'CD' else 'Senado'})"
            )
        # Autoria de iniciativa (API do Senado), quando difere da do documento.
        for pid, nome, partido, uf in conn.execute(
            f"""
            SELECT id_proposicao, nome, partido, uf FROM autores_proposicao
            WHERE fonte = 'iniciativa' AND id_proposicao IN ({marcadores})
            ORDER BY id_proposicao, ordem
            """,
            lote,
        ):
            iniciativa.setdefault(pid, []).append(_rotulo_autor(nome, partido, uf))
        for pid, n in conn.execute(
            # `votacoes` também guarda as simbólicas: filtrar por `tem_voto_nominal = 1`.
            f"SELECT id_proposicao, COUNT(*) FROM votacoes WHERE id_proposicao IN ({marcadores}) "
            "AND tem_voto_nominal = 1 GROUP BY 1",
            lote,
        ):
            votacoes[pid] = n
        for pid, n in conn.execute(
            f"SELECT id_proposicao, COUNT(*) FROM proposicoes_chunks WHERE id_proposicao IN ({marcadores}) GROUP BY 1",
            lote,
        ):
            trechos[pid] = n
    detalhes = []
    for r in linhas:
        pid = r["id_proposicao"]
        item: dict[str, Any] = {
            "id_proposicao": pid,
            "sigla_tipo": r["sigla_tipo"],
            "numero": r["numero"],
            "ano": ano_da_data(r["ano"], r["data_apresentacao"]),
            "casa": r["casa"],
            "data_apresentacao": r["data_apresentacao"],
            "ementa": _limpar_ementa(r["ementa"]),
            "autores": autores.get(pid, SEM_AUTORIA),
            "url_inteiro_teor": r["url_inteiro_teor"],
            "votacoes_nominais": votacoes.get(pid, 0),
            "trechos_de_inteiro_teor_indexados": trechos.get(pid, 0),
        }
        autoria_iniciativa = ", ".join(iniciativa.get(pid, []))
        if autoria_iniciativa and autoria_iniciativa != item["autores"]:
            item["autoria_de_iniciativa"] = autoria_iniciativa
        if equivalentes.get(pid):
            item["mesma_materia_na_outra_casa"] = equivalentes[pid]
        proprio = f"{r['sigla_tipo']} {r['numero']}/{r['ano']}"
        outras = [x for x in identificacoes.get(pid, []) if not x.startswith(proprio + " (")]
        if outras:
            item["outras_identificacoes"] = outras
        detalhes.append(item)
    return detalhes


def buscar_proposicao(
    sigla_tipo: str,
    numero: int,
    ano: Optional[int] = None,
    casa: Optional[str] = None,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    sigla = (sigla_tipo or "").strip().upper()
    if not sigla:
        return {
            "erro": "SIGLA_VAZIA",
            "observacao": "Informe a sigla (PL, PEC, PLP, MPV, PDL, REQ, VET...).",
        }
    filtros = ["UPPER(sigla_tipo) = ?", "numero = ?"]
    params: list[Any] = [sigla, int(numero)]
    if ano:
        filtros.append("ano = ?")
        params.append(int(ano))
    casa = normalizar_casa(casa)
    if casa:
        filtros.append("casa = ?")
        params.append(casa)

    identificado_por_outro_numero = False
    with conexao(row_factory=True) as conn:
        linhas = conn.execute(
            f"{_SELECT_PROPOSICAO} WHERE {' AND '.join(filtros)} ORDER BY ano DESC, casa",
            params,
        ).fetchall()
        if not linhas:
            # Número anterior ou da outra casa, declarado pelo Senado (a Câmara renumera).
            filtros_id = ["UPPER(i.sigla) = ?", "i.numero = ?"]
            params_id: list[Any] = [sigla, int(numero)]
            if ano:
                filtros_id.append("i.ano = ?")
                params_id.append(int(ano))
            if casa:
                filtros_id.append("p.casa = ?")
                params_id.append(casa)
            linhas = conn.execute(
                f"""
                SELECT DISTINCT p.id_proposicao, p.sigla_tipo, p.numero, p.ano, p.ementa,
                       p.data_apresentacao, p.casa, p.url_inteiro_teor
                FROM proposicoes_identificacoes i JOIN proposicoes p ON p.id_proposicao = i.id_proposicao
                WHERE {' AND '.join(filtros_id)}
                ORDER BY p.ano DESC, p.casa
                """,
                params_id,
            ).fetchall()
            identificado_por_outro_numero = bool(linhas)
        encontradas = _detalhar(conn, linhas)

    if not encontradas:
        return {
            "erro": "NAO_ENCONTRADA",
            "procurado": {
                "sigla_tipo": sigla,
                "numero": numero,
                "ano": ano,
                "casa": casa,
            },
            "observacao": (
                "Nenhuma proposição com esses identificadores na base. Confira sigla e ano, ou tente sem "
                "`casa`. NÃO conclua que ela não existe fora da base."
            ),
        }
    itens, paginacao = paginar(encontradas, pagina, reserva=2_000)
    saida: dict[str, Any] = {
        "paginacao": paginacao,
        "total_encontradas": len(encontradas),
        "proposicoes": itens,
    }
    avisos = []
    if identificado_por_outro_numero:
        avisos.append(
            f"Nenhum registro tem hoje o número {sigla} {numero}{f'/{ano}' if ano else ''}; estes registros "
            "o têm como identificação anterior ou da outra casa (`outras_identificacoes`), segundo a API "
            "do Senado. Diga ao usuário o número atual."
        )
    if len(encontradas) > 1:
        avisos.append(
            "Há mais de um registro com esses identificadores. Uma proposição que tramita nas duas "
            "casas aparece com IDs diferentes na Câmara e no Senado (`mesma_materia_na_outra_casa`), e "
            "normalmente só uma delas tem votos. Escolha pelo campo `casa` e por `votacoes_nominais`, e "
            "diga ao usuário de qual casa é o dado."
        )
    if avisos:
        saida["aviso_sistema"] = " ".join(avisos)
    return saida


def obter_detalhes_proposicoes(
    lista_ids: list[int], pagina: Optional[int] = None
) -> dict[str, Any]:
    ids = ids_inteiros(lista_ids) or []
    if not ids:
        return {"proposicoes": [], "paginacao": None}
    linhas = []
    with conexao(row_factory=True) as conn:
        for i in range(0, len(ids), LOTE):
            lote = ids[i : i + LOTE]
            linhas += conn.execute(
                f"{_SELECT_PROPOSICAO} WHERE id_proposicao IN ({','.join('?' * len(lote))})",
                lote,
            ).fetchall()
        linhas.sort(key=lambda r: str(r["data_apresentacao"] or ""), reverse=True)
        detalhes = _detalhar(conn, linhas)
    encontrados = {d["id_proposicao"] for d in detalhes}
    indice = [
        {
            "id_proposicao": p["id_proposicao"],
            "proposicao": f"{p['sigla_tipo']} {p['numero']}/{p['ano']}",
            "casa": p["casa"],
        }
        for p in detalhes
    ]
    itens, paginacao = paginar(
        detalhes,
        pagina,
        reserva=len(str(indice)) + 2_000,
        resumo="O campo `indice_completo`",
    )
    saida: dict[str, Any] = {"paginacao": paginacao, "total": len(detalhes)}
    if not paginacao["completo"]:
        saida["indice_completo"] = indice
    saida["proposicoes"] = itens
    ausentes = [i for i in ids if i not in encontrados]
    if ausentes:
        saida["ids_nao_encontrados"] = ausentes
    return saida


# ---------------------------------------------------------------------------
# busca_semantica_proposicoes
# ---------------------------------------------------------------------------


def _formatar_trecho(c: dict[str, Any]) -> str:
    tipo = c.get("tipo_dispositivo") or "Artigo/Parágrafo"
    ident = c.get("identificador_normativo") or ""
    return f'[{tipo} {ident}]: "{c.get("texto_original") or ""}"'


def _apendice_sem_votacao(
    termos: list[str],
    ja_listados: set[int],
    lista_ids_chamador: Optional[list[int]],
    data_inicio: Optional[str],
    data_fim: Optional[str],
    casa: Optional[str],
    top_k: int,
) -> list[dict[str, Any]]:
    """
    Proposições relevantes do tema que não têm votação nominal (muitas são
    aprovadas simbolicamente). Usa todos os termos.
    """
    if lista_ids_chamador is not None and not lista_ids_chamador:
        return []
    buscador = obter_buscador()
    candidatos: dict[int, dict[str, Any]] = {}
    try:
        for termo in termos:
            for r in buscador.buscar_ementas(
                termo,
                lista_ids=lista_ids_chamador,
                data_inicio=data_inicio,
                data_fim=data_fim,
                casa=casa,
                top_k=top_k,
            ):
                pid = r["id_proposicao"]
                if pid in ja_listados:
                    continue
                # Soma do RRF entre termos, como na busca principal.
                if pid not in candidatos:
                    candidatos[pid] = {**r, "score": 0.0}
                candidatos[pid]["score"] += r.get("score") or 0.0
    except Exception:  # noqa: BLE001 — a busca principal já respondeu
        logger.exception(
            "Falha ao montar o apêndice de proposições sem votação nominal"
        )
        return []

    votados = ids_votados()
    # Só tipos deliberáveis: pareceres e substitutivos avulsos repetem a ementa da matéria principal.
    deliberaveis = tipos_deliberaveis()
    selecionadas = []
    for c in sorted(candidatos.values(), key=lambda c: c["score"], reverse=True):
        if c["id_proposicao"] in votados or c.get("sigla_tipo") not in deliberaveis:
            continue
        c["ano"] = ano_da_data(c.get("ano"), c.get("data_apresentacao"))
        if not c["ano"]:
            continue
        selecionadas.append(
            {
                "id_proposicao": c["id_proposicao"],
                "sigla_tipo": c["sigla_tipo"],
                "numero": c["numero"],
                "ano": c["ano"],
                "casa": c.get("casa"),
                "ementa": _limpar_ementa(c.get("ementa")),
                "score": round(c["score"], 5),
                "destaque_semantico": c.get("destaque_semantico"),
                "tem_votacao_nominal": False,
                "status_votacao": "SEM_VOTACAO_NOMINAL",
            }
        )
        if len(selecionadas) >= MAX_APENDICE_SEM_VOTACAO:
            break
    return selecionadas


def _confianca(item: dict[str, Any]) -> str:
    """
    Confiança de um resultado, sem filtrar nada.

    Dois sinais: `casamento_lexical` (o termo aparece no texto, pelo FTS5) e
    `destaque_semantico` (quanto a ementa se destaca do acervo para o termo).
    Quando concordam, o rótulo vale; quando se contradizem, devolve
    `indeterminada` e os dois números seguem no resultado.
    """
    d = item.get("destaque_semantico")
    lexical = bool(item.get("casamento_lexical"))
    if d is None:
        # Sem vetor, o lexical é o único sinal.
        return "alta" if lexical else "baixa"
    if d >= LIMIAR_DESTAQUE_ALTO:
        return "alta"
    if lexical and d < LIMIAR_DESTAQUE_MODERADO:
        return "indeterminada"
    if lexical or d >= LIMIAR_DESTAQUE_MODERADO:
        return "alta" if lexical else "moderada"
    return "baixa"


def _motivo_da_confianca(item: dict[str, Any]) -> str:
    """Em uma frase, de onde veio a confiança."""
    d = item.get("destaque_semantico")
    lexical = bool(item.get("casamento_lexical"))
    if d is None:
        return (
            "os termos aparecem no texto; esta proposição não tem vetor, então não há medida semântica para conferir"
            if lexical
            else "os termos não aparecem no texto e não há vetor para comparar"
        )
    if lexical and d < LIMIAR_DESTAQUE_MODERADO:
        return (
            f"os termos APARECEM no texto, mas o destaque semântico é {d} — abaixo de "
            f"{LIMIAR_DESTAQUE_MODERADO}, a faixa de consultas sem sentido. Os dois sinais se contradizem: "
            "costuma ser menção de passagem, nome de programa em anexo ou citação de outra lei. "
            "Confira o trecho antes de usar como fonte."
        )
    if lexical:
        return f"os termos aparecem no texto e o destaque semântico ({d}) acompanha"
    if d >= LIMIAR_DESTAQUE_ALTO:
        return f"os termos não aparecem literalmente, mas o destaque semântico é alto ({d})"
    if d >= LIMIAR_DESTAQUE_MODERADO:
        return f"os termos não aparecem literalmente e o destaque semântico é intermediário ({d})"
    return f"os termos não aparecem no texto e o destaque semântico é baixo ({d})"


def _calcular_busca(
    termos: tuple[str, ...],
    somente_votadas: bool,
    casa: Optional[str],
    data_inicio: Optional[str],
    data_fim: Optional[str],
    lista_ids: Optional[tuple[int, ...]],
    top_k: int,
) -> dict[str, Any]:
    """
    Fusão por PROPOSIÇÃO, por Reciprocal Rank Fusion (k = 60).

    Para cada termo entram duas listas de proposições: a das ementas (já
    híbrida) e a dos trechos de inteiro teor agregados por proposição (vale a
    posição do melhor trecho). Cada lista contribui 1/(60 + posição), somado
    entre listas e termos. O cosseno segue na saída só como informação.
    """
    cfg = obter_config()
    buscador = obter_buscador()
    ids_chamador = list(lista_ids) if lista_ids is not None else None
    universo = ids_chamador
    if somente_votadas:
        # Só ~0,3% das proposições têm voto nominal; as relevantes sem voto voltam no apêndice.
        votados = ids_votados()
        base = ids_chamador if ids_chamador is not None else sorted(votados)
        universo = [i for i in base if i in votados]

    candidatos = max(CANDIDATOS_POR_LISTA, top_k * 3)
    acumulados: dict[int, dict[str, Any]] = {}
    tem_trechos = buscador.chunks_disponiveis()

    def entrada(pid: int, meta: dict[str, Any]) -> dict[str, Any]:
        if pid not in acumulados:
            acumulados[pid] = {
                "id_proposicao": pid,
                "sigla_tipo": meta.get("sigla_tipo"),
                "numero": meta.get("numero"),
                "ano": meta.get("ano"),
                "ementa": meta.get("ementa"),
                "data_apresentacao": meta.get("data_apresentacao"),
                "casa": meta.get("casa"),
                "score": 0.0,
                "score_semantico": None,
                "destaque_semantico": None,
                "casamento_lexical": False,
                "origens": set(),
                "trechos": [],
            }
        return acumulados[pid]

    for termo in termos:
        for posicao, r in enumerate(
            buscador.buscar_ementas(
                termo,
                lista_ids=universo,
                data_inicio=data_inicio,
                data_fim=data_fim,
                casa=casa,
                threshold=cfg.threshold,
                top_k=candidatos,
            ),
            1,
        ):
            item = entrada(r["id_proposicao"], r)
            item["score"] += 1.0 / (K_RRF + posicao)
            item["origens"].add("ementa")
            item["casamento_lexical"] |= r.get("rank_lexical") is not None
            if r.get("score_semantico") is not None:
                item["score_semantico"] = max(item["score_semantico"] or 0.0, r["score_semantico"])
            if r.get("destaque_semantico") is not None:
                item["destaque_semantico"] = max(
                    item["destaque_semantico"] or float("-inf"), r["destaque_semantico"]
                )

        if not tem_trechos:
            continue
        ordem_das_proposicoes: list[int] = []
        for c in buscador.buscar_trechos(
            termo,
            lista_ids=universo,
            casa=casa,
            data_inicio=data_inicio,
            data_fim=data_fim,
            threshold=cfg.threshold_inteiro_teor,
            top_k=candidatos * TRECHOS_POR_PROPOSICAO_CANDIDATA,
        ):
            pid = c["id_proposicao"]
            item = entrada(pid, c)
            if pid not in ordem_das_proposicoes:
                ordem_das_proposicoes.append(pid)
            item["origens"].add("inteiro_teor")
            item["casamento_lexical"] |= c.get("score_lexical") is not None
            _anexar_trecho(item, c)
        for posicao, pid in enumerate(ordem_das_proposicoes, 1):
            acumulados[pid]["score"] += 1.0 / (K_RRF + posicao)

    finais = sorted(acumulados.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    # Trechos para as do topo que vieram só pela ementa.
    if finais and tem_trechos:
        sem_trecho = [r["id_proposicao"] for r in finais[:TOP_ENRIQUECIDAS] if not r["trechos"]]
        if sem_trecho:
            por_id = {r["id_proposicao"]: r for r in finais}
            for termo in termos:
                for c in buscador.buscar_trechos(
                    termo,
                    lista_ids=sem_trecho,
                    casa=casa,
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    top_k=len(sem_trecho) * MAX_TRECHOS_POR_PROPOSICAO,
                ):
                    _anexar_trecho(por_id[c["id_proposicao"]], c)

    with conexao() as conn:
        autores = _autores(conn, [r["id_proposicao"] for r in finais])

    resultados: list[dict[str, Any]] = []
    for r in finais:
        pid = r["id_proposicao"]
        trechos = sorted(r["trechos"], key=lambda t: t["score"], reverse=True)
        item = {
            "id_proposicao": pid,
            "sigla_tipo": r["sigla_tipo"],
            "numero": r["numero"],
            "ano": ano_da_data(r.get("ano"), r.get("data_apresentacao")),
            "casa": r["casa"],
            "data_apresentacao": r.get("data_apresentacao"),
            "ementa": _limpar_ementa(r.get("ementa")),
            "autores": autores.get(pid, SEM_AUTORIA),
            "score": round(r["score"], 5),
            "score_semantico": round(r["score_semantico"], 3) if r["score_semantico"] is not None else None,
            "destaque_semantico": r["destaque_semantico"],
            "casamento_lexical": r["casamento_lexical"],
            "busca_origem": "ementa_e_teor" if len(r["origens"]) > 1 or (r["trechos"] and "ementa" in r["origens"])
            else next(iter(r["origens"])),
            "trechos_relevantes_inteiro_teor": [t["texto"] for t in trechos[:MAX_TRECHOS_POR_PROPOSICAO]],
        }
        item["confianca"] = _confianca(item)
        item["motivo_da_confianca"] = _motivo_da_confianca(item)
        if somente_votadas:
            item["tem_votacao_nominal"] = True
        resultados.append(item)

    avisos: list[str] = []
    if resultados:
        topo = resultados[: min(10, len(resultados))]
        baixas = sum(1 for r in topo if r["confianca"] == "baixa")
        indefinidas = sum(1 for r in topo if r["confianca"] == "indeterminada")
        if not any(r["confianca"] == "alta" for r in topo):
            avisos.append(
                f"ATENÇÃO: nenhum dos primeiros resultados contém os termos {list(termos)} nem se destaca "
                "do acervo semanticamente. É provável que não haja "
                "proposição sobre o tema com esse vocabulário. Confira a ementa de cada um antes de usá-lo; "
                "se nenhum tratar do tema, diga que não encontrou, ou refaça com sinônimos em português."
            )
        if indefinidas:
            avisos.append(
                f"{indefinidas} dos {len(topo)} primeiros resultados têm `confianca: indeterminada`: os termos "
                "APARECEM no texto, mas a medida semântica diz que a proposição não trata do assunto. Leia "
                "`motivo_da_confianca` e o trecho antes de usar — é o padrão de menção de passagem, nome de "
                "programa em anexo ou citação de outra lei. Não trate como fonte do tema sem conferir."
            )
        if baixas > len(topo) // 2:
            avisos.append(
                f"{baixas} dos {len(topo)} primeiros resultados têm `confianca: baixa` (sem os termos no "
                "texto e sem destaque semântico). Use só os que a ementa confirma serem do tema."
            )
    else:
        avisos.append(
            "Nenhuma proposição encontrada"
            + (" entre as que têm votação nominal" if somente_votadas else "")
            + ". Diga isso ao usuário; não complete com conhecimento próprio."
        )

    saida: dict[str, Any] = {
        "termos": list(termos),
        "avisos": avisos,
        "resultados": resultados,
    }
    if somente_votadas:
        saida["apendice_sem_votacao_nominal"] = _apendice_sem_votacao(
            list(termos),
            {r["id_proposicao"] for r in resultados},
            ids_chamador,
            data_inicio,
            data_fim,
            casa,
            top_k,
        )
        if saida["apendice_sem_votacao_nominal"]:
            avisos.append(
                "APÊNDICE (`apendice_sem_votacao_nominal`): proposições relevantes para o tema que NÃO "
                "possuem votação nominal registrada. NENHUM parlamentar tem voto individual nelas "
                "(tipicamente foram apreciadas de forma simbólica, ou ainda não foram votadas). NÃO as "
                "use para afirmar posicionamento de ninguém; use-as para dizer ao usuário que, sobre "
                "este tema, existem proposições sem voto nominal a consultar — inclusive as mais conhecidas."
            )
    return saida


def _anexar_trecho(item: dict[str, Any], c: dict[str, Any]) -> None:
    """Guarda um trecho na proposição, sem repetir texto idêntico."""
    texto = _formatar_trecho(c)
    original = (c.get("texto_original") or "").strip()
    for existente in item["trechos"]:
        if existente["original"] == original:
            existente["score"] = max(existente["score"], c.get("score") or 0.0)
            return
    item["trechos"].append({"texto": texto, "original": original, "score": c.get("score") or 0.0})


def busca_semantica_proposicoes(
    termos: list[str],
    somente_votadas: bool = False,
    casa: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    lista_ids: Optional[list[int]] = None,
    top_k: Optional[int] = None,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    cfg = obter_config()
    termos_limpos = tuple(
        t.strip()
        for t in (termos or [])
        if isinstance(t, str) and t.strip() and t.strip().lower() != "none"
    )
    if not termos_limpos:
        return {
            "erro": "SEM_TERMOS",
            "observacao": "Informe ao menos um termo de busca.",
        }

    ids = ids_inteiros(lista_ids)
    argumentos = (
        termos_limpos,
        bool(somente_votadas),
        normalizar_casa(casa),
        data_inicio or None,
        data_fim or None,
        tuple(ids) if ids is not None else None,
        max(1, min(int(top_k or cfg.top_k), cfg.top_k_maximo)),
    )
    completo = cache.obter_ou_calcular(
        chave_de("busca_semantica", *argumentos), lambda: _calcular_busca(*argumentos)
    )

    # Índice compacto de todos os resultados, repetido em toda página.
    indice = [
        {
            "posicao": i,
            "id_proposicao": r["id_proposicao"],
            "proposicao": f"{r['sigla_tipo']} {r['numero']}/{r['ano']}",
            "casa": r["casa"],
            "confianca": r["confianca"],
            "motivo_da_confianca": r.get("motivo_da_confianca"),
        }
        for i, r in enumerate(completo["resultados"], 1)
    ]
    reserva = (
        len(str(completo.get("avisos")))
        + len(str(completo.get("apendice_sem_votacao_nominal", "")))
        + len(str(indice))
        + 3_000
    )
    itens, paginacao = paginar(
        completo["resultados"],
        pagina,
        reserva=reserva,
        resumo="O campo `indice_completo`",
    )
    avisos = list(completo["avisos"])
    if paginacao.get("aviso"):
        avisos.insert(0, paginacao["aviso"])
    saida: dict[str, Any] = {
        "paginacao": paginacao,
        "termos": completo["termos"],
        "total_resultados": len(completo["resultados"]),
        "avisos": avisos,
    }
    if not paginacao["completo"]:
        saida["indice_completo"] = indice
    saida["resultados"] = itens
    if "apendice_sem_votacao_nominal" in completo:
        saida["apendice_sem_votacao_nominal"] = completo["apendice_sem_votacao_nominal"]
    return saida


def resultados_completos_da_busca(**kwargs: Any) -> dict[str, Any]:
    """Todas as páginas de uma busca, para ferramentas que encadeiam a busca."""
    primeira = busca_semantica_proposicoes(**kwargs, pagina=1)
    if "erro" in primeira:
        return primeira
    resultados = list(primeira["resultados"])
    total = primeira["paginacao"]["total_paginas"]
    for n in range(2, total + 1):
        resultados += busca_semantica_proposicoes(**kwargs, pagina=n)["resultados"]
    avisos = [a for a in primeira["avisos"] if not a.startswith("RESULTADO PAGINADO")]
    return {**primeira, "resultados": resultados, "avisos": avisos}


# ---------------------------------------------------------------------------
# busca_inteiro_teor
# ---------------------------------------------------------------------------


def _filtrar_por_casa_e_data(
    ids: list[int],
    casa: Optional[str],
    data_inicio: Optional[str],
    data_fim: Optional[str],
) -> list[int]:
    """`ids` que passam nos filtros, na ordem recebida."""
    if not (casa or data_inicio or data_fim):
        return ids
    filtros, params = [], []
    for cond, valor in (
        ("casa = ?", casa),
        ("data_apresentacao >= ?", data_inicio),
        ("data_apresentacao <= ?", data_fim),
    ):
        if valor:
            filtros.append(cond)
            params.append(valor)
    aceitos: set[int] = set()
    with conexao() as conn:
        for i in range(0, len(ids), LOTE):
            lote = ids[i : i + LOTE]
            aceitos |= {
                r[0]
                for r in conn.execute(
                    f"SELECT id_proposicao FROM proposicoes WHERE id_proposicao IN ({','.join('?' * len(lote))}) AND "
                    + " AND ".join(filtros),
                    [*lote, *params],
                )
            }
    return [i for i in ids if i in aceitos]


def busca_inteiro_teor(
    termo: str,
    lista_ids: Optional[list[int]] = None,
    casa: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    top_k: Optional[int] = None,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    cfg = obter_config()
    termo = (termo or "").strip()
    # Sem termo não há o que buscar.
    if not termo:
        return {"erro": "SEM_TERMO", "trechos": []}
    top_k = max(
        1, min(int(top_k or cfg.top_k_inteiro_teor), cfg.top_k_inteiro_teor_maximo)
    )
    ids = ids_inteiros(lista_ids)
    casa = normalizar_casa(casa)

    def calcular() -> dict[str, Any]:
        avisos: list[str] = []
        sem_trechos: list[int] = []
        if ids:
            with conexao() as conn:
                com_trechos: set[int] = set()
                for i in range(0, len(ids), LOTE):
                    lote = ids[i : i + LOTE]
                    com_trechos |= {
                        r[0]
                        for r in conn.execute(
                            f"SELECT DISTINCT id_proposicao FROM proposicoes_chunks WHERE id_proposicao IN ({','.join('?' * len(lote))})",
                            lote,
                        )
                    }
            sem_trechos = [i for i in ids if i not in com_trechos]
        trechos = obter_buscador().buscar_trechos(
            termo,
            lista_ids=ids or None,
            casa=casa,
            data_inicio=data_inicio,
            data_fim=data_fim,
            threshold=cfg.threshold_inteiro_teor,
            top_k=top_k,
        )
        lidas: list[dict[str, Any]] = []
        falhas: list[dict[str, Any]] = []
        nao_tentadas: list[int] = []
        if sem_trechos:
            # Proposições pedidas sem inteiro teor indexado são lidas na hora
            # (trechos só em memória); os filtros de casa e data valem para elas.
            limite = cfg.sob_demanda_max_proposicoes if cfg.sob_demanda else 0
            alvo = _filtrar_por_casa_e_data(sem_trechos, casa, data_inicio, data_fim)
            nao_tentadas = alvo[limite:]
            if alvo[:limite]:
                extras, lidas, falhas = sob_demanda.buscar(
                    alvo[:limite], termo, cfg.threshold_inteiro_teor, top_k
                )
                # Mesma escala de score (RRF, k = 60) nas duas fontes.
                trechos = sorted(
                    [{**t, "origem": "indice"} for t in trechos]
                    + [{**t, "origem": "documento_baixado_agora"} for t in extras],
                    key=lambda t: t["score"],
                    reverse=True,
                )[:top_k]
            if lidas:
                avisos.append(
                    f"{len(lidas)} proposição(ões) sem inteiro teor indexado foram lidas agora do documento "
                    "oficial (`proposicoes_lidas_sob_demanda`). Trechos delas vêm com "
                    "`origem = documento_baixado_agora`; cite a `url_documento` como fonte."
                )
                incompletas = [
                    d["id_proposicao"] for d in lidas if d["texto_incompleto"]
                ]
                if incompletas:
                    avisos.append(
                        f"O texto lido de {incompletas} está INCOMPLETO (`texto_incompleto`): pouco texto "
                        "extraível, em geral capa ou PDF digitalizado como imagem, ou documento longo demais "
                        "cortado. Ausência de trecho nelas NÃO significa que o texto não trata do tema; "
                        "diga isso ao usuário e indique a `url_documento`."
                    )
            if falhas or nao_tentadas:
                sem_texto = [f["id_proposicao"] for f in falhas] + nao_tentadas
                avisos.append(
                    f"{len(sem_texto)} proposição(ões) pedidas NÃO têm inteiro teor disponível "
                    f"({sem_texto[:20]}{'...' if len(sem_texto) > 20 else ''}). Ausência de trecho "
                    "nelas NÃO significa que o texto não trata do tema — significa que não há texto para "
                    "consultar. Diga isso ao usuário."
                    + (" Motivos em `falhas_sob_demanda`." if falhas else "")
                    + (
                        f" {len(nao_tentadas)} não foram baixadas porque cada chamada lê no máximo "
                        f"{limite} documento(s); chame de novo com esses IDs em `lista_ids`."
                        if nao_tentadas and limite
                        else ""
                    )
                )
        if not trechos:
            avisos.append(
                "Nenhum trecho encontrado. Tente sinônimos ou termos do vocabulário jurídico; não afirme "
                "que nenhuma proposição trata do tema."
            )
        return {
            "avisos": avisos,
            "proposicoes_sem_inteiro_teor_indexado": sem_trechos,
            "proposicoes_lidas_sob_demanda": lidas,
            "falhas_sob_demanda": falhas,
            "trechos": [
                {
                    "id_proposicao": t["id_proposicao"],
                    "proposicao": f"{t['sigla_tipo']} {t['numero']}/{t['ano']}",
                    "casa": t.get("casa"),
                    "data_apresentacao": t.get("data_apresentacao"),
                    "ementa": _limpar_ementa(t.get("ementa")),
                    "tipo_dispositivo": t.get("tipo_dispositivo"),
                    "identificador_normativo": t.get("identificador_normativo"),
                    "texto_original": t.get("texto_original"),
                    "score": t.get("score"),
                    "score_semantico": t.get("score_semantico"),
                    "score_lexical": t.get("score_lexical"),
                    "origem": t.get("origem", "indice"),
                }
                for t in trechos
            ],
        }

    completo = cache.obter_ou_calcular(
        chave_de("inteiro_teor", termo, ids, casa, data_inicio, data_fim, top_k),
        calcular,
    )
    indice = [
        {
            "posicao": i,
            "id_proposicao": t["id_proposicao"],
            "proposicao": t["proposicao"],
            "dispositivo": f"{t.get('tipo_dispositivo') or ''} {t.get('identificador_normativo') or ''}".strip(),
        }
        for i, t in enumerate(completo["trechos"], 1)
    ]
    itens, paginacao = paginar(
        completo["trechos"],
        pagina,
        reserva=len(str(indice)) + len(str(completo["avisos"])) + 3_000,
        resumo="O campo `indice_completo`",
    )
    avisos = list(completo["avisos"])
    if paginacao.get("aviso"):
        avisos.insert(0, paginacao["aviso"])
    saida: dict[str, Any] = {
        "paginacao": paginacao,
        "termo": termo,
        "total_trechos": len(completo["trechos"]),
        "avisos": avisos,
        "proposicoes_sem_inteiro_teor_indexado": completo[
            "proposicoes_sem_inteiro_teor_indexado"
        ],
        "proposicoes_lidas_sob_demanda": completo["proposicoes_lidas_sob_demanda"],
        "falhas_sob_demanda": completo["falhas_sob_demanda"],
    }
    if not paginacao["completo"]:
        saida["indice_completo"] = indice
    saida["trechos"] = itens
    return saida
