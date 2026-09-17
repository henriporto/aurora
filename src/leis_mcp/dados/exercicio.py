"""
Em exercício na data? A partir de `exercicio_eventos` (histórico da Câmara).

Cada evento registra a situação a partir de `data_hora`; o estado numa data é a
situação do último evento até ela.

A afirmação usada pelo servidor é CONSERVADORA (`fora_de_exercicio_no_dia`),
porque o histórico da API tem lacunas medidas contra os votos reais:

- 2.922 votos (20 deputados) foram dados com "CONVOCADO" como último evento: a
  posse ou a reassunção nem sempre vira evento "Exercício". CONVOCADO fica
  indeterminado, nunca "fora";
- licenças datadas às 00:00 do dia em que o deputado votou de manhã (44 votos).
  Por isso a saída precisa valer no fim da véspera E no fim do dia.

Mesmo assim, 19 dos 1,26 milhão de votos da Câmara (0,0015%) caem em dias que
o histórico dá como fora de exercício: o deputado votou durante licença
registrada. Por isso quem chama (`votos.status_por_proposicao`) também não
afirma "fora" quando o deputado votou em outra votação da mesma casa no dia:
um voto real contradiz o histórico. O voto só derruba a afirmação, nunca a cria.
"""

from __future__ import annotations

import bisect
from datetime import date, timedelta
import sqlite3
import threading
from typing import Optional

from leis_mcp.dados.banco import conexao

EM_EXERCICIO = "Exercício"

#: Situações que afirmam, sem ambiguidade, que o deputado não estava em exercício.
SAIDAS = frozenset({"Licença", "Afastado", "SUPLENCIA", "FIM_MANDATO", "VACANCIA"})

_trava = threading.Lock()
_eventos: Optional[dict[int, tuple[list[str], list[str]]]] = None


def _carregar() -> dict[int, tuple[list[str], list[str]]]:
    global _eventos
    with _trava:
        if _eventos is None:
            eventos: dict[int, tuple[list[str], list[str]]] = {}
            try:
                with conexao() as conn:
                    for pid, data_hora, situacao in conn.execute(
                        "SELECT id_parlamentar, data_hora, situacao FROM exercicio_eventos "
                        "ORDER BY id_parlamentar, data_hora"
                    ):
                        datas, situacoes = eventos.setdefault(pid, ([], []))
                        datas.append(data_hora)
                        situacoes.append(situacao)
            except sqlite3.OperationalError:
                pass  # banco sem a tabela: nada a afirmar
            _eventos = eventos
        return _eventos


def tem_historico(id_parlamentar: int) -> bool:
    return id_parlamentar in _carregar()


def situacao_na_data(id_parlamentar: int, data: str) -> Optional[str]:
    """
    Situação do parlamentar na data/hora `data` (ISO), ou None se não houver
    histórico dele ou nenhum evento até a data.

    Data sem hora ("2023-12-15") é tratada como o fim do dia: um deputado que
    tomou posse às 12h votou naquela data em exercício.
    """
    registro = _carregar().get(id_parlamentar)
    if not registro or not data:
        return None
    datas, situacoes = registro
    chave = data if len(data) > 10 else f"{data}T23:59:59"
    pos = bisect.bisect_right(datas, chave) - 1
    return situacoes[pos] if pos >= 0 else None


def em_exercicio(id_parlamentar: int, data: str) -> Optional[bool]:
    """True/False pelo histórico; None quando não há histórico para decidir."""
    situacao = situacao_na_data(id_parlamentar, data)
    return None if situacao is None else situacao == EM_EXERCICIO


def fora_de_exercicio_no_dia(id_parlamentar: int, data: str) -> bool:
    """
    True só quando o histórico afirma que o deputado estava fora de exercício o
    dia inteiro da data: situação de saída (ou ainda sem nenhum evento) no fim
    da véspera e no fim do dia. Sem histórico, ou com CONVOCADO, é False.
    """
    if not data or not tem_historico(id_parlamentar):
        return False
    dia = data[:10]
    try:
        vespera = (date.fromisoformat(dia) - timedelta(days=1)).isoformat()
    except ValueError:
        return False

    def saida(momento: str) -> bool:
        situacao = situacao_na_data(id_parlamentar, momento)
        return situacao is None or situacao in SAIDAS

    return saida(f"{vespera}T23:59:59") and saida(f"{dia}T23:59:59")
