"""
Inteiro teor sob demanda: baixa, segmenta e busca o texto de uma proposição
que o ETL ainda não indexou.

O banco é somente leitura: trechos e vetores ficam só em memória (LRU), e a
busca neles repete a do índice (lexical por radical + cosseno, fundidos por RRF).

- Senado: a URL gravada é um XML de metadados; a do documento é resolvida na
  API de dados abertos.
- Câmara: `prop_mostrarintegra` pode devolver PDF ou DOCX; os dois são lidos.
- Só baixa de hosts da Câmara e do Senado, com teto de tamanho e de tempo.
- A falha é devolvida com o motivo, nunca como "o texto não trata do tema".
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse
from xml.etree import ElementTree

import numpy as np

from leis_mcp.config import obter_config
from leis_mcp.dados.banco import conexao
from leis_mcp.dados.buscador import K_RRF, STOPWORDS_PT, obter_buscador
from leis_mcp.dados.chunker_hierarquico import HierarchicalLegislativeChunker

logger = logging.getLogger(__name__)

HOSTS_PERMITIDOS = ("camara.leg.br", "senado.leg.br")
USER_AGENT = "Mozilla/5.0 (compatible; leis-mcp)"

#: Acima disto o documento é cortado (com aviso): vetorizar tudo em CPU estouraria o tempo da chamada.
MAX_TRECHOS_POR_DOCUMENTO = 3000

#: Abaixo disto o texto é provavelmente capa ou PDF digitalizado como imagem.
TEXTO_CURTO = 1500

#: Documentos lidos guardados em memória.
MAX_DOCUMENTOS_EM_MEMORIA = 64


class FalhaSobDemanda(RuntimeError):
    """O texto não pôde ser obtido; a mensagem vai para o modelo."""


@dataclass
class Documento:
    id_proposicao: int
    url: str
    trechos: list[dict[str, Any]]
    vetores: np.ndarray
    tokens: list[list[str]]
    caracteres: int
    cortado: bool = False


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _host_permitido(url: str) -> bool:
    partes = urlparse(url)
    host = (partes.hostname or "").lower()
    return partes.scheme in ("http", "https") and any(
        host == h or host.endswith("." + h) for h in HOSTS_PERMITIDOS
    )


class _RedirecionamentoRestrito(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _host_permitido(newurl):
            raise FalhaSobDemanda(f"redirecionamento para host não permitido: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_abridor = urllib.request.build_opener(_RedirecionamentoRestrito)


def _baixar(url: str, aceitar: str = "*/*") -> tuple[bytes, str]:
    if not _host_permitido(url):
        raise FalhaSobDemanda(f"host não permitido: {url}")
    cfg = obter_config()
    limite = cfg.sob_demanda_max_mb * 1024 * 1024
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": aceitar}
    )
    try:
        with _abridor.open(req, timeout=cfg.sob_demanda_timeout_seg) as resp:
            conteudo = resp.read(limite + 1)
            tipo = (resp.headers.get("Content-Type") or "").lower()
    except FalhaSobDemanda:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FalhaSobDemanda(f"download falhou ({url}): {e}") from e
    if len(conteudo) > limite:
        raise FalhaSobDemanda(
            f"documento maior que {cfg.sob_demanda_max_mb} MB ({url})"
        )
    return conteudo, tipo


def _urls_senado(id_materia: int) -> list[str]:
    """URLs dos documentos de uma matéria, do texto principal para o acessório."""

    def prioridade(descricao: str) -> int:
        d = descricao.lower()
        if any(p in d for p in ("projeto", "proposta", "pec", "medida provisória")):
            return 1
        if "avulso" in d:
            return 2
        if "veto" in d or "razões" in d:
            return 3
        if "requerimento" in d:
            return 20
        return 100

    candidatos: list[tuple[int, str]] = []
    # API atual primeiro; a antiga (materia/textos) fica como segunda tentativa.
    try:
        bruto, _ = _baixar(
            "https://legis.senado.leg.br/dadosabertos/processo/documento"
            f"?codigoMateria={id_materia}",
            "application/json",
        )
        for doc in json.loads(bruto):
            url = doc.get("urlDocumento")
            if url:
                candidatos.append((prioridade(doc.get("descricaoTipo") or ""), url))
    except (FalhaSobDemanda, ValueError, AttributeError) as e:
        logger.info("Senado, API de documentos (%s): %s", id_materia, e)
    if not candidatos:
        try:
            bruto, _ = _baixar(
                f"https://legis.senado.leg.br/dadosabertos/materia/textos/{id_materia}",
                "application/xml",
            )
            for texto in ElementTree.fromstring(bruto).iter("Texto"):
                url = texto.findtext("UrlTexto")
                formato = (texto.findtext("FormatoTexto") or "").lower()
                if url and (
                    "pdf" in formato or texto.findtext("TipoDocumento") == "PDF"
                ):
                    candidatos.append(
                        (prioridade(texto.findtext("DescricaoTipoTexto") or ""), url)
                    )
        except (FalhaSobDemanda, ElementTree.ParseError) as e:
            logger.info("Senado, API de textos (%s): %s", id_materia, e)
    candidatos.sort(key=lambda c: c[0])
    return list(dict.fromkeys(url for _, url in candidatos))


# ---------------------------------------------------------------------------
# Extração de texto
# ---------------------------------------------------------------------------


def _texto_pdf(conteudo: bytes) -> str:
    from pypdf import PdfReader

    leitor = PdfReader(io.BytesIO(conteudo))
    return "\n".join(t for p in leitor.pages if (t := p.extract_text()))


def _texto_docx(conteudo: bytes) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        raiz = ElementTree.fromstring(z.read("word/document.xml"))
    paragrafos = [
        "".join(t.text or "" for t in p.iter(f"{ns}t")) for p in raiz.iter(f"{ns}p")
    ]
    return "\n".join(p for p in paragrafos if p.strip())


def _extrair_texto(conteudo: bytes) -> str:
    if conteudo.startswith(b"%PDF"):
        return _texto_pdf(conteudo)
    if conteudo.startswith(b"PK"):
        return _texto_docx(conteudo)
    raise FalhaSobDemanda(
        "a URL não devolveu PDF nem DOCX (provavelmente uma página HTML)"
    )


def _obter_texto(id_proposicao: int, casa: str, url: str) -> tuple[str, str]:
    """(texto, URL efetivamente lida)."""
    if casa == "Senado":
        urls = _urls_senado(id_proposicao)
        if not urls:
            raise FalhaSobDemanda("o Senado não informou documento para a matéria")
    else:
        if not url:
            raise FalhaSobDemanda("a proposição não tem URL de inteiro teor na base")
        urls = [url]
    erros = []
    melhor: tuple[str, str] = ("", "")
    # Até 3 candidatos: os seguintes cobrem link quebrado e PDF sem camada de texto.
    for candidato in urls[:3]:
        try:
            conteudo, _ = _baixar(candidato)
            texto = _extrair_texto(conteudo)
        except FalhaSobDemanda as e:
            erros.append(str(e))
            continue
        except Exception as e:  # noqa: BLE001 - PDF/DOCX corrompido
            erros.append(f"{candidato}: leitura do documento falhou: {e}")
            continue
        if len(texto.strip()) > len(melhor[0].strip()):
            melhor = (texto, candidato)
        if len(texto.strip()) >= TEXTO_CURTO:
            break
        erros.append(
            f"{candidato}: só {len(texto.strip())} caracteres de texto extraível "
            "(PDF digitalizado como imagem?)"
        )
    if melhor[0].strip():
        return melhor
    raise FalhaSobDemanda("; ".join(erros))


# ---------------------------------------------------------------------------
# Leitura e cache
# ---------------------------------------------------------------------------


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def _tokens(texto: str) -> list[str]:
    return re.findall(r"\w+", _normalizar(texto))


class _Cache:
    def __init__(self) -> None:
        self._docs: OrderedDict[int, Documento] = OrderedDict()
        self._falhas: dict[int, tuple[float, str]] = {}
        self._trava = threading.Lock()
        self._travas_por_id: dict[int, threading.Lock] = {}

    def _trava_de(self, id_proposicao: int) -> threading.Lock:
        with self._trava:
            return self._travas_por_id.setdefault(id_proposicao, threading.Lock())

    def obter(self, id_proposicao: int) -> Documento:
        # Uma trava por proposição: chamadas simultâneas pelo mesmo ID baixam uma vez só.
        with self._trava_de(id_proposicao):
            ttl = obter_config().cache_ttl_seg
            with self._trava:
                if id_proposicao in self._docs:
                    self._docs.move_to_end(id_proposicao)
                    return self._docs[id_proposicao]
                falha = self._falhas.get(id_proposicao)
                if falha and time.monotonic() - falha[0] < ttl:
                    raise FalhaSobDemanda(falha[1])
            try:
                doc = _ler(id_proposicao)
            except FalhaSobDemanda as e:
                with self._trava:
                    self._falhas[id_proposicao] = (time.monotonic(), str(e))
                raise
            with self._trava:
                self._docs[id_proposicao] = doc
                while len(self._docs) > MAX_DOCUMENTOS_EM_MEMORIA:
                    self._docs.popitem(last=False)
            return doc


_cache = _Cache()


def _ler(id_proposicao: int) -> Documento:
    with conexao() as conn:
        linha = conn.execute(
            """
            SELECT p.sigla_tipo, p.numero, p.ano, p.ementa, p.url_inteiro_teor, p.casa,
                   p.data_apresentacao,
                   (SELECT pa.nome FROM autoria a
                    JOIN parlamentares pa ON pa.id_parlamentar = a.id_parlamentar
                    WHERE a.id_proposicao = p.id_proposicao LIMIT 1)
            FROM proposicoes p WHERE p.id_proposicao = ?
            """,
            (id_proposicao,),
        ).fetchone()
    if not linha:
        raise FalhaSobDemanda("proposição não encontrada na base")
    sigla, numero, ano, ementa, url, casa, data, autor = linha

    t0 = time.monotonic()
    texto, url_lida = _obter_texto(id_proposicao, casa, url)
    # Mesmos metadados do ETL, para o vetor ter a mesma forma dos trechos indexados.
    chunks = HierarchicalLegislativeChunker().chunk_document(
        texto,
        {
            "id_proposicao": id_proposicao,
            "tipo": sigla or "PL",
            "numero": str(numero or "S/N"),
            "ano": ano or "",
            "ementa": ementa or "",
            "autor": autor or "Desconhecido",
        },
    )
    if not chunks:
        raise FalhaSobDemanda("o documento não tem estrutura de texto segmentável")
    cortado = len(chunks) > MAX_TRECHOS_POR_DOCUMENTO
    chunks = chunks[:MAX_TRECHOS_POR_DOCUMENTO]
    vetores = obter_buscador().gerar_embeddings_normalizados(
        [c["texto_enriquecido"] for c in chunks]
    )
    trechos = [
        {
            "id_chunk": c["id_chunk"],
            "id_proposicao": id_proposicao,
            "tipo_dispositivo": c["tipo_dispositivo"],
            "identificador_normativo": c["identificador_normativo"],
            "texto_original": c["texto_original"],
            "sigla_tipo": sigla,
            "numero": numero,
            "ano": ano,
            "ementa": ementa,
            "casa": casa,
            "data_apresentacao": data,
        }
        for c in chunks
    ]
    logger.info(
        "Inteiro teor sob demanda: proposição %s, %d trechos em %.1fs",
        id_proposicao,
        len(trechos),
        time.monotonic() - t0,
    )
    return Documento(
        id_proposicao=id_proposicao,
        url=url_lida,
        trechos=trechos,
        vetores=vetores,
        tokens=[_tokens(c["texto_enriquecido"]) for c in chunks],
        caracteres=len(texto.strip()),
        cortado=cortado,
    )


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------


def _radicais(termo: str) -> list[tuple[str, bool]]:
    """Mesma regra de `Buscador.sanitizar_fts`: (radical, casa por prefixo)."""
    palavras = _tokens(termo)
    significativas = [p for p in palavras if p not in STOPWORDS_PT]
    radicais = []
    for p in significativas or palavras:
        if len(p) > 6:
            p = p[:6]
        elif len(p) > 3 and p.endswith("s"):
            p = p[:-1]
        radicais.append((p, len(p) > 3))
    return radicais


def _ocorrencias(tokens: list[str], radicais: list[tuple[str, bool]]) -> int:
    """Total de ocorrências se TODOS os radicais aparecem (AND); senão 0."""
    total = 0
    for radical, prefixo in radicais:
        n = sum(
            1 for t in tokens if (t.startswith(radical) if prefixo else t == radical)
        )
        if not n:
            return 0
        total += n
    return total


def buscar(
    ids: list[int], termo: str, threshold: float, top_k: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Busca `termo` no inteiro teor baixado das proposições `ids`.

    Devolve (trechos, lidas, falhas). `trechos` usa o mesmo formato e a mesma
    escala de score (RRF) de `Buscador.buscar_trechos`.
    """
    lidas: list[dict[str, Any]] = []
    falhas: list[dict[str, Any]] = []
    docs: list[Documento] = []

    def obter(id_proposicao: int) -> Documento | str:
        try:
            return _cache.obter(id_proposicao)
        except FalhaSobDemanda as e:
            return str(e)

    # O tempo é quase todo de rede; a vetorização já é serializada pela trava do modelo.
    with ThreadPoolExecutor(max_workers=min(len(ids), 5) or 1) as pool:
        resultados = list(pool.map(obter, ids))
    for id_proposicao, doc in zip(ids, resultados):
        if isinstance(doc, str):
            falhas.append({"id_proposicao": id_proposicao, "motivo": doc})
            continue
        docs.append(doc)
        lidas.append(
            {
                "id_proposicao": id_proposicao,
                "url_documento": doc.url,
                "trechos_no_documento": len(doc.trechos),
                "caracteres_extraidos": doc.caracteres,
                "texto_incompleto": doc.cortado or doc.caracteres < TEXTO_CURTO,
            }
        )
    if not docs:
        return [], lidas, falhas

    trechos = [t for d in docs for t in d.trechos]
    vetores = np.vstack([d.vetores for d in docs])
    tokens = [tk for d in docs for tk in d.tokens]

    vetor = obter_buscador().gerar_embedding(termo)
    norma = np.linalg.norm(vetor)
    if norma > 0:
        vetor = vetor / norma
    cossenos = vetores @ vetor

    radicais = _radicais(termo)
    ocorrencias = [_ocorrencias(t, radicais) if radicais else 0 for t in tokens]

    ordem_sem = [i for i in np.argsort(-cossenos) if cossenos[i] >= threshold]
    ordem_lex = sorted(
        (i for i, n in enumerate(ocorrencias) if n), key=lambda i: -ocorrencias[i]
    )
    rank_sem = {int(i): r for r, i in enumerate(ordem_sem, 1)}
    rank_lex = {i: r for r, i in enumerate(ordem_lex, 1)}

    fundidos = []
    for i in set(rank_sem) | set(rank_lex):
        score = sum(1.0 / (K_RRF + r[i]) for r in (rank_sem, rank_lex) if i in r)
        fundidos.append(
            {
                **trechos[i],
                "score": score,
                "score_semantico": float(cossenos[i]) if i in rank_sem else None,
                "score_lexical": ocorrencias[i] if i in rank_lex else None,
            }
        )
    fundidos.sort(key=lambda x: x["score"], reverse=True)
    return fundidos[:top_k], lidas, falhas
