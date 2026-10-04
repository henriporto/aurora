"""
Busca híbrida: densa (embeddings + cosseno) e esparsa (FTS5/BM25), fundidas por
Reciprocal Rank Fusion com k = 60.

Duas camadas, cada uma com seus índices:

- **ementas** (`proposicoes_embeddings` + `proposicoes_fts`): híbrida simétrica,
  as duas buscas rodam de forma independente sobre o acervo inteiro;
- **inteiro teor** (`proposicoes_chunks_embeddings` + `proposicoes_chunks_fts`):
  lexical primeiro, e a densa reordena dentro das proposições que o lexical
  encontrou (ou dentro de `lista_ids`).

Nunca escreve no banco, falha se o modelo de embeddings não carregar e é
seguro para várias threads.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

import numpy as np

from leis_mcp.config import obter_config
from leis_mcp.dados.banco import conexao
from leis_mcp.texto import ementa_sem_profissoes, normalizar_casa

logger = logging.getLogger(__name__)

K_RRF = 60.0

#: Abaixo disto a busca AND é restritiva demais e é complementada por OR.
MIN_RESULTADOS_LEXICAIS = 10

#: Teto de linhas do FTS5: além das primeiras centenas, nada pesa no RRF.
MAX_RESULTADOS_LEXICAIS = 2000

#: Mesmo teto para a lista densa das ementas; é o que limita o custo da fusão.
MAX_RESULTADOS_DENSOS = 2000

#: Proposições cujos vetores de trechos são lidos do SQLite por busca de
#: inteiro teor. Sem teto, um termo comum lê centenas de MB.
MAX_PROPOSICOES_DENSA_TRECHOS = 400

#: Tokens lidos por texto vetorizado (consulta ou trecho sob demanda).
MAX_TOKENS = 512

#: Ementas usadas para estimar a distribuição de cossenos de cada termo.
TAMANHO_AMOSTRA_DESTAQUE = 20_000

#: Vetores de consulta em cache: uma busca vetoriza o mesmo termo várias vezes.
MAX_VETORES_EM_CACHE = 2048

STOPWORDS_PT = {
    "a", "ao", "aos", "as", "à", "às", "da", "das", "de", "do", "dos", "e", "em",
    "na", "nas", "no", "nos", "num", "numa", "o", "os", "ou", "para", "pela",
    "pelas", "pelo", "pelos", "por", "que", "se", "sobre", "um", "uma", "uns",
    "umas", "com", "como",
}  # fmt: skip

_ACENTOS = str.maketrans("çáâãàéêíóôõú", "caaaaeeiooou")


class ModeloIndisponivel(RuntimeError):
    """O modelo de embeddings não pôde ser carregado."""


class IndiceDeOutroModelo(RuntimeError):
    """Os vetores do banco foram gerados por um modelo diferente do configurado."""


def destaque(cosseno: Optional[float], estatisticas: tuple[float, float]) -> Optional[float]:
    """
    Quanto um cosseno se destaca da distribuição do termo no acervo:
    (cosseno − mediana) / (p99 − mediana).

    O cosseno cru não é comparável entre termos nem entre modelos.
    """
    if cosseno is None:
        return None
    mediana, p99 = estatisticas
    return round((cosseno - mediana) / max(p99 - mediana, 1e-6), 2)


class Buscador:
    def __init__(self) -> None:
        self._cfg = obter_config()
        self._modelo = None
        self._trava_modelo = threading.Lock()
        self._trava_acervo = threading.Lock()
        self._trava_encode = threading.Lock()

        # Acervo das ementas em memória (~2 GiB): metadados, matriz de vetores e normas.
        self._acervo: Optional[list[dict[str, Any]]] = None
        self._matriz: Optional[np.ndarray] = None
        self._normas: Optional[np.ndarray] = None
        self._indice_por_id: Optional[dict[int, int]] = None
        self._amostra: Optional[np.ndarray] = None
        self._chunks_disponiveis: Optional[bool] = None
        self._vetores_consulta: OrderedDict[str, np.ndarray] = OrderedDict()
        self._trava_cache = threading.Lock()

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------

    def _carregar_modelo(self):
        with self._trava_modelo:
            if self._modelo is None:
                t0 = time.monotonic()
                try:
                    from sentence_transformers import SentenceTransformer

                    self._modelo = SentenceTransformer(
                        self._cfg.embedding_model, device="cpu"
                    )
                    self._modelo.max_seq_length = MAX_TOKENS
                except Exception as e:  # noqa: BLE001
                    raise ModeloIndisponivel(
                        f"Não foi possível carregar o modelo de embeddings "
                        f"'{self._cfg.embedding_model}': {e}"
                    ) from e
                logger.info(
                    "Modelo de embeddings carregado em %.1fs", time.monotonic() - t0
                )
        return self._modelo

    def _carregar_acervo(self) -> None:
        with self._trava_acervo:
            if self._acervo is not None:
                return
            t0 = time.monotonic()
            acervo: list[dict[str, Any]] = []
            matriz: Optional[np.ndarray] = None
            dim = None
            with conexao() as conn:
                self._conferir_modelo_do_indice(conn)
                total = conn.execute("SELECT COUNT(*) FROM proposicoes_embeddings").fetchone()[0]
                for row in conn.execute(
                    """
                    SELECT pe.id_proposicao, pe.embedding, p.sigla_tipo, p.numero,
                           p.ano, p.ementa, p.data_apresentacao, p.casa
                    FROM proposicoes_embeddings pe
                    LEFT JOIN proposicoes p ON pe.id_proposicao = p.id_proposicao
                    """
                ):
                    if not row[1]:
                        continue
                    vetor = np.frombuffer(row[1], dtype=np.float32)
                    if dim is None:
                        dim = len(vetor)
                        # Matriz alocada uma vez e preenchida linha a linha: np.vstack dobraria o pico de memória.
                        matriz = np.empty((total, dim), dtype=np.float32)
                    if len(vetor) != dim:
                        continue
                    assert matriz is not None
                    matriz[len(acervo)] = vetor
                    acervo.append(
                        {
                            "id_proposicao": row[0],
                            "sigla_tipo": row[2] or "N/D",
                            "numero": row[3] or 0,
                            "ano": row[4] or 0,
                            "ementa": row[5] or "",
                            "data_apresentacao": row[6] or "",
                            "casa": row[7] or "",
                        }
                    )
            if not acervo:
                raise RuntimeError("proposicoes_embeddings está vazia.")
            assert matriz is not None
            self._matriz = matriz if len(acervo) == total else matriz[: len(acervo)].copy()
            self._normas = np.linalg.norm(self._matriz, axis=1)
            self._indice_por_id = {c["id_proposicao"]: i for i, c in enumerate(acervo)}
            gerador = np.random.default_rng(0)
            self._amostra = gerador.choice(
                len(acervo), size=min(len(acervo), TAMANHO_AMOSTRA_DESTAQUE), replace=False
            )
            self._acervo = acervo
            logger.info(
                "Acervo vetorial em memória: %d ementas em %.1fs",
                len(acervo),
                time.monotonic() - t0,
            )

    def _conferir_modelo_do_indice(self, conn: sqlite3.Connection) -> None:
        """
        Falha se o banco declara vetores de outro modelo (o ranking sairia
        aleatório, sem erro). Bancos sem a tabela `indice_vetorial` não são conferidos.
        """
        try:
            linha = conn.execute(
                "SELECT valor FROM indice_vetorial WHERE chave = 'modelo'"
            ).fetchone()
        except sqlite3.OperationalError:
            logger.warning(
                "Banco sem `indice_vetorial`: não dá para conferir se os vetores são de %s",
                self._cfg.embedding_model,
            )
            return
        if linha and linha[0] != self._cfg.embedding_model:
            raise IndiceDeOutroModelo(
                f"Os vetores do banco são de '{linha[0]}', mas LEIS_EMBEDDING_MODEL é "
                f"'{self._cfg.embedding_model}'. Ajuste a variável ou reindexe com "
                "etl/reindexar_embeddings.py."
            )

    def chunks_disponiveis(self) -> bool:
        if self._chunks_disponiveis is None:
            try:
                with conexao() as conn:
                    self._chunks_disponiveis = (
                        conn.execute(
                            "SELECT 1 FROM proposicoes_chunks_embeddings LIMIT 1"
                        ).fetchone()
                        is not None
                    )
            except Exception:  # noqa: BLE001
                self._chunks_disponiveis = False
        return self._chunks_disponiveis

    def aquecer(self) -> None:
        self._carregar_modelo()
        self._carregar_acervo()
        self.chunks_disponiveis()
        self.gerar_embedding("aquecimento")

    @property
    def pronto(self) -> bool:
        return self._modelo is not None and self._acervo is not None

    # ------------------------------------------------------------------
    # Primitivas
    # ------------------------------------------------------------------

    def gerar_embedding(self, texto: str) -> np.ndarray:
        """Vetor normalizado de uma CONSULTA (com a instrução do modelo), em cache."""
        if texto:
            texto = ementa_sem_profissoes(texto)
        with self._trava_cache:
            if texto in self._vetores_consulta:
                self._vetores_consulta.move_to_end(texto)
                return self._vetores_consulta[texto]
        instrucao = self._cfg.embedding_instrucao
        if instrucao and not instrucao.endswith((" ", "\n")):
            instrucao += " "
        modelo = self._carregar_modelo()
        with self._trava_encode:
            vetor = modelo.encode(
                instrucao + texto, convert_to_numpy=True, normalize_embeddings=True
            )
        vetor = np.asarray(vetor, dtype=np.float32)
        with self._trava_cache:
            self._vetores_consulta[texto] = vetor
            while len(self._vetores_consulta) > MAX_VETORES_EM_CACHE:
                self._vetores_consulta.popitem(last=False)
        return vetor

    def estatisticas_do_termo(self, vetor: np.ndarray) -> tuple[float, float]:
        """(mediana, percentil 99) dos cossenos do termo numa amostra fixa das ementas."""
        self._carregar_acervo()
        assert self._matriz is not None and self._normas is not None
        assert self._amostra is not None
        cossenos = (self._matriz[self._amostra] @ vetor) / np.maximum(
            self._normas[self._amostra], 1e-12
        )
        return float(np.median(cossenos)), float(np.percentile(cossenos, 99))

    def gerar_embeddings_normalizados(self, textos: list[str]) -> np.ndarray:
        """Vetores de DOCUMENTOS em lote (sem instrução), com norma L2 = 1, como o ETL grava."""
        modelo = self._carregar_modelo()
        with self._trava_encode:
            vetores = modelo.encode(
                textos, batch_size=64, convert_to_numpy=True, show_progress_bar=False
            )
        vetores = np.asarray(vetores, dtype=np.float32)
        normas = np.linalg.norm(vetores, axis=1, keepdims=True)
        return vetores / np.maximum(normas, 1e-12)

    @staticmethod
    def sanitizar_fts(consulta: str) -> str:
        """
        Converte o termo em expressão FTS5: sem pontuação e stopwords, acentos
        normalizados, radical aproximado (trunca em 6 letras) e termos unidos
        por AND. O recall do AND restritivo é recuperado pelo fallback em OR.
        """
        if not consulta:
            return ""
        palavras = re.sub(r"[^\w\s]", " ", consulta).split()
        if not palavras:
            return ""
        significativas = [p for p in palavras if p.lower() not in STOPWORDS_PT]
        if significativas:
            palavras = significativas
        termos = []
        for p in palavras:
            norm = p.lower().translate(_ACENTOS)
            if len(norm) > 6:
                norm = norm[:6]
            elif len(norm) > 3 and norm.endswith("s"):
                norm = norm[:-1]
            termos.append(f"{norm}*" if len(norm) > 3 else norm)
        return " AND ".join(termos)

    def _similaridades(
        self, vetor: np.ndarray, lista_ids: Optional[list[int]]
    ) -> tuple[np.ndarray, np.ndarray]:
        """(índices no acervo, cossenos) para o universo pedido."""
        self._carregar_acervo()
        assert self._matriz is not None and self._normas is not None
        assert self._indice_por_id is not None
        if lista_ids is None:
            indices = np.arange(len(self._acervo))
            matriz, normas = self._matriz, self._normas
        else:
            indices = np.array(
                [self._indice_por_id[i] for i in lista_ids if i in self._indice_por_id],
                dtype=np.int64,
            )
            if not len(indices):
                return indices, np.array([], dtype=np.float32)
            matriz, normas = self._matriz[indices], self._normas[indices]
        denom = normas * np.linalg.norm(vetor)
        prod = matriz @ vetor
        sims = np.zeros_like(prod)
        validos = denom > 0
        sims[validos] = prod[validos] / denom[validos]
        return indices, sims

    def buscar_fts_ementas(
        self,
        termo: str,
        lista_ids: Optional[list[int]] = None,
        completar_com_or: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Ementas que casam com o termo no FTS5, na ordem do BM25.

        `completar_com_or=False` devolve só os casamentos com todas as palavras
        (AND). É o que a busca híbrida usa: o complemento em OR piorava a fusão.
        """
        if lista_ids is not None and not lista_ids:
            return []
        expressao = self.sanitizar_fts(termo)
        if not expressao:
            return []

        # O recorte por `lista_ids` é feito em Python, depois do MATCH e antes do
        # teto: um `IN (...)` no FTS5 é ordens de grandeza mais lento.
        filtro = set(lista_ids) if lista_ids is not None else None

        def executar(conn, expr: str) -> Optional[list[dict[str, Any]]]:
            try:
                cursor = conn.execute(
                    "SELECT rowid, rank FROM proposicoes_fts WHERE ementa MATCH ? "
                    "ORDER BY rank ASC, rowid ASC",
                    [expr],
                )
                casados: list[tuple[int, float]] = []
                for rowid, rank in cursor:
                    if filtro is None or rowid in filtro:
                        casados.append((rowid, rank))
                        if len(casados) >= MAX_RESULTADOS_LEXICAIS:
                            break
            except Exception:  # sintaxe FTS5 inválida
                return None
            metadados: dict[int, tuple] = {}
            ids = [i for i, _ in casados]
            for inicio in range(0, len(ids), 900):
                lote = ids[inicio : inicio + 900]
                for r in conn.execute(
                    "SELECT id_proposicao, sigla_tipo, numero, ano, ementa, "
                    "data_apresentacao, casa FROM proposicoes "
                    f"WHERE id_proposicao IN ({','.join('?' * len(lote))})",
                    lote,
                ):
                    metadados[r[0]] = r
            return [
                {
                    "id_proposicao": rowid,
                    "sigla_tipo": metadados[rowid][1] or "N/D",
                    "numero": metadados[rowid][2] or 0,
                    "ano": metadados[rowid][3] or 0,
                    "ementa": metadados[rowid][4] or "",
                    "data_apresentacao": metadados[rowid][5] or "",
                    "casa": metadados[rowid][6] or "",
                    "score_lexical": float(rank),
                }
                for rowid, rank in casados
                if rowid in metadados
            ]

        with conexao() as conn:
            resultado_and = executar(conn, expressao)
            resultados = resultado_and or []
            # OR quando o AND falhou ou trouxe pouco; os acertos do AND ficam no topo.
            if completar_com_or and (
                resultado_and is None or len(resultados) < MIN_RESULTADOS_LEXICAIS
            ):
                todas = re.findall(r"\b\w+\b", termo)
                palavras = [p for p in todas if p.lower() not in STOPWORDS_PT] or todas
                if palavras:
                    expr_or = " OR ".join(
                        f"{p}*" if len(p) > 3 else p for p in palavras
                    )
                    resultado_or = executar(conn, expr_or)
                    if resultado_or is not None:
                        vistos = {r["id_proposicao"] for r in resultados}
                        resultados = resultados + [
                            r for r in resultado_or if r["id_proposicao"] not in vistos
                        ]
        return resultados[:MAX_RESULTADOS_LEXICAIS]

    # ------------------------------------------------------------------
    # Camada de ementas
    # ------------------------------------------------------------------

    def buscar_ementas(
        self,
        termo: str,
        lista_ids: Optional[list[int]] = None,
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
        casa: Optional[str] = None,
        threshold: float = 0.0,
        top_k: int = 30,
    ) -> list[dict[str, Any]]:
        """
        Ementas por RRF (k = 60) da lista lexical e da densa.

        `threshold` > 0 corta a lista densa por cosseno; o padrão é não cortar,
        porque a escala do cosseno depende do modelo. `destaque_semantico` diz
        quanto cada resultado se destaca do acervo para este termo.
        """
        casa = normalizar_casa(casa)
        if lista_ids is not None and not lista_ids:
            return []

        # 1. Lexical, com os filtros aplicados depois (o FTS5 não filtra data/casa).
        lexicais = []
        for r in self.buscar_fts_ementas(termo, lista_ids, completar_com_or=False):
            if casa and r["casa"] != casa:
                continue
            data = r["data_apresentacao"]
            if data_inicio and data and data < data_inicio:
                continue
            if data_fim and data and data[:10] > data_fim[:10]:
                continue
            lexicais.append(r)

        # 2. Densa sobre o acervo em memória, com os mesmos filtros.
        vetor = self.gerar_embedding(termo)
        estatisticas = self.estatisticas_do_termo(vetor)
        indices, sims = self._similaridades(vetor, lista_ids)
        assert self._acervo is not None
        semanticos: list[dict[str, Any]] = []
        ordem = np.argsort(-sims, kind="stable")
        fim_limite = (
            f"{data_fim}T23:59:59" if data_fim and len(data_fim) == 10 else data_fim
        )
        for pos in ordem:
            score = float(sims[pos])
            if (threshold and score < threshold) or len(semanticos) >= MAX_RESULTADOS_DENSOS:
                break
            item = self._acervo[int(indices[pos])]
            if casa and item["casa"] != casa:
                continue
            data = item["data_apresentacao"]
            if data_inicio and (not data or data < data_inicio):
                continue
            if fim_limite and (not data or data > fim_limite):
                continue
            semanticos.append({**item, "score": score})

        # 3. RRF.
        rank_lex = {r["id_proposicao"]: i for i, r in enumerate(lexicais, 1)}
        rank_sem = {r["id_proposicao"]: i for i, r in enumerate(semanticos, 1)}
        lex_por_id = {r["id_proposicao"]: r for r in lexicais}
        sem_por_id = {r["id_proposicao"]: r for r in semanticos}

        fundidos = []
        for pid in set(rank_lex) | set(rank_sem):
            rrf = 0.0
            if pid in rank_lex:
                rrf += 1.0 / (K_RRF + rank_lex[pid])
            if pid in rank_sem:
                rrf += 1.0 / (K_RRF + rank_sem[pid])
            base = sem_por_id.get(pid) or lex_por_id[pid]
            fundidos.append(
                {
                    "id_proposicao": pid,
                    "sigla_tipo": base["sigla_tipo"],
                    "numero": base["numero"],
                    "ano": base["ano"],
                    "ementa": base["ementa"],
                    "data_apresentacao": base["data_apresentacao"],
                    "casa": base["casa"],
                    "score": rrf,
                    "rank_lexical": rank_lex.get(pid),
                    "rank_semantico": rank_sem.get(pid),
                    "score_lexical": lex_por_id.get(pid, {}).get("score_lexical"),
                    "score_semantico": sem_por_id.get(pid, {}).get("score"),
                    "destaque_semantico": destaque(
                        sem_por_id.get(pid, {}).get("score"), estatisticas
                    ),
                }
            )
        fundidos.sort(key=lambda x: x["score"], reverse=True)
        return fundidos[:top_k]

    # ------------------------------------------------------------------
    # Camada de inteiro teor
    # ------------------------------------------------------------------

    def _prefiltro_trechos(
        self, termo: str, lista_ids: Optional[list[int]], lexicais: list
    ) -> list[int]:
        """
        Proposições cujos vetores de trechos são lidos para a parte densa.

        Candidatas, em ordem: as do lexical (ordem do BM25) e as de ementa mais
        próxima do termo pela densa, dentro de `lista_ids` quando houver, até
        MAX_PROPOSICOES_DENSA_TRECHOS.
        """
        if lista_ids and len(lista_ids) <= MAX_PROPOSICOES_DENSA_TRECHOS:
            return list(lista_ids)
        permitidos = set(lista_ids) if lista_ids else None
        candidatos: dict[int, None] = {}
        for r in lexicais:
            if permitidos is None or r[1] in permitidos:
                candidatos.setdefault(r[1], None)
                if len(candidatos) >= MAX_PROPOSICOES_DENSA_TRECHOS // 2:
                    break
        indices, sims = self._similaridades(self.gerar_embedding(termo), lista_ids)
        assert self._acervo is not None
        for pos in np.argsort(-sims, kind="stable"):
            if len(candidatos) >= MAX_PROPOSICOES_DENSA_TRECHOS:
                break
            candidatos.setdefault(self._acervo[int(indices[pos])]["id_proposicao"], None)
        return list(candidatos)

    def buscar_trechos(
        self,
        termo: str,
        lista_ids: Optional[list[int]] = None,
        casa: Optional[str] = None,
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
        threshold: float = 0.0,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        casa = normalizar_casa(casa)
        if not self.chunks_disponiveis():
            return []

        filtros: list[str] = []
        params: list[Any] = []
        if lista_ids:
            filtros.append(f"c.id_proposicao IN ({','.join('?' * len(lista_ids))})")
            params.extend(lista_ids)
        if casa:
            filtros.append("p.casa = ?")
            params.append(casa)
        if data_inicio:
            filtros.append("p.data_apresentacao >= ?")
            params.append(data_inicio)
        if data_fim:
            filtros.append("p.data_apresentacao <= ?")
            params.append(data_fim)

        sql_lex = """
            SELECT c.id_chunk, c.id_proposicao, c.tipo_dispositivo,
                   c.identificador_normativo, c.texto_original,
                   p.sigla_tipo, p.numero, p.ano, p.ementa, p.casa,
                   p.data_apresentacao, f.rank
            FROM proposicoes_chunks_fts f
            JOIN proposicoes_chunks c ON f.rowid = c.id_chunk_int
            JOIN proposicoes p ON c.id_proposicao = p.id_proposicao
            WHERE f.texto_enriquecido MATCH ?
        """
        if filtros:
            sql_lex += " AND " + " AND ".join(filtros)
        sql_lex += " ORDER BY f.rank ASC"

        with conexao() as conn:
            lexicais: list = []
            try:
                lexicais = conn.execute(
                    sql_lex, [self.sanitizar_fts(termo), *params]
                ).fetchall()
            except Exception:
                palavras = re.findall(r"\b\w+\b", termo)
                if palavras:
                    try:
                        expr = " OR ".join(
                            f"{p}*" if len(p) > 3 else p for p in palavras
                        )
                        lexicais = conn.execute(sql_lex, [expr, *params]).fetchall()
                    except Exception:
                        lexicais = []

            prefiltro = self._prefiltro_trechos(termo, lista_ids, lexicais)
            sql_emb = """
                SELECT ce.id_chunk, ce.id_proposicao, ce.embedding,
                       c.tipo_dispositivo, c.identificador_normativo, c.texto_original,
                       p.sigla_tipo, p.numero, p.ano, p.ementa, p.casa, p.data_apresentacao
                FROM proposicoes_chunks_embeddings ce
                JOIN proposicoes_chunks c ON ce.id_chunk = c.id_chunk
                JOIN proposicoes p ON ce.id_proposicao = p.id_proposicao
            """
            filtros_emb: list[str] = [
                f"ce.id_proposicao IN ({','.join('?' * len(prefiltro))})"
            ]
            params_emb: list[Any] = list(prefiltro)
            for cond, valor in (
                ("p.casa = ?", casa),
                ("p.data_apresentacao >= ?", data_inicio),
                ("p.data_apresentacao <= ?", data_fim),
            ):
                if valor:
                    filtros_emb.append(cond)
                    params_emb.append(valor)
            sql_emb += " WHERE " + " AND ".join(filtros_emb)
            linhas_emb = conn.execute(sql_emb, params_emb).fetchall()

        vetor = self.gerar_embedding(termo)

        sem_score: dict[str, float] = {}
        sem_meta: dict[str, dict[str, Any]] = {}
        if linhas_emb:
            matriz = np.vstack(
                [np.frombuffer(r[2], dtype=np.float32) for r in linhas_emb]
            )
            scores = matriz @ vetor
            for i, r in enumerate(linhas_emb):
                sc = float(scores[i])
                if not threshold or sc >= threshold:
                    sem_score[r[0]] = sc
                    sem_meta[r[0]] = {
                        "id_chunk": r[0],
                        "id_proposicao": r[1],
                        "tipo_dispositivo": r[3],
                        "identificador_normativo": r[4],
                        "texto_original": r[5],
                        "sigla_tipo": r[6],
                        "numero": r[7],
                        "ano": r[8],
                        "ementa": r[9],
                        "casa": r[10],
                        "data_apresentacao": r[11],
                    }

        lex_por_id: dict[str, dict[str, Any]] = {}
        for r in lexicais:
            lex_por_id[r[0]] = {
                "id_chunk": r[0],
                "id_proposicao": r[1],
                "tipo_dispositivo": r[2],
                "identificador_normativo": r[3],
                "texto_original": r[4],
                "sigla_tipo": r[5],
                "numero": r[6],
                "ano": r[7],
                "ementa": r[8],
                "casa": r[9],
                "data_apresentacao": r[10],
                "score_lexical": float(r[11]),
            }

        rank_lex = {cid: i for i, cid in enumerate(lex_por_id, 1)}
        rank_sem = {
            cid: i
            for i, (cid, _) in enumerate(
                sorted(sem_score.items(), key=lambda x: x[1], reverse=True), 1
            )
        }
        fundidos = []
        for cid in set(rank_lex) | set(rank_sem):
            rrf = 0.0
            if cid in rank_lex:
                rrf += 1.0 / (K_RRF + rank_lex[cid])
            if cid in rank_sem:
                rrf += 1.0 / (K_RRF + rank_sem[cid])
            fundidos.append(
                {
                    **(sem_meta.get(cid) or lex_por_id.get(cid) or {}),
                    "score": rrf,
                    "score_semantico": sem_score.get(cid),
                    "score_lexical": lex_por_id.get(cid, {}).get("score_lexical"),
                }
            )
        fundidos.sort(key=lambda x: x["score"], reverse=True)
        return fundidos[:top_k]


_buscador: Optional[Buscador] = None
_trava_global = threading.Lock()


def obter_buscador() -> Buscador:
    global _buscador
    with _trava_global:
        if _buscador is None:
            _buscador = Buscador()
        return _buscador
