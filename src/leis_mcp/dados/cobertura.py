"""O que a base cobre, medido no próprio banco e calculado uma vez por processo."""

from __future__ import annotations

import threading
from typing import Optional

from leis_mcp.dados.banco import conexao
from leis_mcp.texto import br

#: Abaixo disto, considera-se que a ingestão daquela casa não foi feita.
MIN_VOTACOES_PARA_COBERTURA = 50

#: Primeiro ano com votos nominais e vetos na base.
ANO_INICIAL_VOTOS = 2018
ANO_INICIAL_VETOS = 2018

_trava = threading.Lock()
_cobertura_casa: Optional[dict[str, int]] = None
_tipos_deliberaveis: Optional[set[str]] = None
_alcance: Optional[dict] = None


def cobertura_por_casa() -> dict[str, int]:
    """Votações distintas por casa, saturando em MIN_VOTACOES_PARA_COBERTURA."""
    global _cobertura_casa
    with _trava:
        if _cobertura_casa is None:
            cobertura: dict[str, int] = {}
            with conexao() as conn:
                for casa in ("Câmara", "Senado"):
                    cobertura[casa] = conn.execute(
                        "SELECT COUNT(*) FROM (SELECT id_votacao FROM votos "
                        "WHERE casa = ? GROUP BY id_votacao LIMIT ?)",
                        (casa, MIN_VOTACOES_PARA_COBERTURA),
                    ).fetchone()[0]
            _cobertura_casa = cobertura
        return _cobertura_casa


def tipos_deliberaveis() -> set[str]:
    """Siglas de tipos que já receberam votação nominal alguma vez."""
    global _tipos_deliberaveis
    with _trava:
        if _tipos_deliberaveis is None:
            with conexao() as conn:
                _tipos_deliberaveis = {
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT sigla_tipo FROM proposicoes WHERE id_proposicao "
                        "IN (SELECT DISTINCT id_proposicao FROM votos)"
                    )
                    if r[0]
                }
        return _tipos_deliberaveis


_ids_votados: Optional[frozenset[int]] = None


def ids_votados() -> frozenset[int]:
    """IDs das proposições com ao menos um voto nominal (~1.300)."""
    global _ids_votados
    with _trava:
        if _ids_votados is None:
            with conexao() as conn:
                _ids_votados = frozenset(
                    r[0]
                    for r in conn.execute("SELECT DISTINCT id_proposicao FROM votos")
                )
        return _ids_votados


def alcance() -> dict:
    """Números de cobertura da base, para o recurso `leis://alcance`."""
    global _alcance
    with _trava:
        if _alcance is None:
            with conexao() as conn:
                # `votacoes` tem nominais e simbólicas: filtrar por `tem_voto_nominal = 1`.
                primeiro, ultimo, n_votacoes = conn.execute(
                    "SELECT MIN(substr(data, 1, 10)), MAX(substr(data, 1, 10)), COUNT(*) "
                    "FROM votacoes WHERE tem_voto_nominal = 1"
                ).fetchone()
                n_props = conn.execute("SELECT COUNT(*) FROM proposicoes").fetchone()[0]
                com_voto = conn.execute(
                    "SELECT COUNT(DISTINCT id_proposicao) FROM votacoes "
                    "WHERE tem_voto_nominal = 1"
                ).fetchone()[0]
                # Simbólicas: decisão sem voto individual registrado.
                n_simbolicas, com_alguma = conn.execute(
                    "SELECT (SELECT COUNT(*) FROM votacoes WHERE tem_voto_nominal = 0), "
                    "(SELECT COUNT(DISTINCT id_proposicao) FROM votacoes)"
                ).fetchone()
                # Só votos com posição; ausências oficiais registradas não contam.
                n_votos = conn.execute(
                    "SELECT COUNT(*) FROM votos WHERE tipo_voto NOT IN "
                    "('Ausente', 'Não registrou voto', 'Presidente (não vota)', "
                    "'Votou (secreta)', 'Indeterminado', 'Artigo 17')"
                ).fetchone()[0]
                n_parl = conn.execute("SELECT COUNT(*) FROM parlamentares").fetchone()[
                    0
                ]
                n_chunks = conn.execute(
                    "SELECT COUNT(*) FROM proposicoes_chunks"
                ).fetchone()[0]
            _alcance = {
                "votacoes_primeira_data": primeiro,
                "votacoes_ultima_data": ultimo,
                "votacoes_nominais": n_votacoes,
                "votacoes_simbolicas": n_simbolicas,
                "votos_individuais": n_votos,
                "proposicoes": n_props,
                "proposicoes_com_votacao_nominal": com_voto,
                "proposicoes_com_alguma_votacao": com_alguma,
                "parlamentares": n_parl,
                "trechos_de_inteiro_teor": n_chunks,
            }
        return _alcance


def texto_de_alcance() -> str:
    a = alcance()
    pct = f"{100 * a['proposicoes_com_votacao_nominal'] / max(a['proposicoes'], 1):.1f}".replace(".", ",")
    primeiro = a["votacoes_primeira_data"]
    return (
        "ALCANCE DA BASE (medido no banco, não estimado — use estes números quando precisar dizer o que a "
        "base cobre):\n"
        f"   - Votações nominais: {br(a['votacoes_nominais'])} sessões, de {primeiro} a "
        f"{a['votacoes_ultima_data']}, com {br(a['votos_individuais'])} votos individuais. NÃO há voto "
        "individual registrado fora desse intervalo; matéria votada antes disso não tem como ser respondida "
        "com voto nominal, e dizer isso é a resposta certa.\n"
        f"   - Proposições: {br(a['proposicoes'])} no acervo, das quais "
        f"{br(a['proposicoes_com_votacao_nominal'])} ({pct}%) têm ao menos uma votação nominal.\n"
        f"   - Votações SIMBÓLICAS registradas: {br(a['votacoes_simbolicas'])}. Nelas houve decisão e NÃO há "
        "voto individual de ninguém — os líderes orientam e o painel não é aberto. Somando as duas formas, "
        f"{br(a['proposicoes_com_alguma_votacao'])} proposições têm alguma votação registrada. Por isso "
        "'sem votação nominal' NÃO quer dizer 'nunca foi votada': pode ter sido aprovada por acordo, e a "
        "diferença está em `votacoes.tem_voto_nominal`.\n"
        f"   - O acervo de proposições é mais amplo que o de votos: uma proposição anterior a {primeiro} pode "
        "existir na base sem nenhum voto associado.\n"
        f"   - Parlamentares cadastrados: {br(a['parlamentares'])}. Trechos de inteiro teor indexados: "
        f"{br(a['trechos_de_inteiro_teor'])}.\n"
        "   - Relatorias: só da Câmara. Não há orientação de bancada na base.\n"
        "   - Discursos: a base guarda o inteiro teor de falas em plenário das duas casas, mas NENHUMA "
        "ferramenta os expõe hoje. Não afirme nada sobre o que alguém disse em plenário; se a pergunta for "
        "sobre discurso, diga que esse dado ainda não está disponível por ferramenta.\n"
    )


def aquecer() -> None:
    cobertura_por_casa()
    tipos_deliberaveis()
    ids_votados()
    alcance()
