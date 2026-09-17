"""
`leis-mcp`: inicia o servidor.

    leis-mcp                              # HTTP em 127.0.0.1:8000 (padrão)
    leis-mcp --transporte stdio           # para clientes que iniciam o processo
    leis-mcp --host 0.0.0.0 --porta 8000  # dentro do contêiner

Opções de linha de comando sobrepõem as variáveis de ambiente equivalentes.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from leis_mcp.config import ConfiguracaoInvalida, obter_config, validar_para_servir
from leis_mcp.registro import configurar_log

logger = logging.getLogger("leis_mcp")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="leis-mcp", description="Servidor MCP do processo legislativo federal."
    )
    parser.add_argument("--transporte", choices=["http", "stdio"])
    parser.add_argument("--host")
    parser.add_argument("--porta", type=int)
    parser.add_argument(
        "--sem-aquecer",
        action="store_true",
        help="Não carrega modelo e acervo na partida.",
    )
    args = parser.parse_args()

    for opcao, variavel in (
        ("transporte", "LEIS_TRANSPORTE"),
        ("host", "LEIS_HOST"),
        ("porta", "LEIS_PORTA"),
    ):
        valor = getattr(args, opcao)
        if valor is not None:
            os.environ[variavel] = str(valor)
    if args.sem_aquecer:
        os.environ["LEIS_AQUECER"] = "0"

    try:
        cfg = obter_config()
        configurar_log(cfg.log_nivel)
        validar_para_servir(cfg)
    except ConfiguracaoInvalida as e:
        configurar_log()
        logger.error("Configuração inválida: %s", e)
        sys.exit(2)

    # Imports pesados só depois de a configuração ser validada.
    from leis_mcp.dados import cobertura
    from leis_mcp.dados.banco import verificar_banco
    from leis_mcp.dados.buscador import obter_buscador
    from leis_mcp.servidor import criar_servidor
    from leis_mcp.usuarios.repositorio import Repositorio

    banco = verificar_banco()
    if not banco["ok"]:
        logger.error(
            "Banco %s sem as tabelas: %s",
            cfg.db_path,
            ", ".join(banco["tabelas_faltando"]),
        )
        sys.exit(2)

    repositorio = Repositorio(cfg.usuarios_db_path, cfg.fuso_cota)
    repositorio.inicializar()
    repositorio.sincronizar_admins(cfg.admins)

    logger.info(
        "Banco: %s | usuários: %s | autenticação: %s | papel padrão: %s | cota padrão: %d",
        cfg.db_path,
        cfg.usuarios_db_path,
        cfg.auth,
        cfg.papel_padrao,
        cfg.cota_padrao,
    )

    if cfg.aquecer_na_partida:
        inicio = time.monotonic()
        cobertura.aquecer()
        obter_buscador().aquecer()
        logger.info("Aquecimento concluído em %.1fs", time.monotonic() - inicio)

    mcp = criar_servidor(cfg, repositorio)

    if cfg.transporte == "stdio":
        mcp.run(transport="stdio", show_banner=False)
    else:
        logger.info("Escutando em http://%s:%d/mcp", cfg.host, cfg.porta)
        mcp.run(
            transport="http",
            host=cfg.host,
            port=cfg.porta,
            show_banner=False,
            stateless_http=True,
            json_response=True,
        )


if __name__ == "__main__":
    main()
