"""
Persistência de usuários e chamadas em `usuarios.db` (SQLite, modo WAL).

É o ÚNICO arquivo que o servidor escreve. Fica separado do `leis.db`, que é
somente leitura, e sobrevive a trocas do banco legislativo.

Convenções:
- datas em UTC, ISO 8601 (`2026-09-16T08:30:00+00:00`), comparáveis como texto;
- `cota_diaria`: NULL usa a cota padrão (LEIS_COTA_PADRAO); -1 é ilimitada;
  0 bloqueia o uso sem bloquear a conta. Admins nunca têm cota;
- argumentos das chamadas são guardados integralmente e sem prazo de expiração.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from leis_mcp.config import PAPEIS

COTA_ILIMITADA = -1
STATUS_CHAMADA = ("ok", "erro", "negado_cota", "negado_papel", "negado_permissao")
#: Status que consomem cota: a ferramenta rodou (com ou sem erro).
STATUS_QUE_CONSOMEM = ("ok", "erro")

_ESQUEMA = f"""
CREATE TABLE IF NOT EXISTS usuarios (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT    NOT NULL UNIQUE,
    nome           TEXT,
    papel          TEXT    NOT NULL CHECK (papel IN ({",".join(repr(p) for p in PAPEIS)})),
    cota_diaria    INTEGER,
    plano          TEXT,
    assinatura_id  TEXT,
    observacao     TEXT,
    criado_em      TEXT    NOT NULL,
    ultimo_acesso  TEXT
);

