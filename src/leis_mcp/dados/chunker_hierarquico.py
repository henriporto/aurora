#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Segmentador (Chunker) Hierárquico para Projetos de Lei.
Baseado nas divisões estruturais da Lei Complementar nº 95/1998 (Títulos, Capítulos,
Seções, Artigos, Parágrafos, Incisos).

Garante:
 - Injeção do contexto do Caput do Artigo (e Parágrafo, se aplicável) em cada chunk.
 - Captura de textos marginais: Preâmbulo, Anexos, Fechos.
 - Fallback para documentos sem estrutura Art./§/I (VETs, calendários, decisões).
"""

import re
import unicodedata
from typing import Dict, Any, List, Optional


# ---------------------------------------------------------------------------
# Nós da hierarquia
# ---------------------------------------------------------------------------

class ArtigoNode:
    """Representa um Artigo na hierarquia legal (unidade básica)."""
    def __init__(self, identificador: str, caput: str, contexto_secao: str):
        self.identificador = identificador   # Ex: "Art. 3º"
        self.caput = caput.strip()           # Texto completo do Caput
        self.contexto_secao = contexto_secao
        self.paragrafos: List['ParagrafoNode'] = []
        self.incisos: List['IncisoNode'] = []

    def __repr__(self) -> str:
        return f"<ArtigoNode {self.identificador}>"


class ParagrafoNode:
    """Representa um Parágrafo sob um Artigo."""
    def __init__(self, identificador: str, texto: str, artigo_pai: 'ArtigoNode'):
        self.identificador = identificador   # Ex: "§ 2º" ou "Parágrafo único"
        self.texto = texto.strip()
        self.artigo_pai = artigo_pai
        self.incisos: List['IncisoNode'] = []

    def __repr__(self) -> str:
        return f"<ParagrafoNode {self.identificador} de {self.artigo_pai.identificador}>"


class IncisoNode:
    """Representa um Inciso sob um Artigo ou Parágrafo."""
    def __init__(self, identificador: str, texto: str,
                 artigo_pai: 'ArtigoNode',
                 paragrafo_pai: Optional['ParagrafoNode'] = None):
        self.identificador = identificador
        self.texto = texto.strip()
        self.artigo_pai = artigo_pai
        self.paragrafo_pai = paragrafo_pai

    def __repr__(self) -> str:
        pai = self.paragrafo_pai.identificador if self.paragrafo_pai else self.artigo_pai.identificador
        return f"<IncisoNode {self.identificador} sob {pai}>"


class PreambuloNode:
    """
    Representa o Preâmbulo / ementa / considerandos de um documento legal —
    todo texto que aparece ANTES do primeiro Artigo numerado.
    """
    def __init__(self, texto: str):
        self.texto = texto.strip()

    def __repr__(self) -> str:
        return f"<PreambuloNode {len(self.texto)} chars>"


class AnexoNode:
    """Representa um Anexo na estrutura legal (após os artigos principais)."""
    def __init__(self, identificador: str, texto: str):
        self.identificador = identificador.strip()
        self.texto = texto.strip()

    def __repr__(self) -> str:
        return f"<AnexoNode {self.identificador}>"


class FechoNode:
    """Representa o Fecho do documento (data, local, assinaturas)."""
    def __init__(self, texto: str):
        self.texto = texto.strip()

    def __repr__(self) -> str:
        return f"<FechoNode {len(self.texto)} chars>"


# ---------------------------------------------------------------------------
# Chunker principal
# ---------------------------------------------------------------------------

def montar_texto_enriquecido(
    documento: str,
    identificador_dispositivo: str,
    tipo_dispositivo: str,
    contexto_secao: Optional[str],
    artigo_caput: Optional[str],
    paragrafo_texto: Optional[str],
    texto_original: str,
) -> str:
    """
    Texto que é vetorizado e indexado no FTS5 para cada trecho.

    Não inclui a ementa nem o autor (a ementa tem índice próprio; repeti-la
    deixava todos os trechos da proposição com o mesmo vetor). Fica só o
    contexto que muda o sentido do dispositivo: a seção, o caput do artigo e o
    parágrafo. Função pura, reaplicável às colunas de `proposicoes_chunks`.
    """
    linhas = [f"{documento} · {identificador_dispositivo} ({tipo_dispositivo})"]
    if contexto_secao and contexto_secao not in ("Preâmbulo", "Anexo", "Fecho"):
        linhas.append(f"Seção: {contexto_secao}")
    if tipo_dispositivo not in ("Caput", "Preâmbulo", "Anexo", "Fecho"):
        if artigo_caput:
            linhas.append(f"Caput do artigo: {artigo_caput}")
        if paragrafo_texto and tipo_dispositivo == "Inciso":
            linhas.append(f"Parágrafo: {paragrafo_texto}")
    return "\n".join(linhas) + "\n\n" + texto_original


class HierarchicalLegislativeChunker:
    """
    Chunker hierárquico estruturado de leis e projetos de lei de acordo com a LC 95/1998.
    Suporta também textos marginais: Preâmbulo, Anexos, Fecho e Vetos Presidenciais.
    """

    # Divisões estruturais (TÍTULO I — ..., CAPÍTULO II, etc.)
    RE_DIVISAO = re.compile(
        r'^\s*(LIVRO|TÍTULO|CAPÍTULO|SEÇÃO|SUBSEÇÃO)\s+([IVXLCDM\d\-\w]+)(?:\s*[-–—]\s*(.*))?$',
        re.IGNORECASE
    )

    # Artigo (Art. 1º, Art. 10-A)
    RE_ARTIGO = re.compile(
        r'^\s*Art\.\s*(?:\d+-\w+|\d+)(?:[º°ª\.]|\b)(?:\s*[-–—]\s*)?',
        re.IGNORECASE
    )

    # Parágrafo (§ 1º, § 2º-A, Parágrafo único.)
    RE_PARAGRAFO = re.compile(
        r'^\s*(§\s*(?:\d+-\w+|\d+)(?:[º°ª\.]|\b)|Parágrafo\s+único\.)(?:\s*[-–—]\s*)?',
        re.IGNORECASE
    )

    # Inciso (I -, II -, III —)  — não confunde com itens de listas que usam letras
    RE_INCISO = re.compile(
        r'^\s*([IVXLCDM]+)\s*[-–—]',
        re.IGNORECASE
    )

    # Alínea (a), b), c) ...)
    RE_ALINEA = re.compile(r'^\s*([a-z])\)\s+', re.IGNORECASE)

    # Anexo / Apêndice
    RE_ANEXO = re.compile(
        r'^\s*(APÊNDICE|ANEXOS?|ANEXO\s+ÚNICO|ANEXO\s+[IVXLCDM\d\-\w]+)(?:\s*[-–—]\s*(.*))?$',
        re.IGNORECASE
    )

    # Fecho (Brasília, em ..., Sala das Sessões, ...)
    RE_DATA_FECHO = re.compile(
        r'^\s*(Brasília|Sala\s+das\s+Sessões|Plenário|Sala\s+de\s+Comissão|Sala\s+de\s+Comissões)'
        r'\s*,\s*(?:em\s*)?(?:\d+|_+)\s+de\s+[a-zA-ZçÇ_]+\s+de\s+(?:\d{4}|_+)\b',
        re.IGNORECASE
    )

    # Razões de Veto (VET específico)
    RE_RAZOES_VETO = re.compile(
        r'^\s*(RAZÕES?\s+DO\s+VETO|MENSAGEM\s+DE\s+VETO|DISPOSITIVOS?\s+VETADO|Senhor\s+Presidente\s+do\s+Congresso)',
        re.IGNORECASE
    )

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    def _remover_acentos(self, texto: str) -> str:
        """Remove acentuação para normalizar chaves de dicionário."""
        return "".join(
            c for c in unicodedata.normalize('NFD', texto)
            if not unicodedata.combining(c)
        )

    def _gerar_string_secao(self, contexto: Dict[str, str]) -> str:
        """Gera o caminho hierárquico atual (ex: 'TÍTULO I > CAPÍTULO II')."""
        partes = [contexto[k] for k in ["livro", "titulo", "capitulo", "secao", "subsecao"] if contexto[k]]
        return " > ".join(partes)

    def _split_text_into_paragraphs(self, text: str, max_chars: int = 1200) -> List[str]:
        """
        Divide texto livre em blocos semânticos respeitando quebras de linha,
        com tamanho máximo de max_chars caracteres por chunk.
        """
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
        chunks: List[str] = []
        current: List[str] = []
        current_len = 0

        for p in paragraphs:
            if len(p) > max_chars:
                # Flush do buffer atual
                if current:
                    chunks.append("\n".join(current))
                    current = []
                    current_len = 0
                # Divide o parágrafo gigante por palavras
                while len(p) > max_chars:
                    split_idx = p.rfind(' ', 0, max_chars)
                    if split_idx < max_chars // 2:
                        split_idx = max_chars
                    chunks.append(p[:split_idx].strip())
                    p = p[split_idx:].strip()
                if p:
                    current.append(p)
                    current_len = len(p)
            else:
                if current_len + len(p) + 1 > max_chars:
                    chunks.append("\n".join(current))
                    current = [p]
                    current_len = len(p)
                else:
                    current.append(p)
                    current_len += len(p) + 1

        if current:
            chunks.append("\n".join(current))

        return chunks

    # ------------------------------------------------------------------
    # Parser principal: texto → lista de nós
    # ------------------------------------------------------------------

    def parse_hierarchy(self, text: str) -> List[object]:
        """
        Analisa o texto sequencialmente e constrói a lista de nós hierárquicos.
        Retorna mix de PreambuloNode, ArtigoNode, AnexoNode, FechoNode.
        """
        lines = text.split('\n')
        n_lines = len(lines)

        contexto: Dict[str, str] = {
            "livro": "", "titulo": "", "capitulo": "", "secao": "", "subsecao": ""
        }

        nodes: List[object] = []
        preambulo_lines: List[str] = []
        found_first_artigo = False

        active_artigo: Optional[ArtigoNode] = None
        active_paragrafo: Optional[ParagrafoNode] = None
        active_inciso: Optional[IncisoNode] = None

        in_anexo = False
        active_anexo: Optional[AnexoNode] = None
        in_fecho = False
        fecho_lines: List[str] = []

        i = 0
        while i < n_lines:
            line_raw = lines[i]
            line_stripped = line_raw.strip()

            if not line_stripped:
                i += 1
                continue

            # ---- 1. Fecho detectado — captura tudo até o fim ----
            if self.RE_DATA_FECHO.match(line_stripped):
                # Flush de Artigo/Parágrafo/Inciso ativos
                active_artigo = None
                active_paragrafo = None
                active_inciso = None
                in_fecho = True
                in_anexo = False
                active_anexo = None
                fecho_lines.append(line_stripped)
                i += 1
                continue

            if in_fecho:
                fecho_lines.append(line_stripped)
                i += 1
                continue

            # ---- 2. Razões de Veto (VET) ----
            if self.RE_RAZOES_VETO.match(line_stripped):
                # Trata como preâmbulo adicional
                found_first_artigo = False  # Reseta para acumular tudo como preâmbulo
                preambulo_lines.append(line_stripped)
                i += 1
                continue

            # ---- 3. Anexo ----
            anexo_match = self.RE_ANEXO.match(line_stripped)
            if anexo_match:
                # Flush artigo ativo
                active_artigo = None
                active_paragrafo = None
                active_inciso = None
                in_anexo = True
                nome_anexo = line_stripped
                if active_anexo and active_anexo.texto:
                    nodes.append(active_anexo)
                active_anexo = AnexoNode(nome_anexo, "")
                i += 1
                continue

            if in_anexo and active_anexo is not None:
                # Novo Anexo?
                if self.RE_ANEXO.match(line_stripped):
                    if active_anexo.texto:
                        nodes.append(active_anexo)
                    active_anexo = AnexoNode(line_stripped, "")
                    i += 1
                    continue
                active_anexo.texto += line_stripped + "\n"
                i += 1
                continue

            # ---- 4. Divisão estrutural (TÍTULO, CAPÍTULO, SEÇÃO...) ----
            div_match = self.RE_DIVISAO.match(line_stripped)
            if div_match:
                tipo = self._remover_acentos(div_match.group(1).lower())
                valor = div_match.group(2)
                nome = div_match.group(3) or ""

                # Tenta pegar o nome na próxima linha se não veio inline
                if not nome:
                    j = i + 1
                    while j < n_lines and not lines[j].strip():
                        j += 1
                    if j < n_lines:
                        proxima = lines[j].strip()
                        if not any(pat.match(proxima) for pat in [
                            self.RE_DIVISAO, self.RE_ARTIGO,
                            self.RE_PARAGRAFO, self.RE_INCISO, self.RE_ANEXO
                        ]):
                            nome = proxima
                            i = j  # consome a linha do nome

                descricao = f"{div_match.group(1)} {valor}"
                if nome:
                    descricao += f" - {nome}"
                contexto[tipo] = descricao

                # Zera níveis inferiores de contexto
                niveis = ["livro", "titulo", "capitulo", "secao", "subsecao"]
                if tipo in niveis:
                    idx_tipo = niveis.index(tipo)
                    for k in range(idx_tipo + 1, len(niveis)):
                        contexto[niveis[k]] = ""

                active_artigo = None
                active_paragrafo = None
                active_inciso = None
                i += 1
                continue

            # ---- 5. Artigo ----
            art_match = self.RE_ARTIGO.match(line_stripped)
            if art_match:
                found_first_artigo = True

                # Flush preâmbulo se existir
                if preambulo_lines:
                    nodes.append(PreambuloNode("\n".join(preambulo_lines)))
                    preambulo_lines = []

                identificador = art_match.group(0).strip(" -—\t.")
                active_artigo = ArtigoNode(
                    identificador=identificador,
                    caput=line_stripped,
                    contexto_secao=self._gerar_string_secao(contexto)
                )
                nodes.append(active_artigo)
                active_paragrafo = None
                active_inciso = None
                i += 1
                continue

            # ---- 6. Parágrafo ----
            parag_match = self.RE_PARAGRAFO.match(line_stripped)
            if parag_match and active_artigo:
                identificador = parag_match.group(1).strip(" -—\t.")
                active_paragrafo = ParagrafoNode(
                    identificador=identificador,
                    texto=line_stripped,
                    artigo_pai=active_artigo
                )
                active_artigo.paragrafos.append(active_paragrafo)
                active_inciso = None
                i += 1
                continue

            # ---- 7. Inciso ----
            inciso_match = self.RE_INCISO.match(line_stripped)
            if inciso_match and active_artigo:
                identificador = f"Inciso {inciso_match.group(1).strip()}"
                if active_paragrafo:
                    active_inciso = IncisoNode(
                        identificador=identificador,
                        texto=line_stripped,
                        artigo_pai=active_artigo,
                        paragrafo_pai=active_paragrafo
                    )
                    active_paragrafo.incisos.append(active_inciso)
                else:
                    active_inciso = IncisoNode(
                        identificador=identificador,
                        texto=line_stripped,
                        artigo_pai=active_artigo
                    )
                    active_artigo.incisos.append(active_inciso)
                i += 1
                continue

            # ---- 8. Continuação / texto livre ----
            if active_inciso:
                active_inciso.texto += "\n" + line_stripped
            elif active_paragrafo:
                active_paragrafo.texto += "\n" + line_stripped
            elif active_artigo:
                active_artigo.caput += "\n" + line_stripped
            else:
                # Texto antes do primeiro Artigo → Preâmbulo
                preambulo_lines.append(line_stripped)

            i += 1

        # --- Flush final ---
        if preambulo_lines and not found_first_artigo:
            nodes.append(PreambuloNode("\n".join(preambulo_lines)))
        elif preambulo_lines:
            # Preâmbulo residual após artigos (raro) — descarta ou insere antes do primeiro Artigo
            pass

        if active_anexo and active_anexo.texto:
            nodes.append(active_anexo)

        if fecho_lines:
            nodes.append(FechoNode("\n".join(fecho_lines)))

        return nodes

    # ------------------------------------------------------------------
    # Criação de chunks enriquecidos
    # ------------------------------------------------------------------

    def _criar_bloco_enriquecido(self,
                                  metadata: Dict[str, Any],
                                  contexto_secao: str,
                                  identificador_dispositivo: str,
                                  tipo_dispositivo: str,
                                  artigo_caput: str,
                                  paragrafo_texto: Optional[str],
                                  texto_original: str) -> Dict[str, Any]:
        """
        Monta o dicionário do chunk estruturado e constrói o 'texto_enriquecido'
        (ver `montar_texto_enriquecido`).
        """
        tipo_lei   = metadata.get('tipo', 'PL')
        num_lei    = metadata.get('numero', 'S/N')
        ano_lei    = metadata.get('ano', '')
        id_prop    = metadata.get('id_proposicao', 0)

        texto_original_limpo = "\n".join(
            l.strip() for l in texto_original.split("\n") if l.strip()
        )
        texto_enriquecido = montar_texto_enriquecido(
            documento=f"{tipo_lei} {num_lei}/{ano_lei}",
            identificador_dispositivo=identificador_dispositivo,
            tipo_dispositivo=tipo_dispositivo,
            contexto_secao=contexto_secao,
            artigo_caput=artigo_caput,
            paragrafo_texto=paragrafo_texto,
            texto_original=texto_original_limpo,
        )

        # ID de chunk determinístico e único
        safe_id = (
            identificador_dispositivo
            .replace(" ", "_").replace("º", "").replace(".", "")
            .replace("§", "Par").replace(",", "").replace("/", "_")
        )
        id_chunk = f"chunk_{id_prop}_{safe_id}"

        return {
            "id_chunk": id_chunk,
            "id_proposicao": id_prop,
            "tipo_norma": tipo_lei,
            "numero_norma": f"{num_lei}/{ano_lei}",
            "ano_norma": int(ano_lei) if str(ano_lei).isdigit() else None,
            "titulo_secao": contexto_secao,
            "identificador_normativo": identificador_dispositivo,
            "tipo_dispositivo": tipo_dispositivo,
            "artigo_pai_caput": artigo_caput,
            "paragrafo_pai_texto": paragrafo_texto,
            "texto_original": texto_original_limpo,
            "texto_enriquecido": texto_enriquecido,
        }

    def _chunk_preambulo(self, node: PreambuloNode, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Gera chunks enriquecidos a partir do preâmbulo."""
        partes = self._split_text_into_paragraphs(node.texto, max_chars=1200)
        chunks = []
        for idx, part in enumerate(partes, 1):
            ident = "Preâmbulo" if len(partes) == 1 else f"Preâmbulo, Parte {idx}"
            chunks.append(self._criar_bloco_enriquecido(
                metadata=metadata,
                contexto_secao="Preâmbulo",
                identificador_dispositivo=ident,
                tipo_dispositivo="Preâmbulo",
                artigo_caput="",
                paragrafo_texto=None,
                texto_original=part
            ))
        return chunks

    def _chunk_anexo(self, node: AnexoNode, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Gera chunks enriquecidos a partir de um Anexo."""
        partes = self._split_text_into_paragraphs(node.texto, max_chars=1200)
        chunks = []
        for idx, part in enumerate(partes, 1):
            ident = node.identificador if len(partes) == 1 else f"{node.identificador}, Parte {idx}"
            chunks.append(self._criar_bloco_enriquecido(
                metadata=metadata,
                contexto_secao="Anexo",
                identificador_dispositivo=ident,
                tipo_dispositivo="Anexo",
                artigo_caput="",
                paragrafo_texto=None,
                texto_original=part
            ))
        return chunks

    def _chunk_fecho(self, node: FechoNode, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Gera chunk do fecho (data, assinaturas). Normalmente curto."""
        return [self._criar_bloco_enriquecido(
            metadata=metadata,
            contexto_secao="Fecho",
            identificador_dispositivo="Fecho",
            tipo_dispositivo="Fecho",
            artigo_caput="",
            paragrafo_texto=None,
            texto_original=node.texto
        )]

    # ------------------------------------------------------------------
    # API pública principal
    # ------------------------------------------------------------------

    def chunk_document(self, text: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Gera os blocos normativos (chunks) a partir do texto e metadados da proposição.
        Retorna lista ordenada de dicionários de chunks prontos para vetorização.
        """
        nodes = self.parse_hierarchy(text)
        chunks: List[Dict[str, Any]] = []

        for node in nodes:
            if isinstance(node, PreambuloNode):
                # Só chunkifica o preâmbulo se tiver conteúdo substancial (> 50 chars)
                if len(node.texto) > 50:
                    chunks.extend(self._chunk_preambulo(node, metadata))

            elif isinstance(node, ArtigoNode):
                art = node

                # --- Chunk do Caput ---
                chunks.append(self._criar_bloco_enriquecido(
                    metadata=metadata,
                    contexto_secao=art.contexto_secao,
                    identificador_dispositivo=art.identificador,
                    tipo_dispositivo="Caput",
                    artigo_caput=art.caput,
                    paragrafo_texto=None,
                    texto_original=art.caput
                ))

                # --- Chunks dos Parágrafos ---
                for parag in art.paragrafos:
                    chunks.append(self._criar_bloco_enriquecido(
                        metadata=metadata,
                        contexto_secao=art.contexto_secao,
                        identificador_dispositivo=f"{art.identificador}, {parag.identificador}",
                        tipo_dispositivo="Parágrafo",
                        artigo_caput=art.caput,
                        paragrafo_texto=None,
                        texto_original=parag.texto
                    ))

                    # --- Incisos sob Parágrafo ---
                    for inc in parag.incisos:
                        chunks.append(self._criar_bloco_enriquecido(
                            metadata=metadata,
                            contexto_secao=art.contexto_secao,
                            identificador_dispositivo=f"{art.identificador}, {parag.identificador}, {inc.identificador}",
                            tipo_dispositivo="Inciso",
                            artigo_caput=art.caput,
                            paragrafo_texto=parag.texto,
                            texto_original=inc.texto
                        ))

                # --- Incisos diretamente sob o Artigo ---
                for inc in art.incisos:
                    chunks.append(self._criar_bloco_enriquecido(
                        metadata=metadata,
                        contexto_secao=art.contexto_secao,
                        identificador_dispositivo=f"{art.identificador}, {inc.identificador}",
                        tipo_dispositivo="Inciso",
                        artigo_caput=art.caput,
                        paragrafo_texto=None,
                        texto_original=inc.texto
                    ))

            elif isinstance(node, AnexoNode):
                if len(node.texto) > 30:
                    chunks.extend(self._chunk_anexo(node, metadata))

            elif isinstance(node, FechoNode):
                # Fecho tem pouco valor semântico, não vetoriza por padrão
                pass

        return chunks


# ---------------------------------------------------------------------------
# Self-test / Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import unittest

    pl_simulado_texto = """
PROJETO DE LEI Nº 2.456, DE 2026

Ementa: Regulamenta o desenvolvimento ético de sistemas de Inteligência Artificial.

O CONGRESSO NACIONAL decreta:

TÍTULO I
DAS DIRETRIZES FUNDAMENTAIS

CAPÍTULO I
DOS PRINCÍPIOS E OBJETIVOS

Art. 1º Este projeto de lei estabelece normas para o desenvolvimento ético de IA.

Art. 2º São princípios orientadores:
I - transparência e explicabilidade dos modelos;
II - justiça social e não discriminação.

CAPÍTULO II
DOS DEVERES DE GOVERNANÇA

Art. 3º Os fornecedores de sistemas de IA devem assegurar supervisão humana.
§ 1º A supervisão deve garantir a possibilidade de intervenção imediata.
§ 2º Para sistemas de alto risco, os desenvolvedores deverão:
I - realizar auditoria algorítmica independente anualmente;
II - manter documentação técnica detalhada.
Parágrafo único. O regulamento definirá quais sistemas são de alto risco.

Art. 4º Esta Lei entra em vigor na data de sua publicação.

Brasília, em 1º de janeiro de 2026.

FULANO DE TAL
Presidente da República
"""

    meta = {
        "id_proposicao": 987654,
        "tipo": "PL", "numero": "2.456", "ano": 2026,
        "ementa": "Regulamenta IA ética.", "autor": "Deputada Maria"
    }

    chunker = HierarchicalLegislativeChunker()
    chunks = chunker.chunk_document(pl_simulado_texto, meta)

    print(f"Total de chunks gerados: {len(chunks)}\n")
    for idx, c in enumerate(chunks, 1):
        print(f"--- CHUNK {idx} ({c['identificador_normativo']}) tipo={c['tipo_dispositivo']} ---")
        print(c['texto_enriquecido'][:300])
        print("-" * 60 + "\n")

    # --- Testes unitários ---
    class TestChunker(unittest.TestCase):
        def setUp(self):
            self.chunker = HierarchicalLegislativeChunker()
            self.chunks = self.chunker.chunk_document(pl_simulado_texto, meta)

        def test_contagem_minima_chunks(self):
            # Art1(Caput) + Art2(Caput+I+II) + Art3(Caput+§1+§2+§2I+§2II+PU) + Art4(Caput) = 11
            self.assertGreaterEqual(len(self.chunks), 10)

        def test_sem_ementa_nem_autor(self):
            for c in self.chunks:
                self.assertEqual(c["id_proposicao"], 987654)
                self.assertIn("PL 2.456/2026", c["texto_enriquecido"])
                self.assertNotIn("Maria", c["texto_enriquecido"])
                self.assertNotIn("Regulamenta IA ética", c["texto_enriquecido"])

        def test_caput_injetado_em_paragrafo(self):
            p1 = next(c for c in self.chunks if c["identificador_normativo"] == "Art. 3º, § 1º")
            self.assertIn("Caput do artigo: Art. 3º Os fornecedores", p1["texto_enriquecido"])

        def test_caput_e_paragrafo_injetados_em_inciso(self):
            inc = next(c for c in self.chunks if "Art. 3º, § 2º, Inciso I" == c["identificador_normativo"])
            self.assertIn("Caput do artigo:", inc["texto_enriquecido"])
            self.assertIn("Parágrafo: § 2º Para sistemas de alto risco", inc["texto_enriquecido"])

        def test_inciso_direto_sob_artigo(self):
            inc = next(c for c in self.chunks if "Art. 2º, Inciso I" == c["identificador_normativo"])
            self.assertIsNone(inc["paragrafo_pai_texto"])

    suite = unittest.TestLoader().loadTestsFromTestCase(TestChunker)
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)
