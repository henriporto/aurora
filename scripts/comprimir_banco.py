# /// script
# requires-python = ">=3.10"
# dependencies = ["python-dotenv>=1", "zstandard>=0.23"]
# ///
"""
Gera a cópia comprimida do leis.db para publicar no Hugging Face.

    uv run scripts/comprimir_banco.py [origem] [--nivel 9] [--verificar]

Origem, pasta de saída e nível vêm do `.env` (ver `scripts/config.py`); os
argumentos, quando passados, têm prioridade. Saída em `LEIS_PUBLICAR_DIR`:

- `leis.db.zst`: o banco comprimido com zstd.
- `leis.db.json`: SHA-256 e tamanho do banco DESCOMPRIMIDO, que o
  `baixar_banco.py` usa para conferir a instalação do usuário.

A cópia passa antes pelo `preparar_banco.py` (API de backup do SQLite,
`journal_mode=DELETE`), então sai consistente e em arquivo único mesmo com o
banco de origem aberto. O nível 9 comprime o banco para ~19% a ~270 MB/s; o 19
ganha pouco mais de 1 ponto e é dezenas de vezes mais lento.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import zstandard

from config import DB_PATH, HF_REPO, PUBLICAR_DIR, RAIZ, ZSTD_NIVEL


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "origem", type=Path, nargs="?", default=DB_PATH
    )
    parser.add_argument("--saida", type=Path, default=PUBLICAR_DIR)
    parser.add_argument(
        "--nivel",
        type=int,
        default=ZSTD_NIVEL,
        help=f"Nível zstd, 1 a 22 (padrão {ZSTD_NIVEL}).",
    )
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="Roda PRAGMA quick_check na cópia antes de comprimir (alguns minutos).",
    )
    args = parser.parse_args()

    if not args.origem.is_file():
        sys.exit(f"Origem não encontrada: {args.origem}")
    args.saida.mkdir(parents=True, exist_ok=True)
    comprimido = args.saida / "leis.db.zst"
    manifesto = args.saida / "leis.db.json"
    copia = args.saida / "leis.db"
    for caminho in (comprimido, manifesto, copia):
        if caminho.exists():
            sys.exit(f"Já existe, não sobrescrevo: {caminho}")

    inicio = time.monotonic()
    comando = [
        sys.executable,
        str(RAIZ / "scripts" / "preparar_banco.py"),
        str(args.origem),
        str(copia),
    ]
    if args.verificar:
        comando.append("--verificar")
    subprocess.run(comando, check=True)

    try:
        arquivo_sha = Path(f"{copia}.sha256")
        digest = arquivo_sha.read_text().split()[0]
        tamanho = copia.stat().st_size

        print(
            f"Comprimindo com zstd nível {args.nivel} -> {comprimido} ...", flush=True
        )
        compressor = zstandard.ZstdCompressor(
            level=args.nivel, threads=-1, write_content_size=True
        )
        parcial = comprimido.with_suffix(".zst.parcial")
        with copia.open("rb") as entrada, parcial.open("wb") as saida:
            compressor.copy_stream(
                entrada, saida, size=tamanho, read_size=8 * 1024 * 1024
            )
        parcial.rename(comprimido)
    finally:
        copia.unlink(missing_ok=True)
        Path(f"{copia}.sha256").unlink(missing_ok=True)

    manifesto.write_text(
        json.dumps(
            {
                "arquivo": comprimido.name,
                "banco": "leis.db",
                "sha256": digest,
                "bytes": tamanho,
                "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        )
        + "\n"
    )

    final = comprimido.stat().st_size
    print(
        f"Pronto em {time.monotonic() - inicio:.0f}s: {tamanho / 1e9:.2f} GB -> "
        f"{final / 1e9:.2f} GB ({100 * final / tamanho:.0f}%)"
    )
    print("Para publicar (uma vez: uvx --from huggingface_hub hf auth login):")
    print(
        f"  uvx --from huggingface_hub hf upload {HF_REPO} {args.saida} . --repo-type dataset"
    )


if __name__ == "__main__":
    main()
