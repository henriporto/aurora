"""
Configuração do servidor, lida uma vez das variáveis de ambiente.

Nenhum outro módulo lê `os.environ` diretamente: a lista completa de variáveis
reconhecidas é este arquivo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PAPEIS = ("admin", "usuario", "pendente", "bloqueado")


class ConfiguracaoInvalida(RuntimeError):
    """Variável de ambiente ausente ou com valor fora do domínio."""


def _texto(nome: str, padrao: str = "") -> str:
    return os.environ.get(nome, padrao).strip()


def _inteiro(nome: str, padrao: int) -> int:
    bruto = _texto(nome)
    if not bruto:
        return padrao
    try:
        return int(bruto)
    except ValueError as e:
        raise ConfiguracaoInvalida(
            f"{nome} deve ser inteiro, recebido {bruto!r}"
        ) from e


def _real(nome: str, padrao: float) -> float:
    bruto = _texto(nome)
    if not bruto:
        return padrao
    try:
        return float(bruto)
    except ValueError as e:
        raise ConfiguracaoInvalida(f"{nome} deve ser número, recebido {bruto!r}") from e


def _booleano(nome: str, padrao: bool) -> bool:
    bruto = _texto(nome).lower()
    if not bruto:
        return padrao
    return bruto not in ("0", "false", "nao", "não", "off", "no")


def _lista(nome: str) -> tuple[str, ...]:
    return tuple(p.strip().lower() for p in _texto(nome).split(",") if p.strip())


@dataclass(frozen=True)
class Config:
    # ---- Banco legislativo (somente leitura) --------------------------------
    db_path: Path
    #: Abre com `immutable=1` (sem locks): só para banco que não muda com o servidor no ar.
    db_imutavel: bool

    # ---- Banco de usuários (leitura e escrita) ------------------------------
    usuarios_db_path: Path

    # ---- Transporte ---------------------------------------------------------
    transporte: str
    host: str
    porta: int
    #: Permite LEIS_AUTH=off fora de 127.0.0.1. Só para o contêiner de desenvolvimento.
    permitir_sem_auth_em_rede: bool

    # ---- Autenticação -------------------------------------------------------
    auth: str
    url_publica: str
    google_client_id: str
    google_client_secret: str
    jwt_chave: str
    #: Identidade usada quando a autenticação está desligada (desenvolvimento).
    email_dev: str

    # ---- Usuários e cotas ---------------------------------------------------
    admins: tuple[str, ...]
    papel_padrao: str
    cota_padrao: int
    fuso_cota: str

    # ---- Busca --------------------------------------------------------------
    embedding_model: str
    #: Prefixo das consultas (não dos documentos), exigido pelo Qwen3-Embedding.
    embedding_instrucao: str
    #: Proposições por busca de ementas.
    top_k: int
    top_k_maximo: int
    top_k_inteiro_teor: int
    top_k_inteiro_teor_maximo: int
    threshold: float
    threshold_inteiro_teor: float
    buscas_simultaneas: int
    #: Espera máxima por vaga de ferramenta pesada; depois disso, "servidor ocupado".
    espera_maxima_seg: float
    aquecer_na_partida: bool

    # ---- Inteiro teor sob demanda -------------------------------------------
    #: Baixa da Câmara/Senado o texto de proposições sem inteiro teor indexado (só em memória).
    sob_demanda: bool
    sob_demanda_max_proposicoes: int
    sob_demanda_timeout_seg: float
    sob_demanda_max_mb: int

    # ---- Tamanho das respostas ----------------------------------------------
    #: Caracteres por página de resposta (o claude.ai aceita ~150 mil por resultado).
    max_chars_pagina: int
    #: Tempo que o resultado de uma busca fica em memória para servir as páginas seguintes.
    cache_ttl_seg: int

    # ---- Segurança e limites ------------------------------------------------
    #: Limite de tempo do SQL livre (o claude.ai abandona a chamada em 240 s). 0 desliga.
    sql_timeout_seg: float
    mascarar_erros: bool

    log_nivel: str = field(default="INFO")

    @property
    def auth_ligada(self) -> bool:
        return self.auth != "off"


@lru_cache(maxsize=1)
def obter_config() -> Config:
    auth = _texto("LEIS_AUTH", "google").lower()
    if auth not in ("google", "off"):
        raise ConfiguracaoInvalida("LEIS_AUTH deve ser 'google' ou 'off'.")

    transporte = _texto("LEIS_TRANSPORTE", "http").lower()
    if transporte not in ("http", "stdio"):
        raise ConfiguracaoInvalida("LEIS_TRANSPORTE deve ser 'http' ou 'stdio'.")

    papel_padrao = _texto("LEIS_PAPEL_PADRAO", "pendente").lower()
    if papel_padrao not in PAPEIS:
        raise ConfiguracaoInvalida(f"LEIS_PAPEL_PADRAO deve ser um de {PAPEIS}.")

    # Padrão: o banco dentro do próprio projeto (dados/leis.db, fora do git).
    db = _texto("LEIS_DB_PATH", "dados/leis.db")

    return Config(
        db_path=Path(db).expanduser(),
        db_imutavel=_booleano("LEIS_DB_IMUTAVEL", False),
        usuarios_db_path=Path(
            _texto("LEIS_USUARIOS_DB_PATH", "dados/usuarios.db")
        ).expanduser(),
        transporte=transporte,
        host=_texto("LEIS_HOST", "127.0.0.1"),
        porta=_inteiro("LEIS_PORTA", 8000),
        permitir_sem_auth_em_rede=_booleano("LEIS_PERMITIR_SEM_AUTH_EM_REDE", False),
        auth=auth,
        url_publica=_texto("LEIS_URL_PUBLICA").rstrip("/"),
        google_client_id=_texto("GOOGLE_CLIENT_ID"),
        google_client_secret=_texto("GOOGLE_CLIENT_SECRET"),
        jwt_chave=_texto("LEIS_JWT_CHAVE"),
        email_dev=_texto("LEIS_EMAIL_DEV", "dev@localhost").lower(),
        admins=_lista("LEIS_ADMINS"),
        papel_padrao=papel_padrao,
        cota_padrao=_inteiro("LEIS_COTA_PADRAO", 30),
        fuso_cota=_texto("LEIS_FUSO_COTA", "America/Sao_Paulo"),
        embedding_model=_texto("LEIS_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
        embedding_instrucao=_texto(
            "LEIS_EMBEDDING_INSTRUCAO",
            "Instruct: Given a topic of Brazilian legislation, retrieve summaries (ementas) "
            "of bills that deal with this topic\nQuery: ",
        ),
        top_k=_inteiro("LEIS_TOP_K", 40),
        top_k_maximo=_inteiro("LEIS_TOP_K_MAXIMO", 500),
        top_k_inteiro_teor=_inteiro("LEIS_TOP_K_INTEIRO_TEOR", 8),
        top_k_inteiro_teor_maximo=_inteiro("LEIS_TOP_K_INTEIRO_TEOR_MAXIMO", 100),
        # 0 = sem corte por cosseno: a ordem vem do RRF, e a escala depende do modelo.
        threshold=_real("LEIS_THRESHOLD", 0.0),
        threshold_inteiro_teor=_real("LEIS_THRESHOLD_INTEIRO_TEOR", 0.0),
        buscas_simultaneas=max(1, _inteiro("LEIS_BUSCAS_SIMULTANEAS", 2)),
        espera_maxima_seg=max(1.0, _real("LEIS_ESPERA_MAXIMA_SEG", 45.0)),
        aquecer_na_partida=_booleano("LEIS_AQUECER", True),
        sob_demanda=_booleano("LEIS_SOB_DEMANDA", True),
        sob_demanda_max_proposicoes=max(
            0, _inteiro("LEIS_SOB_DEMANDA_MAX_PROPOSICOES", 5)
        ),
        sob_demanda_timeout_seg=_real("LEIS_SOB_DEMANDA_TIMEOUT_SEG", 20.0),
        sob_demanda_max_mb=max(1, _inteiro("LEIS_SOB_DEMANDA_MAX_MB", 30)),
        max_chars_pagina=max(10_000, _inteiro("LEIS_MAX_CHARS_PAGINA", 100_000)),
        cache_ttl_seg=_inteiro("LEIS_CACHE_TTL_SEG", 900),
        sql_timeout_seg=_real("LEIS_SQL_TIMEOUT_SEG", 230.0),
        mascarar_erros=_booleano("LEIS_MASCARAR_ERROS", auth != "off"),
        log_nivel=_texto("LEIS_LOG_NIVEL", "INFO").upper(),
    )


def validar_para_servir(cfg: Config) -> None:
    """
    Recusa combinações perigosas antes de abrir a porta, como autenticação
    desligada fora de 127.0.0.1 (exporia inclusive o SQL livre).
    """
    if not cfg.db_path.is_file():
        raise ConfiguracaoInvalida(f"Banco não encontrado em {cfg.db_path}.")

    if cfg.transporte == "http" and not cfg.auth_ligada:
        local = cfg.host in ("127.0.0.1", "localhost", "::1")
        if not local and not cfg.permitir_sem_auth_em_rede:
            raise ConfiguracaoInvalida(
                "LEIS_AUTH=off só é aceito com LEIS_HOST=127.0.0.1. Para o "
                "contêiner de desenvolvimento, defina também "
                "LEIS_PERMITIR_SEM_AUTH_EM_REDE=1 e publique a porta só em 127.0.0.1."
            )

    if cfg.auth_ligada:
        faltando = [
            nome
            for nome, valor in (
                ("GOOGLE_CLIENT_ID", cfg.google_client_id),
                ("GOOGLE_CLIENT_SECRET", cfg.google_client_secret),
                ("LEIS_JWT_CHAVE", cfg.jwt_chave),
                ("LEIS_URL_PUBLICA", cfg.url_publica),
            )
            if not valor
        ]
        if faltando:
            raise ConfiguracaoInvalida(
                "Autenticação Google ligada, mas faltam: " + ", ".join(faltando)
            )
        if len(cfg.jwt_chave) < 32:
            raise ConfiguracaoInvalida(
                "LEIS_JWT_CHAVE deve ter ao menos 32 caracteres."
            )
