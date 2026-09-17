"""Vetos presidenciais (proposições VET de autoria da Presidência da República)."""

from __future__ import annotations

from typing import Any, Optional

from leis_mcp.dados.banco import conexao
from leis_mcp.dados.cobertura import ANO_INICIAL_VETOS
from leis_mcp.ferramentas.proposicoes import resultados_completos_da_busca
from leis_mcp.paginacao import paginar
from leis_mcp.texto import ementa_sem_profissoes

#: Mandatos mapeados. Nome fora da lista é ERRO explícito: antes, um nome que
#: não casava deixava a consulta sem filtro de data, e um erro de digitação
#: ('Bolsonaru') atribuía os 485 vetos da base à pessoa errada.
MANDATOS: dict[str, list[tuple[str, str]]] = {
    "lula": [("2003-01-01", "2010-12-31"), ("2023-01-01", "2026-12-31")],
    "bolsonaro": [("2019-01-01", "2022-12-31")],
    "dilma": [("2011-01-01", "2016-08-31")],
    "temer": [("2016-08-31", "2018-12-31")],
}



def consultar_vetos_presidenciais(
    presidente: str,
    termo: Optional[str] = None,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    nome = (presidente or "").lower()
    chave = next((k for k in MANDATOS if k in nome), None)
    if chave is None:
        return {
            "erro": "PRESIDENTE_NAO_RECONHECIDO",
            "presidente_consultado": presidente,
            "presidentes_suportados": sorted(MANDATOS),
            "observacao": (
                "Não há mandato mapeado para este nome, então NÃO é possível listar vetos dele. NÃO "
                "apresente vetos de outros presidentes como se fossem deste. Confirme a grafia do nome."
            ),
        }

    periodos = MANDATOS[chave]
    fim_mandato = max(fim for _, fim in periodos)
    # Mandato inteiramente anterior à cobertura devolvia lista vazia,
    # indistinguível de "este presidente não vetou nada".
    if int(fim_mandato[:4]) < ANO_INICIAL_VETOS:
        return {
            "erro": "FORA_DA_COBERTURA",
            "presidente_consultado": presidente,
            "periodo_do_mandato": [f"{i} a {f}" for i, f in periodos],
            "cobertura_da_base": f"{ANO_INICIAL_VETOS} em diante",
            "observacao": (
                "O mandato deste presidente é anterior ao período coberto pela base de vetos. A lista "
                "vazia NÃO significa que ele não vetou nada — significa que não há dados. Diga isso explicitamente."
            ),
        }

    condicao_datas = " OR ".join(
        "(p.data_apresentacao BETWEEN ? AND ?)" for _ in periodos
    )
    params: list[Any] = []
    for inicio, fim in periodos:
        params.extend([inicio, fim])
    with conexao() as conn:
        linhas = conn.execute(
            f"""
            SELECT p.id_proposicao, p.sigla_tipo, p.numero, p.ano, p.ementa, p.data_apresentacao, p.casa
            FROM proposicoes p
            WHERE p.sigla_tipo = 'VET' AND ({condicao_datas})
              AND EXISTS (
                  SELECT 1 FROM autores_proposicao ap
                  WHERE ap.id_proposicao = p.id_proposicao AND ap.fonte = 'documento'
                    AND ap.tipo = 'PRESIDENTE_REPUBLICA'
              )
            ORDER BY p.data_apresentacao DESC
            """,
            params,
        ).fetchall()

    saida: dict[str, Any] = {
        "presidente": chave,
        "periodos_do_mandato": [f"{i} a {f}" for i, f in periodos],
        "total_de_vetos_no_periodo": len(linhas),
    }
    avisos: list[str] = []
    if min(inicio for inicio, _ in periodos)[:4] < str(ANO_INICIAL_VETOS):
        avisos.append(
            f"COBERTURA PARCIAL: o mandato de {chave.capitalize()} começa antes de {ANO_INICIAL_VETOS}, "
            "primeiro ano com vetos na base. A lista é um RECORTE, não o total de vetos do mandato. Diga "
            "isso ao usuário em vez de apresentá-la como completa."
        )

    if termo and linhas:
        # Todos os vetos do período entram no ranking; nenhum é descartado por
        # corte de cosseno (a escala depende do modelo). Antes a chamada usava
        # threshold=0 e top_k=todos e devolvia tudo como resultado: "Lula +
        # saneamento básico" listava 87 dos 184 vetos. Agora os de confiança
        # baixa (sem os termos no texto e sem destaque semântico) saem da lista
        # principal, mas continuam contados e identificados.
        busca = resultados_completos_da_busca(
            termos=[termo],
            lista_ids=[r[0] for r in linhas],
            top_k=len(linhas),
        )
        relevantes = [v for v in busca.get("resultados", []) if v.get("confianca") != "baixa"]
        demais = [v for v in busca.get("resultados", []) if v.get("confianca") == "baixa"]
        itens, paginacao = paginar(relevantes, pagina, reserva=len(str(demais)) + 3_000)
        saida = {
            "paginacao": paginacao,
            **saida,
            "termo": termo,
            "vetos_relacionados_ao_termo": len(relevantes),
            "vetos": itens,
            "vetos_sem_relacao_aparente": [
                {"id_proposicao": v["id_proposicao"], "veto": f"VET {v['numero']}/{v['ano']}"}
                for v in demais
            ],
        }
        # Os avisos de confiança da busca não se aplicam: aqui os de confiança
        # baixa já estão separados em `vetos_sem_relacao_aparente`.
        avisos = [
            a
            for a in busca.get("avisos", [])
            if not a.startswith(("RESULTADO PAGINADO", "ATENÇÃO: nenhum", ))
            and "`confianca: baixa`" not in a
        ] + avisos
        if not relevantes:
            avisos.append(
                f"Nenhum dos {len(linhas)} vetos do período contém '{termo}' ou trata claramente do tema. "
                "Diga isso ao usuário; `vetos_sem_relacao_aparente` lista os demais só por transparência."
            )
        if paginacao.get("aviso"):
            avisos.insert(0, paginacao["aviso"])
    else:
        vetos = [
            {
                "id_proposicao": r[0],
                "sigla_tipo": r[1],
                "numero": r[2],
                "ano": r[3],
                "ementa": ementa_sem_profissoes(r[4]),
                "data_apresentacao": r[5],
                "casa": r[6],
            }
            for r in linhas
        ]
        indice = [
            {"id_proposicao": v["id_proposicao"], "veto": f"VET {v['numero']}/{v['ano']}", "data": v["data_apresentacao"]}
            for v in vetos
        ]
        itens, paginacao = paginar(
            vetos, pagina, reserva=len(str(indice)) + 3_000, resumo="O campo `indice_completo`"
        )
        saida = {"paginacao": paginacao, **saida}
        if not paginacao["completo"]:
            saida["indice_completo"] = indice
        saida["vetos"] = itens
        if paginacao.get("aviso"):
            avisos.insert(0, paginacao["aviso"])
    saida["avisos"] = avisos
    return saida
