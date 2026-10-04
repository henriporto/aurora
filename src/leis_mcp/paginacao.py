"""
Paginação sem perda e cache de resultados.

Os clientes MCP limitam o tamanho de cada resultado (~150 mil caracteres no
claude.ai). Nada é podado: a lista é dividida em páginas por tamanho, e cada
item vai inteiro para alguma página.

As fronteiras dependem só da lista e do tamanho máximo, então a mesma chamada
com `pagina=2` devolve sempre os mesmos itens. O cache evita refazer a busca.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Hashable, Optional

from leis_mcp.config import obter_config


def _tamanho(item: Any) -> int:
    return len(json.dumps(item, ensure_ascii=False, default=str)) + 2


def paginar(
    itens: list[Any], pagina: Optional[int], reserva: int = 0, resumo: Optional[str] = None
) -> tuple[list[Any], dict[str, Any]]:
    """
    Devolve os itens da página pedida e os metadados de paginação.

    `reserva` desconta do tamanho da página o que a resposta leva além da lista
    (cabeçalho, avisos, resumo). `resumo` nomeia o campo da resposta que traz
    uma visão compacta de todos os itens, citado no aviso de paginação.
    """
    limite = max(5_000, obter_config().max_chars_pagina - reserva)
    fronteiras: list[tuple[int, int]] = []
    inicio, acumulado = 0, 0
    for i, item in enumerate(itens):
        tamanho = _tamanho(item)
        if i > inicio and acumulado + tamanho > limite:
            fronteiras.append((inicio, i))
            inicio, acumulado = i, 0
        acumulado += tamanho
    fronteiras.append((inicio, len(itens)))

    total = len(fronteiras)
    atual = max(1, int(pagina or 1))
    if atual > total:
        return [], {
            "completo": False,
            "pagina": atual,
            "total_paginas": total,
            "total_itens": len(itens),
            "itens_nesta_pagina": 0,
            "proxima_pagina": None,
            "aviso": f"Página {atual} não existe: há {total} página(s).",
        }

    a, b = fronteiras[atual - 1]
    info: dict[str, Any] = {
        # Primeiro campo: o modelo precisa vê-lo antes de tratar a lista como completa.
        "completo": total == 1,
        "pagina": atual,
        "total_paginas": total,
        "total_itens": len(itens),
        "itens_nesta_pagina": b - a,
        "itens_de": a + 1 if b > a else 0,
        "itens_ate": b,
        "proxima_pagina": atual + 1 if atual < total else None,
    }
    if total > 1:
        info["aviso"] = (
            f"RESULTADO PAGINADO: página {atual} de {total} (itens {a + 1} a {b} de {len(itens)}). "
            + (
                (
                    f"{resumo} cobre TODOS os {len(itens)} itens — use-o para totais, contagens e "
                    "conclusões sobre o conjunto. "
                    if resumo
                    else ""
                )
                + (
                    f"O DETALHE dos itens {b + 1} em diante NÃO está nesta resposta: chame a mesma "
                    f"ferramenta com os mesmos argumentos e pagina={atual + 1} para vê-lo antes de "
                    "citar evidência deles ou de apresentar a lista detalhada como completa."
                    if atual < total
                    else "Esta é a última página."
                )
            )
        )
    return itens[a:b], info


class CacheResultados:
    """LRU com expiração, seguro entre threads. O banco é imutável em execução."""

    def __init__(self, capacidade: int = 64) -> None:
        self._dados: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._trava = threading.Lock()
        self._capacidade = capacidade

    def obter_ou_calcular(self, chave: Hashable, calcular: Callable[[], Any]) -> Any:
        ttl = obter_config().cache_ttl_seg
        agora = time.monotonic()
        with self._trava:
            if chave in self._dados:
                instante, valor = self._dados[chave]
                if agora - instante <= ttl:
                    self._dados.move_to_end(chave)
                    return valor
                del self._dados[chave]
        valor = calcular()
        with self._trava:
            self._dados[chave] = (agora, valor)
            self._dados.move_to_end(chave)
            while len(self._dados) > self._capacidade:
                self._dados.popitem(last=False)
        return valor


def chave_de(*partes: Any) -> str:
    return json.dumps(partes, ensure_ascii=False, sort_keys=True, default=str)


cache = CacheResultados()
