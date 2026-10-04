"""Como UM parlamentar votou nas proposições de um tema — em uma chamada."""

from __future__ import annotations

from typing import Any, Optional

from leis_mcp.dados.banco import conexao
from leis_mcp.ferramentas.proposicoes import resultados_completos_da_busca
from leis_mcp.ferramentas.votos import status_por_proposicao
from leis_mcp.paginacao import paginar


def votos_por_tema(
    id_parlamentar: int,
    termos: list[str],
    casa: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    top_k: Optional[int] = None,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    """
    Encadeia `busca_semantica_proposicoes(somente_votadas=True)` e `consultar_votos`.
    """
    with conexao() as conn:
        pessoa = conn.execute(
            "SELECT id_parlamentar, nome, partido, uf, casa FROM parlamentares WHERE id_parlamentar = ?",
            (int(id_parlamentar),),
        ).fetchone()
    if pessoa is None:
        return {
            "erro": "PARLAMENTAR_NAO_ENCONTRADO",
            "observacao": "Use `buscar_id_parlamentar` para obter o ID.",
        }

    # Sem `casa`, busca só na casa do parlamentar: ele não vota na outra.
    casa_da_busca = casa or pessoa[4]
    busca = resultados_completos_da_busca(
        termos=termos,
        somente_votadas=True,
        casa=casa_da_busca,
        data_inicio=data_inicio,
        data_fim=data_fim,
        top_k=top_k,
    )
    if "erro" in busca:
        return busca

    votadas = busca["resultados"]
    ementas = {r["id_proposicao"]: r.get("ementa") for r in votadas}
    status = (
        status_por_proposicao(pessoa[0], [r["id_proposicao"] for r in votadas])
        if votadas
        else []
    )

    votou, sem_voto = [], []
    for s in status:
        s["ementa"] = ementas.get(s.get("id_proposicao"))
        (
            votou
            if s.get("status") in ("VOTOU", "VOTOU_EM_VARIAS_VOTACOES")
            else sem_voto
        ).append(s)

    resumo = [
        {
            "id_proposicao": v["id_proposicao"],
            "proposicao": v["proposicao"],
            "status": v["status"],
            "votos_registrados": sorted(
                {x["voto"] for x in v.get("votacoes", []) if x.get("voto")} | ({v["voto"]} if v.get("voto") else set())
            ),
        }
        for v in votou
    ]
    itens, paginacao = paginar(
        votou,
        pagina,
        reserva=len(str(sem_voto)) + len(str(resumo)) + 6_000,
        resumo="O campo `resumo_dos_votos`",
    )
    avisos = list(busca["avisos"])
    avisos += [
        "As proposições foram encontradas por busca semântica entre as que têm votação nominal: confira "
        "pela ementa se cada uma é de fato do tema antes de usá-la.",
        "Os itens de `ausente_ou_sem_voto` (AUSENTE, OUTRA_CASA, FORA_DE_EXERCICIO, SEM_VOTACAO_NOMINAL, FORA_DA_BASE) são AUSÊNCIA DE "
        "DADO, e NUNCA devem ser apresentados como posicionamento contrário, apoio ou omissão política.",
        "Em `votou`, leia a descrição de cada votação (campo `votacao`): só votação sobre o TEXTO autoriza "
        "dizer que o parlamentar foi a favor ou contra a proposição.",
    ]
    if not votadas:
        avisos.append(
            "Não há proposição com votação nominal sobre o tema na base. Diga isso ao usuário e NÃO conclua "
            "que o parlamentar é contra ou a favor."
        )
    if paginacao.get("aviso"):
        avisos.insert(0, paginacao["aviso"])

    saida: dict[str, Any] = {
        "paginacao": paginacao,
        "parlamentar": {
            "id_parlamentar": pessoa[0],
            "nome": pessoa[1],
            "partido_atual": pessoa[2],
            "uf": pessoa[3],
            "casa": pessoa[4],
        },
        "termos": busca["termos"],
        "casa_da_busca": casa_da_busca,
        "total_proposicoes_votadas_do_tema": len(votadas),
        "avisos": avisos,
    }
    if not paginacao["completo"]:
        saida["resumo_dos_votos"] = resumo
    saida.update(
        {
            "votou": itens,
            "ausente_ou_sem_voto": sem_voto,
            "proposicoes_do_tema_sem_votacao_nominal": busca.get("apendice_sem_votacao_nominal", []),
        }
    )
    return saida
