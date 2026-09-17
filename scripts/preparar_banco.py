#!/usr/bin/env python3
"""
Prepara uma cópia do leis.db para produção.

    python scripts/preparar_banco.py /caminho/leis.db dados/leis-producao.db [--verificar]

Por que não copiar o arquivo direto:

1. O banco de desenvolvimento está em modo WAL. Um banco WAL aberto num volume
   somente leitura precisa dos arquivos -wal/-shm e falha de formas pouco
   óbvias. A cópia sai em `journal_mode=DELETE`, um arquivo único.
2. A API de backup do SQLite produz uma cópia consistente mesmo que o banco de
   origem esteja aberto por outro processo (o ETL, por exemplo).
3. O SHA-256 gravado ao lado permite conferir, na VM, que os 5,5 GB chegaram
   íntegros antes de trocar o banco em uso.

Só usa a biblioteca padrão: roda com qualquer Python 3.10+.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

TABELAS = (
    "proposicoes", "parlamentares", "autoria", "relatorias", "votos", "votacoes",
    "proposicoes_fts", "proposicoes_embeddings", "proposicoes_chunks",
    "proposicoes_chunks_embeddings", "proposicoes_chunks_fts",
)  # fmt: skip


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("origem", type=Path)
    parser.add_argument("destino", type=Path)
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="Roda PRAGMA quick_check na cópia (alguns minutos).",
    )
    args = parser.parse_args()

    if not args.origem.is_file():
        sys.exit(f"Origem não encontrada: {args.origem}")
    if args.destino.exists():
        sys.exit(f"Destino já existe, não sobrescrevo: {args.destino}")
    args.destino.parent.mkdir(parents=True, exist_ok=True)

    inicio = time.monotonic()
    origem = sqlite3.connect(f"file:{args.origem}?mode=ro", uri=True)
    faltando = [
        t for t in TABELAS
        if not origem.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
    ]  # fmt: skip
    if faltando:
        sys.exit(f"O banco de origem não tem as tabelas: {', '.join(faltando)}")

    destino = sqlite3.connect(args.destino)
    print(f"Copiando {args.origem} -> {args.destino} ...", flush=True)
    origem.backup(destino, pages=65536, progress=lambda status, restantes, total: print(
        f"\r  {100 * (total - restantes) / max(total, 1):5.1f}%", end="", flush=True
    ))  # fmt: skip
    print()
    origem.close()

    modo = destino.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
    if args.verificar:
        print("Verificando integridade (quick_check)...", flush=True)
        resultado = destino.execute("PRAGMA quick_check").fetchone()[0]
        if resultado != "ok":
            sys.exit(f"quick_check falhou: {resultado}")
    contagens = {
        t: destino.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("proposicoes", "votos", "votacoes")
    }
    destino.close()

    digest = sha256(args.destino)
    Path(f"{args.destino}.sha256").write_text(f"{digest}  {args.destino.name}\n")
    tamanho = args.destino.stat().st_size / 1e9
    print(
        f"Pronto em {time.monotonic() - inicio:.0f}s: {tamanho:.2f} GB, journal_mode={modo}, {contagens}"
    )
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    main()
