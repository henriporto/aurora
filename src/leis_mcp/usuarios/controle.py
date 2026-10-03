"""
Controle de acesso aplicado a TODA chamada de ferramenta.

Ordem, em cada chamada:
1. identidade — e-mail do token do Google (ou o usuário de desenvolvimento);
2. usuário — criado no primeiro acesso com o papel padrão configurado;
3. papel — `bloqueado` e `pendente` são recusados; ferramentas marcadas com a
   tag `admin` exigem papel `admin`;
4. cota — chamadas de hoje (fuso configurado) contra a cota do usuário;
   a chamada é gravada ANTES de executar, na mesma transação da contagem, para
   que chamadas em paralelo não furem a cota;
5. semáforo — ferramentas marcadas `pesada` disputam um número limitado de vagas
   (uma por usuário de cada vez), com espera máxima, para que buscas simultâneas
   não esgotem a CPU e a RAM da VM;
6. execução e registro — tudo vai para `usuarios.db`, inclusive recusas, e uma
   linha de log por chamada (sem e-mail) alimenta as métricas do GCP.

Recursos por ID (`leis://proposicao/…`, `leis://parlamentar/…`) passam pelo
mesmo caminho e contam na cota.

Uma camada só, antes de todas as ferramentas: quem escreve uma ferramenta nova
não tem como esquecer de aplicar cota ou registro.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Sequence

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

#: Recursos que rodam consultas por ID: contam na cota como uma chamada.
RECURSOS_COM_COTA = ("leis://proposicao/", "leis://parlamentar/")


class ServidorOcupado(Exception):
    """A chamada pesada não conseguiu vaga dentro de LEIS_ESPERA_MAXIMA_SEG."""


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
        self._vagas_por_usuario: dict[int, asyncio.Semaphore] = {}

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
        logger.info(
            "chamada ferramenta=%s status=%s usuario_id=%s", ferramenta, status, usuario.id
        )
        try:
            await anyio.to_thread.run_sync(
                lambda: self.repo.registrar_chamada(
                    usuario.id, ferramenta, argumentos, status, **extra
                )
            )
        except Exception:  # noqa: BLE001 — registro não pode derrubar a resposta
            logger.exception("Falha ao registrar chamada de %s", ferramenta)

    async def _finalizar(
        self,
        chamada_id: int,
        usuario: Usuario,
        ferramenta: str,
        status: str,
        espera_ms: int = 0,
        **extra: Any,
    ) -> None:
        # Uma linha por chamada, sem e-mail: é o que o Cloud Logging transforma
        # nas métricas do painel (docs/deploy_gcp.md, "Monitoramento").
        logger.info(
            "chamada ferramenta=%s status=%s duracao_ms=%s espera_ms=%d usuario_id=%s",
            ferramenta,
            status,
            extra.get("duracao_ms"),
            espera_ms,
            usuario.id,
        )
        try:
            await anyio.to_thread.run_sync(
                lambda: self.repo.finalizar_chamada(chamada_id, status, **extra)
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
        return await self._executar(
            nome,
            argumentos,
            self.tags_por_ferramenta.get(nome, set()),
            lambda: call_next(context),
        )

    async def on_read_resource(self, context: MiddlewareContext, call_next) -> Any:
        uri = str(context.message.uri)
        if not uri.startswith(RECURSOS_COM_COTA):
            usuario = await self._usuario()
            if usuario.papel in ("bloqueado", "pendente"):
                raise ToolError("Conta sem acesso liberado.")
            return await call_next(context)
        # Os recursos por ID rodam as mesmas consultas das ferramentas: sem
        # isto, seriam um caminho sem cota e sem registro.
        return await self._executar(
            "recurso", {"uri": uri}, set(), lambda: call_next(context)
        )

    async def on_get_prompt(self, context: MiddlewareContext, call_next) -> Any:
        usuario = await self._usuario()
        if usuario.papel in ("bloqueado", "pendente"):
            raise ToolError("Conta sem acesso liberado.")
        return await call_next(context)

    # ------------------------------------------------------------------

    async def _executar(
        self,
        nome: str,
        argumentos: Any,
        tags: set[str],
        executar: Callable[[], Awaitable[Any]],
    ) -> Any:
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

        if TAG_ADMIN in tags and usuario.papel != "admin":
            await self._registrar(usuario, nome, argumentos, "negado_permissao")
            raise ToolError(f"A ferramenta '{nome}' é restrita a administradores.")

        limite: Optional[int] = None
        if usuario.papel != "admin" and TAG_LIVRE not in tags:
            limite = (
                self.cfg.cota_padrao
                if usuario.cota_diaria is None
                else usuario.cota_diaria
            )
            if limite == COTA_ILIMITADA:
                limite = None
        livres = tuple(
            n for n, t in self.tags_por_ferramenta.items() if TAG_LIVRE in t
        )
        chamada_id, usadas = await anyio.to_thread.run_sync(
            self.repo.reservar_chamada, usuario.id, nome, argumentos, limite, livres
        )
        if chamada_id is None:
            await self._registrar(usuario, nome, argumentos, "negado_cota")
            raise ToolError(
                f"Cota diária atingida ({usadas} de {limite} chamadas). Ela renova à "
                f"meia-noite no fuso {self.cfg.fuso_cota}. Diga isso ao usuário; não "
                "responda com conhecimento próprio no lugar dos dados."
            )

        inicio = time.monotonic()
        espera_ms = 0
        try:
            if TAG_PESADA in tags:
                async with self._vaga_pesada(usuario, nome, chamada_id):
                    espera_ms = int((time.monotonic() - inicio) * 1000)
                    resultado = await executar()
            else:
                resultado = await executar()
        except ServidorOcupado:
            raise ToolError(
                "Servidor ocupado: muitas buscas em andamento agora. Esta chamada NÃO foi "
                "executada e não contou na cota. Aguarde cerca de um minuto e tente de novo; "
                "não responda com conhecimento próprio no lugar dos dados."
            ) from None
        except Exception as e:
            await self._finalizar(
                chamada_id,
                usuario,
                nome,
                "erro",
                duracao_ms=int((time.monotonic() - inicio) * 1000),
                espera_ms=espera_ms,
                erro=f"{type(e).__name__}: {e}",
            )
            raise

        await self._finalizar(
            chamada_id,
            usuario,
            nome,
            "erro" if getattr(resultado, "is_error", False) else "ok",
            duracao_ms=int((time.monotonic() - inicio) * 1000),
            espera_ms=espera_ms,
            tamanho_resposta=self._tamanho(resultado),
        )
        return resultado

    @asynccontextmanager
    async def _vaga_pesada(
        self, usuario: Usuario, nome: str, chamada_id: int
    ) -> AsyncIterator[None]:
        """
        Duas filas, com espera máxima somada de LEIS_ESPERA_MAXIMA_SEG:
        primeiro a do próprio usuário (uma busca pesada por vez por conta, para
        que chamadas em paralelo de uma pessoa não ocupem todas as vagas), depois
        a global (LEIS_BUSCAS_SIMULTANEAS). Passou do prazo, a chamada é
        recusada e a reserva da cota é desfeita: esperar indefinidamente só
        empurraria a resposta para depois dos 240 s em que o cliente desiste.
        """
        por_usuario = self._vagas_por_usuario.setdefault(
            usuario.id, asyncio.Semaphore(1)
        )
        pilha = AsyncExitStack()
        try:
            async with asyncio.timeout(self.cfg.espera_maxima_seg):
                await pilha.enter_async_context(por_usuario)
                await pilha.enter_async_context(self._vagas())
        except TimeoutError:
            await pilha.aclose()
            try:
                await anyio.to_thread.run_sync(self.repo.cancelar_chamada, chamada_id)
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao cancelar a reserva %s", chamada_id)
            logger.warning(
                "chamada ferramenta=%s status=ocupado usuario_id=%s espera_ms=%d",
                nome,
                usuario.id,
                int(self.cfg.espera_maxima_seg * 1000),
            )
            raise ServidorOcupado() from None
        async with pilha:
            yield
