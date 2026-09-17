"""
Acesso ao `leis.db`, sempre somente leitura.

Todas as conexões com o banco legislativo nascem aqui. O servidor nunca
escreve nele: ingestão e indexação são trabalho do pipeline de ETL, feito fora
do servidor. Isso é garantido em duas camadas — `mode=ro` na URI, e o volume
montado como somente leitura no contêiner.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import quote

from leis_mcp.config import obter_config

TABELAS_OBRIGATORIAS = (
    "proposicoes",
    "parlamentares",
    "autoria",
    "relatorias",
    "votos",
    "votacoes",
    "proposicoes_fts",
    "proposicoes_embeddings",
    "proposicoes_chunks",
    "proposicoes_chunks_embeddings",
    "proposicoes_chunks_fts",
    "autores_proposicao",
)


def conectar(row_factory: bool = False) -> sqlite3.Connection:
    cfg = obter_config()
    uri = f"file:{quote(str(cfg.db_path))}?mode=ro"
    if cfg.db_imutavel:
        uri += "&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0, check_same_thread=False)
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def conexao(row_factory: bool = False) -> Iterator[sqlite3.Connection]:
    conn = conectar(row_factory=row_factory)
    try:
        yield conn
    finally:
        conn.close()


def limitar_tempo(conn: sqlite3.Connection, segundos: float) -> None:
    """
    Interrompe qualquer consulta que passe de `segundos`.

    O `timeout` de `sqlite3.connect` é só espera por lock, não tempo de
    execução: um JOIN cartesiano em `votos` (1,3 milhão de linhas) rodaria até
    acabar. O progress handler é chamado a cada N instruções da VM do SQLite e,
    ao devolver valor não nulo, aborta a consulta com `OperationalError`.
    """
    limite = time.monotonic() + segundos
    conn.set_progress_handler(lambda: 1 if time.monotonic() > limite else 0, 10_000)


def verificar_banco() -> dict:
    """Confere que o arquivo é o banco esperado. Usado na partida e em /saude."""
    with conexao() as conn:
        existentes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
    faltando = [t for t in TABELAS_OBRIGATORIAS if t not in existentes]
    return {"ok": not faltando, "tabelas_faltando": faltando}