CREATE TABLE IF NOT EXISTS chamadas (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id        INTEGER NOT NULL REFERENCES usuarios(id),
    ferramenta        TEXT    NOT NULL,
    argumentos        TEXT,
    status            TEXT    NOT NULL CHECK (status IN ({",".join(repr(s) for s in STATUS_CHAMADA)})),
    erro              TEXT,
    duracao_ms        INTEGER,
    tamanho_resposta  INTEGER,
    criado_em         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chamadas_usuario_data ON chamadas (usuario_id, criado_em);
CREATE INDEX IF NOT EXISTS idx_chamadas_data ON chamadas (criado_em);
CREATE INDEX IF NOT EXISTS idx_chamadas_ferramenta ON chamadas (ferramenta, criado_em);
"""


def agora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Usuario:
    id: int
    email: str
    nome: Optional[str]
    papel: str
    cota_diaria: Optional[int]
    plano: Optional[str]
    criado_em: str
    ultimo_acesso: Optional[str]

    @classmethod
    def da_linha(cls, r: sqlite3.Row) -> "Usuario":
        return cls(
            id=r["id"],
            email=r["email"],
            nome=r["nome"],
            papel=r["papel"],
            cota_diaria=r["cota_diaria"],
            plano=r["plano"],
            criado_em=r["criado_em"],
            ultimo_acesso=r["ultimo_acesso"],
        )


class Repositorio:
    def __init__(self, caminho: Path, fuso: str = "America/Sao_Paulo") -> None:
        self.caminho = caminho
        self.fuso = ZoneInfo(fuso)

    @contextmanager
    def _conexao(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.caminho, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def inicializar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conexao() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_ESQUEMA)

    # ------------------------------------------------------------------
    # Usuários
    # ------------------------------------------------------------------

    def obter(self, email: str) -> Optional[Usuario]:
        with self._conexao() as conn:
            r = conn.execute(
                "SELECT * FROM usuarios WHERE email = ?", (email.lower(),)
            ).fetchone()
        return Usuario.da_linha(r) if r else None

    def garantir(self, email: str, nome: Optional[str], papel_inicial: str) -> Usuario:
        """Busca o usuário, criando no primeiro acesso, e marca o último acesso."""
        email = email.lower()
        agora = agora_utc()
        with self._conexao() as conn:
            conn.execute(
                "INSERT INTO usuarios (email, nome, papel, criado_em, ultimo_acesso) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(email) DO UPDATE SET "
                "ultimo_acesso = excluded.ultimo_acesso, "
                "nome = COALESCE(usuarios.nome, excluded.nome)",
                (email, nome, papel_inicial, agora, agora),
            )
            r = conn.execute(
                "SELECT * FROM usuarios WHERE email = ?", (email,)
            ).fetchone()
        return Usuario.da_linha(r)

    def criar_ou_atualizar(
        self,
        email: str,
        papel: Optional[str] = None,
        cota_diaria: Optional[int] = None,
        nome: Optional[str] = None,
        observacao: Optional[str] = None,
        alterar_cota: bool = False,
    ) -> Usuario:
        email = email.lower()
        if papel is not None and papel not in PAPEIS:
            raise ValueError(f"Papel inválido: {papel}. Use um de {PAPEIS}.")
        with self._conexao() as conn:
            existe = conn.execute(
                "SELECT 1 FROM usuarios WHERE email = ?", (email,)
            ).fetchone()
            if not existe:
                conn.execute(
                    "INSERT INTO usuarios (email, nome, papel, cota_diaria, observacao, criado_em) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        email,
                        nome,
                        papel or "usuario",
                        cota_diaria,
                        observacao,
                        agora_utc(),
                    ),
                )
            else:
                if papel is not None:
                    conn.execute(
                        "UPDATE usuarios SET papel = ? WHERE email = ?", (papel, email)
                    )
                if alterar_cota:
                    conn.execute(
                        "UPDATE usuarios SET cota_diaria = ? WHERE email = ?",
                        (cota_diaria, email),
                    )
                if nome is not None:
                    conn.execute(
                        "UPDATE usuarios SET nome = ? WHERE email = ?", (nome, email)
                    )
                if observacao is not None:
                    conn.execute(
                        "UPDATE usuarios SET observacao = ? WHERE email = ?",
                        (observacao, email),
                    )
            r = conn.execute(
                "SELECT * FROM usuarios WHERE email = ?", (email,)
            ).fetchone()
        return Usuario.da_linha(r)

    def sincronizar_admins(self, emails: tuple[str, ...]) -> None:
        for email in emails:
            self.criar_ou_atualizar(email, papel="admin")

    def listar(self, papel: Optional[str] = None) -> list[dict[str, Any]]:
        inicio_hoje = self.inicio_do_dia_utc()
        sql = (
            "SELECT u.*, "
            "(SELECT COUNT(*) FROM chamadas c WHERE c.usuario_id = u.id "
            f" AND c.status IN {STATUS_QUE_CONSOMEM} AND c.criado_em >= ?) AS chamadas_hoje, "
            "(SELECT COUNT(*) FROM chamadas c WHERE c.usuario_id = u.id) AS chamadas_total "
            "FROM usuarios u"
        )
        params: list[Any] = [inicio_hoje]
        if papel:
            sql += " WHERE u.papel = ?"
            params.append(papel)
        sql += " ORDER BY u.papel, u.email"
        with self._conexao() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    # ------------------------------------------------------------------
    # Cotas e chamadas
    # ------------------------------------------------------------------

    def inicio_do_dia_utc(self) -> str:
        """Meia-noite de hoje no fuso da cota, convertida para UTC."""
        hoje = datetime.now(self.fuso).date()
        return (
            datetime.combine(hoje, time.min, tzinfo=self.fuso)
            .astimezone(timezone.utc)
            .isoformat(timespec="seconds")
        )

    def chamadas_hoje(self, usuario_id: int, ignorar: tuple[str, ...] = ()) -> int:
        """Chamadas que consomem cota hoje, sem contar as ferramentas em `ignorar`."""
        filtro = ""
        params: list[Any] = [usuario_id, self.inicio_do_dia_utc()]
        if ignorar:
            filtro = f" AND ferramenta NOT IN ({','.join('?' * len(ignorar))})"
            params.extend(ignorar)
        with self._conexao() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM chamadas WHERE usuario_id = ? AND status IN {STATUS_QUE_CONSOMEM} "
                "AND criado_em >= ?" + filtro,
                params,
            ).fetchone()[0]

    def reservar_chamada(
        self,
        usuario_id: int,
        ferramenta: str,
        argumentos: Optional[dict[str, Any]],
        limite: Optional[int],
        ignorar: tuple[str, ...] = (),
    ) -> tuple[Optional[int], int]:
        """
        Conta as chamadas de hoje e, se houver cota, grava a chamada ANTES de
        executá-la, na mesma transação (`BEGIN IMMEDIATE`).

        Contar só as chamadas já terminadas deixava passar da cota quem dispara
        várias em paralelo: todas liam a mesma contagem. Reservada aqui, a
        chamada em andamento já conta para as seguintes.

        Devolve (id da chamada, usadas antes dela); id None = cota esgotada.
        `limite` None = sem cota (admin, ferramenta livre, cota ilimitada).
        """
        filtro = ""
        params: list[Any] = [usuario_id, self.inicio_do_dia_utc()]
        if ignorar:
            filtro = f" AND ferramenta NOT IN ({','.join('?' * len(ignorar))})"
            params.extend(ignorar)
        with self._conexao() as conn:
            conn.execute("BEGIN IMMEDIATE")
            usadas = 0
            if limite is not None:
                usadas = conn.execute(
                    f"SELECT COUNT(*) FROM chamadas WHERE usuario_id = ? AND status IN {STATUS_QUE_CONSOMEM} "
                    "AND criado_em >= ?" + filtro,
                    params,
                ).fetchone()[0]
                if usadas >= limite:
                    return None, usadas
            cursor = conn.execute(
                "INSERT INTO chamadas (usuario_id, ferramenta, argumentos, status, criado_em) "
                "VALUES (?, ?, ?, 'ok', ?)",
                (
                    usuario_id,
                    ferramenta,
                    json.dumps(argumentos or {}, ensure_ascii=False, default=str),
                    agora_utc(),
                ),
            )
            return cursor.lastrowid, usadas

    def finalizar_chamada(
        self,
        chamada_id: int,
        status: str,
        duracao_ms: Optional[int] = None,
        tamanho_resposta: Optional[int] = None,
        erro: Optional[str] = None,
    ) -> None:
        with self._conexao() as conn:
            conn.execute(
                "UPDATE chamadas SET status = ?, duracao_ms = ?, tamanho_resposta = ?, erro = ? "
                "WHERE id = ?",
                (
                    status,
                    duracao_ms,
                    tamanho_resposta,
                    (erro or "")[:2000] or None,
                    chamada_id,
                ),
            )

    def cancelar_chamada(self, chamada_id: int) -> None:
        """Apaga uma reserva que não chegou a executar (servidor ocupado)."""
        with self._conexao() as conn:
            conn.execute("DELETE FROM chamadas WHERE id = ?", (chamada_id,))

    def registrar_chamada(
        self,
        usuario_id: int,
        ferramenta: str,
        argumentos: Optional[dict[str, Any]],
        status: str,
        duracao_ms: Optional[int] = None,
        tamanho_resposta: Optional[int] = None,
        erro: Optional[str] = None,
    ) -> None:
        with self._conexao() as conn:
            conn.execute(
                "INSERT INTO chamadas (usuario_id, ferramenta, argumentos, status, erro, "
                "duracao_ms, tamanho_resposta, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    usuario_id,
                    ferramenta,
                    json.dumps(argumentos or {}, ensure_ascii=False, default=str),
                    status,
                    (erro or "")[:2000] or None,
                    duracao_ms,
                    tamanho_resposta,
                    agora_utc(),
                ),
            )

    # ------------------------------------------------------------------
    # Relatórios
    # ------------------------------------------------------------------

    def relatorio(
        self, desde: Optional[str], ate: Optional[str], email: Optional[str]
    ) -> dict[str, Any]:
        filtros, params = ["1 = 1"], []
        if desde:
            filtros.append("c.criado_em >= ?")
            params.append(desde)
        if ate:
            filtros.append("c.criado_em < ?")
            params.append(ate)
        if email:
            filtros.append("u.email = ?")
            params.append(email.lower())
        onde = " AND ".join(filtros)
        base = f"FROM chamadas c JOIN usuarios u ON u.id = c.usuario_id WHERE {onde}"
        with self._conexao() as conn:
            geral = dict(
                conn.execute(
                    f"SELECT COUNT(*) AS chamadas, COUNT(DISTINCT c.usuario_id) AS usuarios, "
                    f"ROUND(AVG(c.duracao_ms)) AS duracao_media_ms {base}",
                    params,
                ).fetchone()
            )
            por_ferramenta = [
                dict(r)
                for r in conn.execute(
                    f"SELECT c.ferramenta, COUNT(*) AS chamadas, "
                    f"SUM(c.status = 'ok') AS ok, SUM(c.status = 'erro') AS erros, "
                    f"SUM(c.status LIKE 'negado%') AS negadas, "
                    f"ROUND(AVG(c.duracao_ms)) AS duracao_media_ms, "
                    f"MAX(c.duracao_ms) AS duracao_max_ms, "
                    f"ROUND(AVG(c.tamanho_resposta)) AS tamanho_medio {base} "
                    f"GROUP BY c.ferramenta ORDER BY chamadas DESC",
                    params,
                )
            ]
            por_usuario = [
                dict(r)
                for r in conn.execute(
                    f"SELECT u.email, u.papel, COUNT(*) AS chamadas, "
                    f"SUM(c.status LIKE 'negado%') AS negadas, MAX(c.criado_em) AS ultima {base} "
                    f"GROUP BY u.id ORDER BY chamadas DESC",
                    params,
                )
            ]
            por_dia = [
                dict(r)
                for r in conn.execute(
                    f"SELECT substr(c.criado_em, 1, 10) AS dia_utc, COUNT(*) AS chamadas {base} "
                    f"GROUP BY dia_utc ORDER BY dia_utc",
                    params,
                )
            ]
        return {
            "geral": geral,
            "por_ferramenta": por_ferramenta,
            "por_usuario": por_usuario,
            "por_dia": por_dia,
        }

    def chamadas(self, email: Optional[str], limite: int) -> list[dict[str, Any]]:
        sql = (
            "SELECT c.id, c.criado_em, u.email, c.ferramenta, c.status, c.duracao_ms, "
            "c.tamanho_resposta, c.argumentos, c.erro FROM chamadas c "
            "JOIN usuarios u ON u.id = c.usuario_id"
        )
        params: list[Any] = []
        if email:
            sql += " WHERE u.email = ?"
            params.append(email.lower())
        sql += " ORDER BY c.id DESC LIMIT ?"
        params.append(limite)
        with self._conexao() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def iterar_chamadas(self, desde: Optional[str]) -> Iterator[sqlite3.Row]:
        sql = (
            "SELECT c.id, c.criado_em, u.email, u.papel, c.ferramenta, c.status, "
            "c.duracao_ms, c.tamanho_resposta, c.argumentos, c.erro "
            "FROM chamadas c JOIN usuarios u ON u.id = c.usuario_id"
        )
        params: list[Any] = []
        if desde:
            sql += " WHERE c.criado_em >= ?"
            params.append(desde)
        sql += " ORDER BY c.id"
        with self._conexao() as conn:
            yield from conn.execute(sql, params)
