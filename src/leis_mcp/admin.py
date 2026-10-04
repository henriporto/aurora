"""
`leis-admin`: administração de usuários, cotas e relatórios de uso.

    leis-admin usuarios listar [--papel pendente]
    leis-admin usuarios adicionar EMAIL [--papel usuario] [--cota 50] [--nome "..."]
    leis-admin usuarios aprovar EMAIL           # pendente -> usuario
    leis-admin usuarios papel EMAIL admin|usuario|pendente|bloqueado
    leis-admin usuarios cota EMAIL 100|ilimitada|padrao
    leis-admin relatorio [--desde 2026-10-01] [--ate 2026-11-01] [--email X]
    leis-admin chamadas [--email X] [--limite 20]
    leis-admin exportar ARQUIVO.csv [--desde 2026-10-01] [--anonimizar]

Lê apenas LEIS_USUARIOS_DB_PATH (e LEIS_FUSO_COTA); não precisa do banco
legislativo nem do modelo. No contêiner:

    docker compose exec leis-mcp leis-admin usuarios listar
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from leis_mcp.config import PAPEIS
from leis_mcp.usuarios.repositorio import COTA_ILIMITADA, Repositorio


def _repositorio() -> Repositorio:
    caminho = Path(
        os.environ.get("LEIS_USUARIOS_DB_PATH", "dados/usuarios.db")
    ).expanduser()
    repo = Repositorio(caminho, os.environ.get("LEIS_FUSO_COTA", "America/Sao_Paulo"))
    repo.inicializar()
    return repo


def _tabela(linhas: list[dict[str, Any]], colunas: list[str]) -> None:
    if not linhas:
        print("(nenhum registro)")
        return
    larguras = {
        c: max(
            len(c),
            *(len(str(l.get(c) if l.get(c) is not None else "")) for l in linhas),
        )
        for c in colunas
    }
    print("  ".join(c.ljust(larguras[c]) for c in colunas))
    print("  ".join("-" * larguras[c] for c in colunas))
    for l in linhas:
        print(
            "  ".join(
                str(l.get(c) if l.get(c) is not None else "").ljust(larguras[c])
                for c in colunas
            )
        )


def _cota_legivel(valor: Optional[int]) -> str:
    if valor is None:
        return "padrão"
    if valor == COTA_ILIMITADA:
        return "ilimitada"
    return str(valor)


def _data_iso(texto: Optional[str]) -> Optional[str]:
    """Aceita AAAA-MM-DD e completa para comparar com as datas UTC gravadas."""
    if not texto:
        return None
    return texto if "T" in texto else f"{texto}T00:00:00+00:00"


def cmd_usuarios(args: argparse.Namespace) -> None:
    repo = _repositorio()
    if args.acao == "listar":
        linhas = repo.listar(args.papel)
        for l in linhas:
            l["cota"] = (
                "—" if l["papel"] == "admin" else _cota_legivel(l["cota_diaria"])
            )
        _tabela(
            linhas,
            [
                "email",
                "nome",
                "papel",
                "cota",
                "chamadas_hoje",
                "chamadas_total",
                "ultimo_acesso",
            ],
        )
    elif args.acao == "adicionar":
        cota = None if args.cota is None else _interpretar_cota(args.cota)
        u = repo.criar_ou_atualizar(
            args.email,
            papel=args.papel,
            cota_diaria=cota,
            nome=args.nome,
            alterar_cota=args.cota is not None,
        )
        print(f"{u.email}: papel={u.papel}, cota={_cota_legivel(u.cota_diaria)}")
    elif args.acao == "aprovar":
        u = repo.criar_ou_atualizar(args.email, papel="usuario")
        print(f"{u.email} aprovado (papel=usuario).")
    elif args.acao == "papel":
        u = repo.criar_ou_atualizar(args.email, papel=args.papel_novo)
        print(f"{u.email}: papel={u.papel}")
    elif args.acao == "cota":
        u = repo.criar_ou_atualizar(
            args.email, cota_diaria=_interpretar_cota(args.valor), alterar_cota=True
        )
        print(f"{u.email}: cota={_cota_legivel(u.cota_diaria)}")


def _interpretar_cota(valor: str) -> Optional[int]:
    v = str(valor).strip().lower()
    if v in ("padrao", "padrão"):
        return None
    if v in ("ilimitada", "ilimitado", "-1"):
        return COTA_ILIMITADA
    try:
        n = int(v)
    except ValueError:
        sys.exit(f"Cota inválida: {valor}. Use um número, 'ilimitada' ou 'padrao'.")
    if n < 0:
        sys.exit("Cota não pode ser negativa (use 'ilimitada').")
    return n


def cmd_relatorio(args: argparse.Namespace) -> None:
    r = _repositorio().relatorio(_data_iso(args.desde), _data_iso(args.ate), args.email)
    g = r["geral"]
    print(
        f"Chamadas: {g['chamadas']} | usuários: {g['usuarios']} | duração média: {g['duracao_media_ms']} ms\n"
    )
    print("Por ferramenta")
    _tabela(
        r["por_ferramenta"],
        [
            "ferramenta",
            "chamadas",
            "ok",
            "erros",
            "negadas",
            "duracao_media_ms",
            "duracao_max_ms",
            "tamanho_medio",
        ],
    )
    print("\nPor usuário")
    _tabela(r["por_usuario"], ["email", "papel", "chamadas", "negadas", "ultima"])
    print("\nPor dia (UTC)")
    _tabela(r["por_dia"], ["dia_utc", "chamadas"])


def cmd_chamadas(args: argparse.Namespace) -> None:
    linhas = _repositorio().chamadas(args.email, args.limite)
    for l in linhas:
        l["argumentos"] = (l["argumentos"] or "")[:90]
    _tabela(
        linhas,
        [
            "id",
            "criado_em",
            "email",
            "ferramenta",
            "status",
            "duracao_ms",
            "argumentos",
        ],
    )


def cmd_exportar(args: argparse.Namespace) -> None:
    """CSV das chamadas. Com --anonimizar, o e-mail vira um hash com sal."""
    repo = _repositorio()
    sal = os.environ.get("LEIS_SAL_ANONIMIZACAO", "")
    if args.anonimizar and not sal:
        sys.exit(
            "Defina LEIS_SAL_ANONIMIZACAO com um texto secreto: sem sal, o hash de um "
            "e-mail conhecido pode ser recalculado por qualquer pessoa."
        )
    total = 0
    with open(args.arquivo, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(
            [
                "id",
                "criado_em_utc",
                "usuario",
                "papel",
                "ferramenta",
                "status",
                "duracao_ms",
                "tamanho_resposta",
                "argumentos",
                "erro",
            ]
        )
        for r in repo.iterar_chamadas(_data_iso(args.desde)):
            usuario = (
                hashlib.sha256((sal + r["email"]).encode()).hexdigest()[:16]
                if args.anonimizar
                else r["email"]
            )
            argumentos = r["argumentos"]
            if argumentos:
                argumentos = json.dumps(json.loads(argumentos), ensure_ascii=False)
            escritor.writerow(
                [
                    r["id"],
                    r["criado_em"],
                    usuario,
                    r["papel"],
                    r["ferramenta"],
                    r["status"],
                    r["duracao_ms"],
                    r["tamanho_resposta"],
                    argumentos,
                    r["erro"],
                ]
            )
            total += 1
    print(f"{total} chamadas exportadas para {args.arquivo}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="leis-admin", description="Administração do servidor da Aurora."
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    u = sub.add_parser("usuarios", help="Gerencia usuários, papéis e cotas.")
    us = u.add_subparsers(dest="acao", required=True)
    listar = us.add_parser("listar")
    listar.add_argument("--papel", choices=PAPEIS)
    adicionar = us.add_parser("adicionar")
    adicionar.add_argument("email")
    adicionar.add_argument("--papel", choices=PAPEIS, default="usuario")
    adicionar.add_argument("--cota", help="Número, 'ilimitada' ou 'padrao'.")
    adicionar.add_argument("--nome")
    aprovar = us.add_parser("aprovar")
    aprovar.add_argument("email")
    papel = us.add_parser("papel")
    papel.add_argument("email")
    papel.add_argument("papel_novo", choices=PAPEIS)
    cota = us.add_parser("cota")
    cota.add_argument("email")
    cota.add_argument("valor", help="Número, 'ilimitada' ou 'padrao'.")
    u.set_defaults(funcao=cmd_usuarios)

    rel = sub.add_parser(
        "relatorio", help="Uso agregado por ferramenta, usuário e dia."
    )
    rel.add_argument("--desde", help="AAAA-MM-DD (UTC)")
    rel.add_argument("--ate", help="AAAA-MM-DD (UTC, exclusivo)")
    rel.add_argument("--email")
    rel.set_defaults(funcao=cmd_relatorio)

    ch = sub.add_parser("chamadas", help="Últimas chamadas, com argumentos.")
    ch.add_argument("--email")
    ch.add_argument("--limite", type=int, default=20)
    ch.set_defaults(funcao=cmd_chamadas)

    ex = sub.add_parser("exportar", help="Exporta chamadas para CSV.")
    ex.add_argument("arquivo")
    ex.add_argument("--desde", help="AAAA-MM-DD (UTC)")
    ex.add_argument("--anonimizar", action="store_true")
    ex.set_defaults(funcao=cmd_exportar)

    args = parser.parse_args()
    try:
        args.funcao(args)
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
