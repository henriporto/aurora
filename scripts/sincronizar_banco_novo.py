#!/usr/bin/env python3
"""
Copia o acervo legislativo de um banco para outro, sem tocar nos discursos.

PARA QUE ISTO FOI ESCRITO
-------------------------
Em 19/09/2026 havia dois bancos vivos: o `leis.db`, onde entraram as votações
simbólicas e a `proposicoes_situacao`, e um `leis_novo.db` montado em paralelo
com os discursos e seus vetores. O segundo era uma cópia tirada no MEIO da
ingestão do primeiro, então levou a ESTRUTURA nova e quase nada do conteúdo — e
levou um estado pior que vazio: `tem_voto_nominal = 0` nas 3.773 votações
nominais, porque a cópia caiu entre o `ALTER TABLE` (que preenche a coluna com o
DEFAULT) e o `UPDATE` que a sincroniza com `votos`. Como as ferramentas filtram
por `tem_voto_nominal = 1`, o servidor lendo aquele banco devolvia ZERO votação
nominal para tudo.

Este script fundiu os dois, e o resultado foi promovido a `dados/leis.db` (o
anterior está em `dados/backup/leis.old.db`). A fusão JÁ FOI FEITA: o script
fica como registro e para o caso de dois bancos voltarem a divergir, sempre com
`--origem` e `--destino` explícitos.

O QUE ELE NÃO TOCA
------------------
As tabelas `discursos*`. A cópia é num sentido só, origem -> destino, e a
última coisa que faz é recalcular `tem_voto_nominal` a partir dos `votos` do
destino — a rede de segurança contra exatamente o estado descrito acima.

Uso:
    .venv/bin/python scripts/sincronizar_banco_novo.py \\
        --origem dados/a.db --destino dados/b.db --conferir   # só compara
    .venv/bin/python scripts/sincronizar_banco_novo.py \\
        --origem dados/a.db --destino dados/b.db
"""
import os
import argparse
import sqlite3
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(SCRIPTS_DIR)

VERDE, AMARELO, VERMELHO, CINZA, FIM = (
    "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[0m",
)


def contagens(conn):
    def q(sql):
        return conn.execute(sql).fetchone()[0]

    return {
        "proposicoes": q("SELECT COUNT(*) FROM proposicoes"),
        "proposicoes_embeddings": q("SELECT COUNT(*) FROM proposicoes_embeddings"),
        "votacoes": q("SELECT COUNT(*) FROM votacoes"),
        "  simbólicas": q("SELECT COUNT(*) FROM votacoes WHERE tem_voto_nominal = 0"),
        "  nominais": q("SELECT COUNT(*) FROM votacoes WHERE tem_voto_nominal = 1"),
        "proposicoes_situacao": q("SELECT COUNT(*) FROM proposicoes_situacao"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Sem padrão, de propósito: o destino é sobrescrito em parte (a
    # `proposicoes_situacao` é apagada e recopiada), e um default apontando para
    # o banco de produção convidaria a rodar sem querer contra ele.
    ap.add_argument("--origem", required=True, help="banco de onde copiar")
    ap.add_argument("--destino", required=True, help="banco a ser atualizado")
    ap.add_argument("--conferir", action="store_true", help="só compara, não grava")
    args = ap.parse_args()

    ro = sqlite3.connect(f"file:{args.origem}?mode=ro", uri=True)
    antes_o = contagens(ro)
    ro.close()

    conn = sqlite3.connect(args.destino, timeout=120.0)
    conn.execute("PRAGMA busy_timeout = 120000;")
    antes_d = contagens(conn)

    rotulo_o = os.path.basename(args.origem)
    rotulo_d = os.path.basename(args.destino)
    print(f"{CINZA}{'':<26}{rotulo_o:>16}{rotulo_d:>18}{FIM}")
    for k in antes_o:
        marca = "" if antes_o[k] == antes_d[k] else f"  {AMARELO}<- difere{FIM}"
        print(f"  {k:<24}{antes_o[k]:>16,}{antes_d[k]:>18,}{marca}")

    if args.conferir:
        print(f"\n{CINZA}--conferir: nada foi gravado.{FIM}")
        return 0

    # A ordem importa: `proposicoes` primeiro, porque `proposicoes_situacao` e
    # `proposicoes_embeddings` têm FK para ela.
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(f"ATTACH DATABASE '{args.origem}' AS o;")
    try:
        cur = conn.cursor()
        print()

        n = cur.execute("""
            INSERT OR IGNORE INTO proposicoes
            SELECT * FROM o.proposicoes
            WHERE id_proposicao NOT IN (SELECT id_proposicao FROM main.proposicoes)
        """).rowcount
        print(f"  proposições novas: {n:,}{CINZA} (o trigger as põe no FTS){FIM}")

        n = cur.execute("""
            INSERT OR REPLACE INTO proposicoes_embeddings
            SELECT * FROM o.proposicoes_embeddings
            WHERE id_proposicao NOT IN (SELECT id_proposicao FROM main.proposicoes_embeddings)
        """).rowcount
        print(f"  embeddings de ementa: {n:,}")

        # `votacoes` de leis.db é a versão boa, inclusive o tem_voto_nominal já
        # sincronizado. REPLACE para trazer orgao/aprovacao às linhas existentes.
        n = cur.execute("INSERT OR REPLACE INTO votacoes SELECT * FROM o.votacoes").rowcount
        print(f"  votações (inseridas/atualizadas): {n:,}")

        cur.execute("DELETE FROM proposicoes_situacao")
        n = cur.execute("INSERT INTO proposicoes_situacao SELECT * FROM o.proposicoes_situacao").rowcount
        print(f"  situações: {n:,}")

        # Rede de segurança: mesmo que a cópia de `votacoes` falhasse, a coluna
        # derivada tem de refletir `votos` deste banco.
        cur.execute(
            "UPDATE votacoes SET tem_voto_nominal = "
            "(id_votacao IN (SELECT DISTINCT id_votacao FROM votos))"
        )
        conn.commit()
    finally:
        conn.execute("DETACH DATABASE o;")

    depois = contagens(conn)
    print(f"\n{CINZA}{'':<26}{rotulo_o:>16}{rotulo_d:>18}{FIM}")
    todas_iguais = True
    for k in antes_o:
        igual = antes_o[k] == depois[k]
        todas_iguais &= igual
        marca = f"  {VERDE}OK{FIM}" if igual else f"  {VERMELHO}AINDA DIFERE{FIM}"
        print(f"  {k:<24}{antes_o[k]:>16,}{depois[k]:>18,}{marca}")

    orfas = conn.execute(
        "SELECT COUNT(*) FROM proposicoes_situacao WHERE id_proposicao NOT IN "
        "(SELECT id_proposicao FROM proposicoes)"
    ).fetchone()[0]
    print(f"\n  linhas de situação órfãs: {orfas}")
    conn.close()
    return 0 if todas_iguais and orfas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
