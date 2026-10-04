"""SQL livre, somente leitura. Exposto apenas ao papel `admin`."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from leis_mcp.config import obter_config
from leis_mcp.dados.banco import conexao, limitar_tempo
from leis_mcp.texto import br

_ACOES_PERMITIDAS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}


def _autorizador(acao, *_):
    return sqlite3.SQLITE_OK if acao in _ACOES_PERMITIDAS else sqlite3.SQLITE_DENY


def executar_consulta_sql(
    consulta: str, pagina: Optional[int] = None
) -> dict[str, Any]:
    """
    Executa a consulta inteira, sem teto de linhas; o resultado é paginado
    por LEIS_MAX_CHARS_PAGINA.

    Travas: conexão `mode=ro`, autorizador que só aceita leitura (bloqueia
    PRAGMA, ATTACH e escrita) e limite de tempo (230 s por padrão; 0 desliga).
    """
    cfg = obter_config()
    limite_pagina = cfg.max_chars_pagina - 3_000
    pagina = max(1, int(pagina or 1))

    with conexao() as conn:
        conn.set_authorizer(_autorizador)
        if cfg.sql_timeout_seg > 0:
            limitar_tempo(conn, cfg.sql_timeout_seg)
        try:
            cursor = conn.execute(consulta)
            colunas = [d[0] for d in cursor.description] if cursor.description else []

            pagina_atual, linha_inicial, acumulado = 1, 1, 0
            registros: list[dict[str, Any]] = []
            numero_linha = 0
            ha_mais = False
            for linha in cursor:
                numero_linha += 1
                registro = dict(zip(colunas, linha))
                tamanho = len(json.dumps(registro, ensure_ascii=False, default=str)) + 2
                if acumulado and acumulado + tamanho > limite_pagina:
                    if pagina_atual == pagina:
                        ha_mais = True
                        break
                    pagina_atual += 1
                    linha_inicial, acumulado = numero_linha, 0
                    registros = []
                acumulado += tamanho
                if pagina_atual == pagina:
                    registros.append(registro)
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e).lower():
                return {
                    "sucesso": False,
                    "erro": (
                        f"TEMPO_ESGOTADO: a consulta passou de {cfg.sql_timeout_seg:g} s. Clientes "
                        "como o claude.ai abandonam a chamada em 240 s; reescreva com filtros ou "
                        "índices (id_proposicao, id_parlamentar, id_votacao)."
                    ),
                }
            return {"sucesso": False, "erro": f"SQL inválido ou não permitido: {e}"}
        except sqlite3.DatabaseError as e:
            return {"sucesso": False, "erro": f"SQL inválido ou não permitido: {e}"}

        if pagina_atual < pagina:
            return {
                "sucesso": True,
                "colunas": colunas,
                "registros": [],
                "paginacao": {
                    "pagina": pagina,
                    "proxima_pagina": None,
                    "total_paginas": pagina_atual,
                },
                "aviso_sistema": f"A página {pagina} não existe: o resultado tem {pagina_atual} página(s).",
            }

        saida: dict[str, Any] = {
            "paginacao": {
                "completo": pagina == 1 and not ha_mais,
                "pagina": pagina,
                "linhas_de": linha_inicial if registros else 0,
                "linhas_ate": linha_inicial + len(registros) - 1 if registros else 0,
                "proxima_pagina": pagina + 1 if ha_mais else None,
            },
            "sucesso": True,
            "colunas": colunas,
            "registros": registros,
        }
        if ha_mais or pagina > 1:
            try:
                total = conn.execute(f"SELECT COUNT(*) FROM ({consulta})").fetchone()[0]
                saida["paginacao"]["total_de_linhas"] = total
            except sqlite3.DatabaseError:
                total = None
            if ha_mais:
                saida["aviso_sistema"] = (
                    f"RESULTADO PAGINADO: linhas {linha_inicial} a {linha_inicial + len(registros) - 1}"
                    + (f" de {br(total)}" if total is not None else "")
                    + f". As demais NÃO estão aqui: repita a mesma consulta com pagina={pagina + 1}. "
                    "Use ORDER BY para que a ordem entre páginas seja estável."
                )
        return saida
