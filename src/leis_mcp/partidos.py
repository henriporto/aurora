"""
Siglas de partido que designam a mesma legenda.

`votos.partido_voto` guarda a sigla crua de cada fonte na data do voto. A mesma
legenda aparece com siglas diferentes por grafia da fonte (a Câmara grava
"PODE", o Senado "PODEMOS") ou por renomeação (PR → PL, PRB → REPUBLICANOS).

Fusão e incorporação não entram: o partido resultante não é o mesmo que os
anteriores. Ficam só como aviso (`SUCESSOES`).
"""

from __future__ import annotations

from typing import Optional

#: Legenda (nome atual da sigla) -> todas as siglas com que aparece na base.
LEGENDAS: dict[str, tuple[str, ...]] = {
    "MDB": ("PMDB", "MDB"),  # renomeação, 2017
    "PL": ("PR", "PL"),  # renomeação, 2019
    "REPUBLICANOS": ("PRB", "REPUBLICANOS"),  # renomeação, 2019
    "CIDADANIA": ("PPS", "CIDADANIA"),  # renomeação, 2019
    "PATRIOTA": ("PEN", "PATRI", "PATRIOTA"),  # renomeação, 2018; sigla, 2019
    "SOLIDARIEDADE": ("SD", "SOLIDARIEDADE"),  # sigla, 2019
    "PODEMOS": ("PODE", "PODEMOS"),  # grafia da Câmara × do Senado
    "AGIR": ("PTC", "AGIR"),  # renomeação, 2022
    "SEM PARTIDO": ("S.PART.", "S/PARTIDO"),  # grafia da Câmara × do Senado
}

#: Fusões e incorporações: legenda -> texto do aviso.
SUCESSOES: dict[str, str] = {
    "UNIÃO": "O UNIÃO resulta da fusão de DEM e PSL (2022).",
    "PRD": "O PRD resulta da fusão de PTB e PATRIOTA (2023).",
    "PODEMOS": "O PODEMOS incorporou o PHS (2019) e o PSC (2023).",
    "SOLIDARIEDADE": "O SOLIDARIEDADE incorporou o PROS (2023).",
    "PCDOB": "O PCdoB incorporou o PPL (2019).",
    "PATRIOTA": "O PATRIOTA incorporou o PRP (2019) e depois se fundiu com o PTB no PRD (2023).",
    "DEM": "O DEM se fundiu com o PSL no UNIÃO (2022).",
    "PSL": "O PSL se fundiu com o DEM no UNIÃO (2022).",
    "PTB": "O PTB se fundiu com o PATRIOTA no PRD (2023).",
    "PSC": "O PSC foi incorporado pelo PODEMOS (2023).",
    "PHS": "O PHS foi incorporado pelo PODEMOS (2019).",
    "PROS": "O PROS foi incorporado pelo SOLIDARIEDADE (2023).",
    "PPL": "O PPL foi incorporado pelo PCdoB (2019).",
    "PRP": "O PRP foi incorporado pelo PATRIOTA (2019).",
}

_LEGENDA_DA_SIGLA = {
    sigla: legenda for legenda, siglas in LEGENDAS.items() for sigla in siglas
}
_LEGENDA_DA_SIGLA.update({"S/PARTIDO": "SEM PARTIDO", "SEM PARTIDO": "SEM PARTIDO"})


def legenda(sigla: str) -> str:
    """Nome atual da legenda de uma sigla (a própria sigla, se não houver alias)."""
    s = (sigla or "").strip().upper()
    return _LEGENDA_DA_SIGLA.get(s, s)


def siglas_da_legenda(sigla: str) -> tuple[str, ...]:
    """Todas as siglas, em maiúsculas, que designam a mesma legenda de `sigla`."""
    nome = legenda(sigla)
    return LEGENDAS.get(nome, (nome,))


def aviso_de_sucessao(sigla: str) -> Optional[str]:
    """Aviso de fusão/incorporação que envolve a legenda, se houver."""
    return SUCESSOES.get(legenda(sigla))
