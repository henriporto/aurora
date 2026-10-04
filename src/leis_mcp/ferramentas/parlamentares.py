"""Identificação de parlamentares e histórico de atuação."""

from __future__ import annotations

from typing import Any, Optional

from leis_mcp.dados.banco import conexao
from leis_mcp.ferramentas.votos import VOTOS_SEM_POSICIONAMENTO
from leis_mcp.paginacao import paginar
from leis_mcp.texto import normalizar_casa, palavras, sem_acento

PAPEIS_VALIDOS = ("autor", "relator", "votante")


def buscar_id_parlamentar(
    nome: str, casa: Optional[str] = None, pagina: Optional[int] = None
) -> dict[str, Any]:
    """
    Parlamentares cujo nome contém as palavras pedidas, sem diferenciar acento.

    Compara em Python, sem `LIKE` (onde `_` e `%` são curingas). Cada palavra
    pedida precisa ser o início de alguma palavra do nome; se nenhum nome casar
    assim, vale o trecho em qualquer posição. Resultado paginado.
    """
    procuradas = palavras(nome or "")
    if not procuradas:
        return {
            "erro": "NOME_VAZIO",
            "recebido": nome,
            "observacao": (
                "Informe ao menos parte do nome do parlamentar, com letras. Esta ferramenta não "
                "lista a base inteira."
            ),
        }
    casa = normalizar_casa(casa)
    with conexao() as conn:
        todos = conn.execute(
            "SELECT id_parlamentar, nome, partido, uf, casa FROM parlamentares"
            + (" WHERE casa = ?" if casa else ""),
            [casa] if casa else [],
        ).fetchall()

    def relevancia(palavras_do_nome: list[str]) -> Optional[int]:
        if palavras_do_nome == procuradas:
            return 0
        if all(p in palavras_do_nome for p in procuradas):
            return 1
        if all(any(w.startswith(p) for w in palavras_do_nome) for p in procuradas):
            return 2
        return None

    achados = []
    for r in todos:
        nivel = relevancia(palavras(r[1]))
        if nivel is not None:
            achados.append((nivel, r))
    if not achados:
        corrido = " ".join(procuradas)
        achados = [(3, r) for r in todos if corrido in " ".join(palavras(r[1]))]
    achados.sort(key=lambda x: (x[0], sem_acento(x[1][1])))

    candidatos = [
        {
            "id_parlamentar": int(r[0]),
            "nome": r[1],
            "partido": r[2],
            "uf": r[3],
            "casa": r[4],
        }
        for _, r in achados
    ]
    itens, paginacao = paginar(candidatos, pagina, reserva=2_000)
    saida: dict[str, Any] = {
        "paginacao": paginacao,
        "total": len(candidatos),
        "parlamentares": itens,
    }
    if not candidatos:
        saida["aviso_sistema"] = (
            "Nenhum parlamentar com esse nome na base. Confira a grafia ou tente só o sobrenome; "
            "NÃO conclua que a pessoa não existe fora da base."
        )
    elif len(candidatos) > 1:
        saida["aviso_sistema"] = (
            "Mais de um candidato. Escolha pelo nome completo, partido, UF e casa; se continuar "
            "ambíguo, pergunte ao usuário. A mesma pessoa pode ter um ID na Câmara e outro no Senado."
        )
    if paginacao.get("aviso"):
        saida["aviso_sistema"] = paginacao["aviso"] + " " + saida.get("aviso_sistema", "")
    return saida


def consultar_historico(
    id_parlamentar: int,
    papel: Optional[str] = None,
    tipo_voto: Optional[str] = None,
    ano: Optional[int] = None,
    pagina: Optional[int] = None,
) -> dict[str, Any]:
    # Papel fora do domínio é erro, não lista vazia.
    if papel is not None and papel.strip().lower() not in PAPEIS_VALIDOS:
        return {
            "erro": "PAPEL_INVALIDO",
            "recebido": papel,
            "papeis_validos": list(PAPEIS_VALIDOS),
        }

    with conexao() as conn:
        # Rótulo de voto inexistente é erro, não lista vazia.
        if tipo_voto is not None:
            rotulos = sorted(
                r[0]
                for r in conn.execute("SELECT DISTINCT tipo_voto FROM votos")
                if r[0]
            )
            if tipo_voto.strip() not in rotulos:
                return {
                    "erro": "TIPO_VOTO_INVALIDO",
                    "recebido": tipo_voto,
                    "rotulos_validos": rotulos,
                    "observacao": (
                        "Para saber se um parlamentar apoiou ou rejeitou uma proposição, use "
                        "`consultar_votos`: ela distingue voto real de ausência de dado. Esta "
                        "ferramenta devolve apenas IDs."
                    ),
                }

        papeis = [papel.strip().lower()] if papel else list(PAPEIS_VALIDOS)
        if tipo_voto and "votante" not in papeis:
            papeis.append("votante")

        filtro_ano = " AND p.ano = ?" if ano else ""
        por_papel: dict[str, set[int]] = {}
        if "autor" in papeis:
            por_papel["autor"] = {
                r[0]
                for r in conn.execute(
                    "SELECT a.id_proposicao FROM autoria a JOIN proposicoes p ON a.id_proposicao = p.id_proposicao "
                    "WHERE a.id_parlamentar = ?" + filtro_ano,
                    [int(id_parlamentar)] + ([int(ano)] if ano else []),
                )
            }
        if "relator" in papeis:
            por_papel["relator"] = {
                r[0]
                for r in conn.execute(
                    "SELECT r.id_proposicao FROM relatorias r JOIN proposicoes p ON r.id_proposicao = p.id_proposicao "
                    "WHERE r.id_parlamentar = ?" + filtro_ano,
                    [int(id_parlamentar)] + ([int(ano)] if ano else []),
                )
            }
        if "votante" in papeis:
            sql = (
                "SELECT v.id_proposicao FROM votos v JOIN proposicoes p ON v.id_proposicao = p.id_proposicao "
                "WHERE v.id_parlamentar = ? "
                # Ausência oficial registrada não é atuação como votante.
                f"AND v.tipo_voto NOT IN ({','.join('?' * len(VOTOS_SEM_POSICIONAMENTO))})"
            )
            params: list[Any] = [int(id_parlamentar), *sorted(VOTOS_SEM_POSICIONAMENTO)]
            if tipo_voto:
                sql += " AND v.tipo_voto = ?"
                params.append(tipo_voto.strip())
            if ano:
                sql += filtro_ano
                params.append(int(ano))
            por_papel["votante"] = {r[0] for r in conn.execute(sql, params)}

    todos = sorted(set().union(*por_papel.values()), reverse=True)
    ids, paginacao = paginar(todos, pagina, reserva=2_000)
    return {
        "paginacao": paginacao,
        "id_parlamentar": int(id_parlamentar),
        "totais_por_papel": {p: len(v) for p, v in por_papel.items()},
        "total": len(todos),
        "ids_proposicoes": ids,
        "aviso_sistema": (
            "Estes IDs NÃO dizem como o parlamentar votou e NÃO distinguem 'não votou' de "
            "'não houve votação nominal'. Para posição, use `consultar_votos` ou "
            "`votos_por_tema`; para ver as proposições, `obter_detalhes_proposicoes`."
        ),
    }
