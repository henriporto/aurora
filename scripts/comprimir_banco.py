# /// script
# requires-python = ">=3.10"
# dependencies = ["python-dotenv>=1", "zstandard>=0.23"]
# ///
"""
Gera a cópia comprimida do leis.db para publicar no Hugging Face.

    uv run scripts/comprimir_banco.py [origem] [--nivel 9] [--verificar]
                                      [--arquivo-morto dados/backup]

Origem, pasta de saída e nível vêm do `.env` (ver `scripts/config.py`); os
argumentos, quando passados, têm prioridade. Saída em `LEIS_PUBLICAR_DIR`:

- `leis.db.zst`: o banco comprimido com zstd.
- `leis.db.json`: SHA-256 e tamanho do banco DESCOMPRIMIDO, que o
  `baixar_banco.py` usa para conferir a instalação do usuário.

A cópia passa antes pelo `preparar_banco.py` (API de backup do SQLite,
`journal_mode=DELETE`), então sai consistente e em arquivo único mesmo com o
banco de origem aberto. O nível 9 comprime o banco para ~19% a ~270 MB/s; o 19
ganha pouco mais de 1 ponto e é dezenas de vezes mais lento (no banco de 12 GB
com os discursos, o nível 9 rende ~33%: os vetores float32 que dominam o
arquivo são quase incompressíveis).

Se já houver um pacote em `LEIS_PUBLICAR_DIR`, ele é MOVIDO para
`--arquivo-morto` (padrão `dados/backup`) com o carimbo de quando foi gerado —
`leis-AAAAMMDD-HHMMSS.db.zst` —, e não sobrescrito. Antes o script abortava
pedindo limpeza manual: protegia o pacote publicado, mas empurrava para o
usuário um `rm` na pasta que contém exatamente o arquivo que está no ar.
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


def _carimbo_do_pacote(manifesto: Path) -> str:
    """Quando o pacote anterior foi gerado, para nomear a cópia arquivada."""
    try:
        gerado = json.loads(manifesto.read_text())["gerado_em"]
        return datetime.fromisoformat(gerado).strftime("%Y%m%d-%H%M%S")
    except Exception:  # noqa: BLE001 — manifesto ausente ou ilegível
        origem = manifesto if manifesto.exists() else None
        quando = datetime.fromtimestamp(origem.stat().st_mtime) if origem else datetime.now()
        return quando.strftime("%Y%m%d-%H%M%S")


def _arquivar_pacote_anterior(
    origem: Path, comprimido: Path, manifesto: Path, copia: Path, arquivo_morto: Path
) -> None:
    """
    Tira da pasta de publicação o pacote da rodada anterior, sem apagá-lo.

    Este script se recusava a sobrescrever e mandava o usuário limpar a pasta à
    mão — proteção correta (o pacote pode ser o que está publicado no Hugging
    Face), execução errada: a recusa acontecia DEPOIS de você já ter decidido
    republicar, e a limpeza manual é o tipo de passo que se faz com `rm` às
    pressas. Arquivar guarda a mesma coisa e não interrompe ninguém.
    """
    # `leis.db` na pasta de saída é a cópia temporária de uma execução
    # interrompida: o fluxo normal a apaga no fim. Não é pacote, é lixo.
    for lixo in (copia, Path(f"{copia}.sha256")):
        if lixo.exists():
            print(f"Removendo sobra de execução anterior: {lixo.name}")
            lixo.unlink()

    existentes = [c for c in (comprimido, manifesto) if c.exists()]
    if not existentes:
        return

    # Aviso antes do trabalho pesado: comprimir 12 GB leva minutos, e refazer o
    # pacote de um banco que não mudou é desperdício que dá para perceber aqui.
    try:
        anterior = json.loads(manifesto.read_text())
        if anterior.get("bytes") == origem.stat().st_size:
            bytes_br = f"{anterior['bytes']:,}".replace(",", ".")
            print(
                f"AVISO: o pacote em {comprimido.parent.name}/ declara {bytes_br} bytes, "
                f"o mesmo tamanho de {origem.name}."
            )
            print("       Pode ser o mesmo banco já empacotado. Ctrl+C para conferir antes.")
    except Exception:  # noqa: BLE001 — sem manifesto legível, segue em frente
        pass

    carimbo = _carimbo_do_pacote(manifesto)
    arquivo_morto.mkdir(parents=True, exist_ok=True)
    for caminho in existentes:
        sufixo = "".join(caminho.suffixes)  # .db.zst / .db.json
        destino = arquivo_morto / f"leis-{carimbo}{sufixo}"
        caminho.rename(destino)
        # `--arquivo-morto` pode apontar para fora do projeto; aí o caminho
        # relativo não existe e o absoluto é o que informa.
        try:
            rotulo = destino.relative_to(RAIZ)
        except ValueError:
            rotulo = destino
        print(f"Pacote anterior guardado em {rotulo}")


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
        "--arquivo-morto",
        type=Path,
        default=RAIZ / "dados" / "backup",
        help="Para onde vai o pacote da rodada anterior (padrão: dados/backup).",
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
    _arquivar_pacote_anterior(args.origem, comprimido, manifesto, copia, args.arquivo_morto)

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
