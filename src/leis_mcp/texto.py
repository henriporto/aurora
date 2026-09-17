"""Normalizações de texto compartilhadas pelas ferramentas."""

from __future__ import annotations

import unicodedata
from typing import Any, Optional


def sem_acento(texto: Any) -> str:
    """Minúsculas e sem diacríticos ('Tábata' e 'Tabata' comparam iguais)."""
    if not texto:
        return ""
    decomposto = unicodedata.normalize("NFD", str(texto))
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn").lower()


#: Sufixos que o projeto original acrescentava a algumas ementas.
_MARCAS_PROFISSOES = ("[Profissões impactadas:", "[Profissões analisadas:")


def ementa_sem_profissoes(ementa: Any) -> str:
    """Ementa sem o sufixo "[Profissões impactadas/analisadas: …]", quando houver."""
    texto = str(ementa or "")
    posicoes = [p for p in (texto.find(m) for m in _MARCAS_PROFISSOES) if p >= 0]
    return (texto[: min(posicoes)] if posicoes else texto).strip()


def palavras(texto: Any) -> list[str]:
    """Palavras em minúsculas e sem acento: letras e dígitos, o resto separa."""
    return "".join(c if c.isalnum() else " " for c in sem_acento(texto)).split()


def normalizar_casa(casa: Optional[str]) -> Optional[str]:
    """
    Converte qualquer grafia de casa legislativa para o valor gravado no banco.

    O banco usa 'Câmara' e 'Senado' por extenso. As siglas 'CD'/'SF' das APIs
    oficiais produziam filtro sem correspondência e ZERO resultados sem erro —
    o pior tipo de falha, porque parece uma resposta legítima.
    """
    if not casa:
        return None
    c = casa.strip().lower()
    if c in {"cd", "camara", "câmara", "camara dos deputados", "câmara dos deputados"}:
        return "Câmara"
    if c in {"sf", "senado", "senado federal"}:
        return "Senado"
    if "câmara" in c or "camara" in c or "deputad" in c:
        return "Câmara"
    if "senado" in c or "senador" in c:
        return "Senado"
    return casa


def br(n: int) -> str:
    """Separador de milhar no padrão brasileiro, número a número."""
    return f"{n:,}".replace(",", ".")


def rotulo_proposicao(sigla: Any, numero: Any, ano: Any) -> str:
    return f"{sigla} {numero}/{ano}"


def ids_inteiros(valores: Optional[list]) -> Optional[list[int]]:
    """Converte uma lista vinda do modelo em inteiros, descartando o inválido."""
    if valores is None:
        return None
    saida: list[int] = []
    for v in valores:
        try:
            saida.append(int(v))
        except (TypeError, ValueError):
            continue
    return saida


def ano_da_data(ano: Any, data: Any) -> Any:
    """Recupera o ano pela data de apresentação quando a coluna vem zerada."""
    if ano:
        return ano
    if data and len(str(data)) >= 4:
        try:
            return int(str(data)[:4])
        except ValueError:
            return ano
    return ano
