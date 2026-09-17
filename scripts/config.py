from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]

load_dotenv(RAIZ / ".env", override=False)


def _texto(nome: str, padrao: str = "") -> str:
    return os.environ.get(nome, padrao).strip() or padrao


def _caminho(nome: str, padrao: str) -> Path:
    caminho = Path(_texto(nome, padrao)).expanduser()
    return caminho if caminho.is_absolute() else RAIZ / caminho


def _inteiro(nome: str, padrao: int) -> int:
    bruto = _texto(nome)
    try:
        return int(bruto) if bruto else padrao
    except ValueError as e:
        raise SystemExit(f"{nome} deve ser inteiro, recebido {bruto!r}") from e


DB_PATH = _caminho("LEIS_DB_PATH", "dados/leis.db")
PUBLICAR_DIR = _caminho("LEIS_PUBLICAR_DIR", "dados/publicar")
ZSTD_NIVEL = _inteiro("LEIS_ZSTD_NIVEL", 9)
HF_REPO = _texto("LEIS_HF_REPO", "henriporto/aurora")
HF_REVISAO = _texto("LEIS_HF_REVISAO") or None
