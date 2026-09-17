"""
Controle de acesso aplicado a TODA chamada de ferramenta.

Ordem, em cada chamada:
1. identidade — e-mail do token do Google (ou o usuário de desenvolvimento);
2. usuário — criado no primeiro acesso com o papel padrão configurado;
3. papel — `bloqueado` e `pendente` são recusados; ferramentas marcadas com a
   tag `admin` exigem papel `admin`;
4. cota — chamadas de hoje (fuso configurado) contra a cota do usuário;
5. semáforo — ferramentas marcadas `pesada` disputam um número limitado de vagas,
   para que buscas simultâneas não esgotem a CPU e a RAM da VM;
6. execução e registro — tudo vai para `usuarios.db`, inclusive recusas.

Uma camada só, antes de todas as ferramentas: quem escreve uma ferramenta nova
não tem como esquecer de aplicar cota ou registro.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import anyio
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

from leis_mcp.config import Config
from leis_mcp.usuarios.repositorio import COTA_ILIMITADA, Repositorio, Usuario

logger = logging.getLogger(__name__)

TAG_ADMIN = "admin"
TAG_PESADA = "pesada"
#: Ferramentas que não consomem cota (o guia de pesquisa).
TAG_LIVRE = "livre"


@dataclass(frozen=True)
class Identidade:
    email: str
    nome: Optional[str]


class ControleDeAcesso(Middleware):
    def __init__(
        self,
        cfg: Config,
        repositorio: Repositorio,
        tags_por_ferramenta: dict[str, set[str]],
    ) -> None:
        self.cfg = cfg
        self.repo = repositorio
        self.tags_por_ferramenta = tags_por_ferramenta
        self._semaforo: Optional[asyncio.Semaphore] = None

    # ------------------------------------------------------------------

    def _vagas(self) -> asyncio.Semaphore:
        if self._semaforo is None:
            self._semaforo = asyncio.Semaphore(self.cfg.buscas_simultaneas)
        return self._semaforo

    def _identidade(self) -> Optional[Identidade]:
        if not self.cfg.auth_ligada or self.cfg.transporte == "stdio":
            return Identidade(self.cfg.email_dev, "Desenvolvimento local")
        token = get_access_token()
        if token is None:
            return None
        email = (token.claims or {}).get("email")
        if not email:
            return None
        # Papel e LEIS_ADMINS são decididos pelo e-mail: sem verificação, uma
        # conta Google criada com o endereço de outra pessoa herdaria o acesso.
        # O tokeninfo devolve "true" (texto); o userinfo, true (booleano).
        if str((token.claims or {}).get("email_verified")).lower() != "true":
            return None
        return Identidade(str(email).lower(), (token.claims or {}).get("name"))

    async def _usuario(self) -> Usuario:
        identidade = self._identidade()
        if identidade is None:
            raise ToolError(
                "Não foi possível identificar o usuário: o login não trouxe e-mail verificado. "
                "Reconecte o servidor autorizando o acesso ao e-mail."
            )
        papel_inicial = self.cfg.papel_padrao
        if identidade.email in self.cfg.admins or (
            not self.cfg.auth_ligada and identidade.email == self.cfg.email_dev
        ):
            papel_inicial = "admin"
        usuario = await anyio.to_thread.run_sync(
            self.repo.garantir, identidade.email, identidade.nome, papel_inicial
        )
        if (
            papel_inicial == "admin"
            and usuario.papel != "admin"
            and identidade.email in self.cfg.admins
        ):
            usuario = await anyio.to_thread.run_sync(
                lambda: self.repo.criar_ou_atualizar(identidade.email, papel="admin")
            )
        return usuario

    async def _registrar(
        self,
        usuario: Usuario,
        ferramenta: str,
        argumentos: Any,
        status: str,
        **extra: Any,
    ) -> None:
        try:
            await anyio.to_thread.run_sync(
                lambda: self.repo.registrar_chamada(
                    usuario.id, ferramenta, argumentos, status, **extra
                )
            )
        except Exception:  # noqa: BLE001 — registro não pode derrubar a resposta
            logger.exception("Falha ao registrar chamada de %s", ferramenta)

    @staticmethod
    def _tamanho(resultado: Any) -> Optional[int]:
        try:
            if getattr(resultado, "structured_content", None) is not None:
                return len(
                    json.dumps(
                        resultado.structured_content, ensure_ascii=False, default=str
                    )
                )
            return sum(
                len(getattr(bloco, "text", "") or "") for bloco in resultado.content
            )
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------

    async def on_list_tools(
        self, context: MiddlewareContext, call_next
    ) -> Sequence[Any]:
        ferramentas = await call_next(context)
        try:
            usuario = await self._usuario()
            e_admin = usuario.papel == "admin"
        except ToolError:
            e_admin = False
        if e_admin:
            return ferramentas
        return [
            f
            for f in ferramentas
            if TAG_ADMIN not in self.tags_por_ferramenta.get(f.name, set())
        ]

    async def on_call_tool(self, context: MiddlewareContext, call_next) -> Any:
        nome = context.message.name
        argumentos = context.message.arguments or {}
        usuario = await self._usuario()

        if usuario.papel == "bloqueado":
            await self._registrar(usuario, nome, argumentos, "negado_papel")
            raise ToolError(
                "Acesso bloqueado para esta conta. Contate o administrador do servidor."
            )
        if usuario.papel == "pendente":
            await self._registrar(usuario, nome, argumentos, "negado_papel")
            raise ToolError(
                f"A conta {usuario.email} foi registrada e aguarda aprovação do administrador. "
                "Tente novamente depois de liberada."
            )

        tags = self.tags_por_ferramenta.get(nome, set())

        if TAG_ADMIN in tags and usuario.papel != "admin":
            await self._registrar(usuario, nome, argumentos, "negado_permissao")
            raise ToolError(f"A ferramenta '{nome}' é restrita a administradores.")

        if usuario.papel != "admin" and TAG_LIVRE not in tags:
            limite = (
                self.cfg.cota_padrao
                if usuario.cota_diaria is None
                else usuario.cota_diaria
            )
            if limite != COTA_ILIMITADA:
                livres = tuple(
                    n for n, t in self.tags_por_ferramenta.items() if TAG_LIVRE in t
                )
                usadas = await anyio.to_thread.run_sync(
                    self.repo.chamadas_hoje, usuario.id, livres
                )
                if usadas >= limite:
                    await self._registrar(usuario, nome, argumentos, "negado_cota")
                    raise ToolError(
                        f"Cota diária atingida ({usadas} de {limite} chamadas). Ela renova à "
                        f"meia-noite no fuso {self.cfg.fuso_cota}. Diga isso ao usuário; não "
                        "responda com conhecimento próprio no lugar dos dados."
                    )

        inicio = time.monotonic()
        try:
            if TAG_PESADA in tags:
                async with self._vagas():
                    resultado = await call_next(context)
            else:
                resultado = await call_next(context)
        except Exception as e:
            await self._registrar(
                usuario,
                nome,
                argumentos,
                "erro",
                duracao_ms=int((time.monotonic() - inicio) * 1000),
                erro=f"{type(e).__name__}: {e}",
            )
            raise

        await self._registrar(
            usuario,
            nome,
            argumentos,
            "erro" if getattr(resultado, "is_error", False) else "ok",
            duracao_ms=int((time.monotonic() - inicio) * 1000),
            tamanho_resposta=self._tamanho(resultado),
        )
        return resultado

    async def on_read_resource(self, context: MiddlewareContext, call_next) -> Any:
        usuario = await self._usuario()
        if usuario.papel in ("bloqueado", "pendente"):
            raise ToolError("Conta sem acesso liberado.")
        return await call_next(context)

    async def on_get_prompt(self, context: MiddlewareContext, call_next) -> Any:
        usuario = await self._usuario()
        if usuario.papel in ("bloqueado", "pendente"):
            raise ToolError("Conta sem acesso liberado.")
        return await call_next(context)
