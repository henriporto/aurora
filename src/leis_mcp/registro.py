"""
Log do processo.

Tudo vai para stderr: no transporte stdio, o stdout é o canal do protocolo MCP.
Por isso nenhum módulo deste pacote usa `print`.
"""

from __future__ import annotations

import logging
import sys


def configurar_log(nivel: str = "INFO") -> None:
    raiz = logging.getLogger()
    if any(getattr(h, "_leis", False) for h in raiz.handlers):
        return
    manipulador = logging.StreamHandler(sys.stderr)
    manipulador._leis = True  # type: ignore[attr-defined]
    manipulador.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    raiz.addHandler(manipulador)
    raiz.setLevel(nivel)
    # Bibliotecas ruidosas: o que interessa delas são avisos e erros.
    for ruidoso in ("httpx", "sentence_transformers", "urllib3", "filelock"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
