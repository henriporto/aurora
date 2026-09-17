# /// script
# requires-python = ">=3.10"
# dependencies = ["huggingface_hub>=0.34", "python-dotenv>=1", "zstandard>=0.23"]
# ///
"""
Baixa o leis.db do Hugging Face, descomprime e instala em `dados/leis.db`.

    uv run scripts/baixar_banco.py [--repo USUARIO/REPO] [--forcar]

Repositório, revisão e destino vêm do `.env` (ver `scripts/config.py`); os
argumentos, quando passados, têm prioridade.

O download (~2 GB) é retomado se cair no meio. O banco descomprimido (~9 GB) é
conferido pelo SHA-256 antes de ocupar o lugar de `dados/leis.db`, e o arquivo
comprimido é apagado no fim. Precisa de ~11 GB livres durante a instalação.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import zstandard
from huggingface_hub import hf_hub_download

from config import DB_PATH, HF_REPO, HF_REVISAO

BLOCO = 8 * 1024 * 1024


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--repo",
        default=HF_REPO,
        help=f"Dataset no Hugging Face (padrão {HF_REPO}).",
    )
    parser.add_argument(
        "--revisao", default=HF_REVISAO, help="Branch, tag ou commit do dataset."
    )
    parser.add_argument("--destino", type=Path, default=DB_PATH)
    parser.add_argument(
        "--forcar", action="store_true", help="Substitui um leis.db já existente."
    )
    args = parser.parse_args()

    if args.destino.exists() and not args.forcar:
        sys.exit(f"Já existe {args.destino}. Use --forcar para substituir.")
    pasta = args.destino.parent
    pasta.mkdir(parents=True, exist_ok=True)
    temporaria = pasta / ".download"

    def baixar(nome: str) -> Path:
        return Path(
            hf_hub_download(
                args.repo,
                nome,
                repo_type="dataset",
                revision=args.revisao,
                local_dir=temporaria,
            )
        )

    print(f"Baixando de huggingface.co/datasets/{args.repo} ...", flush=True)
    manifesto = json.loads(baixar("leis.db.json").read_text())
    comprimido = baixar(manifesto["arquivo"])

    livre = shutil.disk_usage(pasta).free
    if livre < manifesto["bytes"]:
        sys.exit(
            f"Espaço insuficiente em {pasta}: {manifesto['bytes'] / 1e9:.1f} GB necessários, {livre / 1e9:.1f} GB livres."
        )

    print(f"Descomprimindo para {args.destino} ...", flush=True)
    parcial = args.destino.with_name(args.destino.name + ".parcial")
    h = hashlib.sha256()
    escritos = 0
    with comprimido.open("rb") as entrada, parcial.open("wb") as saida:
        leitor = zstandard.ZstdDecompressor().stream_reader(entrada, read_size=BLOCO)
        for bloco in iter(lambda: leitor.read(BLOCO), b""):
            saida.write(bloco)
            h.update(bloco)
            escritos += len(bloco)
            print(
                f"\r  {100 * escritos / manifesto['bytes']:5.1f}%", end="", flush=True
            )
    print()

    if h.hexdigest() != manifesto["sha256"] or escritos != manifesto["bytes"]:
        parcial.unlink()
        sys.exit(
            "O banco descomprimido não confere com o SHA-256 publicado. Rode de novo."
        )

    parcial.replace(args.destino)
    shutil.rmtree(temporaria, ignore_errors=True)
    print(
        f"Pronto: {args.destino} ({escritos / 1e9:.2f} GB, gerado em {manifesto.get('gerado_em', '?')})."
    )


if __name__ == "__main__":
    main()
