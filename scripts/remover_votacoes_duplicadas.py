#!/usr/bin/env python3
"""
Remove votações gravadas duas vezes com IDs diferentes (mesma votação na fonte).

Duplicata: mesma casa, mesma data até o minuto, mesma descrição, mesmo total e
voto idêntico de cada parlamentar. Só remove os pares listados em `MANTER`,
depois de conferir voto a voto; duplicata nova é apenas relatada.

As linhas removidas vão para `votos_removidos` e o ID para
`votacoes_removidas`, que os ETLs de votação consultam para não reinserir.
Para desfazer: `INSERT INTO votos SELECT * FROM votos_removidos WHERE id_votacao = ?`.

    uv run python scripts/remover_votacoes_duplicadas.py --banco dados/leis.db [--aplicar]
    uv run --group etl python etl/criar_tabela_votacoes.py --db dados/leis.db   # depois
"""

from __future__ import annotations

import argparse
import sqlite3

#: (id que fica, id removido, motivo). Medido em 2026-09-16.
MANTER: list[tuple[str, str, str]] = [
    (
        "531331-317",
        "2293985-5",
        "mesma votação (PEC 125/2011, 17/08/2021) gravada duas vezes na mesma proposição",
    ),
    (
        "2414224-6",
        "259094-181",
        "quebra de interstício da PEC 45/2019 (15/12/2023): a API atribui 2414224-6 à PEC 45/2019 "
        "(2403910), e 259094-181 repete a votação no processo principal, a PEC 293/2004",
    ),
    (
        "SEN-6780",
        "SEN-6779",
        "requerimento sobre a PEC 8/2021: fica o registro na matéria, sai a cópia no RQS 1039/2023",
    ),
    (
        "SEN-6799",
        "SEN-6796",
        "requerimento sobre o PL 3626/2023: fica o registro na matéria, sai a cópia no RQS 1103/2023",
    ),
    (
        "SEN-6835",
        "SEN-6834",
        "requerimento sobre o PL 1958/2021: fica o registro na matéria, sai a cópia no RQS 369/2024",
    ),
    (
        "SEN-7034",
        "SEN-7031",
        "requerimento sobre a PEC 48/2023: fica o registro na matéria, sai a cópia no RQS 911/2025",
    ),
]

DDL = """
CREATE TABLE IF NOT EXISTS votos_removidos AS SELECT * FROM votos WHERE 0;
CREATE TABLE IF NOT EXISTS votacoes_removidas (
    id_votacao  TEXT PRIMARY KEY,
    duplicata_de TEXT NOT NULL,
    motivo      TEXT NOT NULL
);
"""


def votos_de(conn: sqlite3.Connection, id_votacao: str) -> dict[int, str]:
    return dict(
        conn.execute(
            "SELECT id_parlamentar, tipo_voto FROM votos WHERE id_votacao = ?",
            (id_votacao,),
        )
    )


def candidatos(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return conn.execute(
        """
        SELECT a.id_votacao, b.id_votacao FROM votacoes a JOIN votacoes b
          ON a.casa = b.casa AND substr(a.data, 1, 16) = substr(b.data, 1, 16)
         AND a.descricao = b.descricao AND a.total_votos = b.total_votos
         AND a.id_votacao < b.id_votacao
        -- Só votação com voto individual: é de `votos` que este script apaga,
        -- e uma simbólica não tem o que apagar. Hoje elas já ficariam de fora
        -- sozinhas, porque `total_votos` é NULL nelas e NULL = NULL é falso em
        -- SQL — mas isso é acidente de semântica, não intenção declarada.
        WHERE a.tem_voto_nominal = 1 AND b.tem_voto_nominal = 1
        """
    ).fetchall()


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--banco", required=True)
    ap.add_argument("--aplicar", action="store_true", help="sem isto, só relata")
    args = ap.parse_args()

    conn = sqlite3.connect(args.banco)
    conn.executescript(DDL)
    decididos = {frozenset((m, r)) for m, r, _ in MANTER}

    for a, b in candidatos(conn):
        va, vb = votos_de(conn, a), votos_de(conn, b)
        identicos = va == vb
        situacao = (
            "em MANTER"
            if frozenset((a, b)) in decididos
            else "NÃO decidida (só relatada)"
        )
        print(
            f"{a} × {b}: {'idênticas' if identicos else 'votos diferentes: não é duplicata'}; {situacao}"
        )

    for manter, remover, motivo in MANTER:
        vm, vr = votos_de(conn, manter), votos_de(conn, remover)
        if not vr:
            print(f"{remover}: já removida")
            continue
        if vm != vr:
            raise SystemExit(
                f"{manter} × {remover}: votos diferentes; nada removido. Revise MANTER."
            )
        print(f"{remover} -> duplicata de {manter} ({len(vr)} votos): {motivo}")
        if args.aplicar:
            with conn:
                conn.execute(
                    "INSERT INTO votos_removidos SELECT * FROM votos WHERE id_votacao = ?",
                    (remover,),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO votacoes_removidas VALUES (?, ?, ?)",
                    (remover, manter, motivo),
                )
                conn.execute("DELETE FROM votos WHERE id_votacao = ?", (remover,))
    if not args.aplicar:
        print("\nNada foi alterado (use --aplicar).")
    conn.close()


if __name__ == "__main__":
    main()
