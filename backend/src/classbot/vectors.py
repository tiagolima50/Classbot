from typing import List, Tuple, Optional
import numpy as np
import ollama
import hashlib
import re

# --- Embeddings ---
EMBED_MODEL = "nomic-embed-text"  # ou outro disponível no teu Ollama

# --- Tokenização (Hugging Face) ---
# Mantemos a compatibilidade com um tokenizer HF (p.ex. LLaMA) para contagem “real” de tokens
TOKENIZER_MODEL = "meta-llama/Llama-3.1-8B"  # ajusta se preferires outro
MAX_CHUNK_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 200

# Carrega o tokenizer do HF (graceful fallback caso falhe)
_TOKENIZER = None
try:
    from transformers import AutoTokenizer  # type: ignore
    _TOKENIZER = AutoTokenizer.from_pretrained(TOKENIZER_MODEL, use_fast=True)
except Exception:
    # Sem transformers / modelo indisponível → seguimos com heurística aproximada
    _TOKENIZER = None

# --- cache simples em memória (texto -> vetor) ---
_EMB_CACHE: dict[str, np.ndarray] = {}

def _key(txt: str) -> str:
    # hash estável para reutilizar embeddings do mesmo texto
    return hashlib.sha256(txt.encode("utf-8")).hexdigest()

def embed(texts: List[str]) -> np.ndarray:
    """Devolve matriz (n, d) de embeddings para uma lista de textos, com cache."""
    vecs: List[np.ndarray] = []
    missing: list[tuple[str, str]] = []  # (key, text)
    order: List[str] = []
    for t in texts:
        k = _key(t)
        order.append(k)
        if k not in _EMB_CACHE:
            missing.append((k, t))
    # calcula apenas os que faltam
    for k, t in missing:
        r = ollama.embeddings(model=EMBED_MODEL, prompt=t)
        _EMB_CACHE[k] = np.array(r["embedding"], dtype=np.float32)
    # recompõe na ordem pedida
    for k in order:
        vecs.append(_EMB_CACHE[k])
    return np.vstack(vecs)

# =========================
#   Chunking “smart”
# =========================

def approx_tokens_nomic(text: str) -> int:
    """
    Heurística de tokens para o embedder (nomic-embed-text):
    ~4 chars por token costuma ser uma aproximação robusta para PT/EN.
    """
    return max(1, int(len(text) / 4))

def _split_sentences(text: str) -> List[str]:
    """
    Divide em frases mantendo pontuação. Junta frases muito curtas para evitar
    pedaços micro. Funciona bem para prosa técnica/documentação.
    """
    text = (text or "").strip()
    if not text:
        return []
    # separação básica por fim de frase
    parts = re.split(r'(?<=[\.\!\?])\s+', text)
    parts = [p.strip() for p in parts if p and p.strip()]

    merged: List[str] = []
    buf: List[str] = []
    for p in parts:
        buf.append(p)
        # Junta frases curtinhas até ~80 chars antes de “fechar”
        if len(" ".join(buf)) > 80:
            merged.append(" ".join(buf))
            buf = []
    if buf:
        merged.append(" ".join(buf))
    # quebra por parágrafos também ajuda (quando há \n\n)
    final: List[str] = []
    for m in merged:
        # mantém parágrafos como unidades preferidas
        ps = [s.strip() for s in re.split(r'\n{2,}', m) if s.strip()]
        final.extend(ps)
    return final if final else parts

def _count_tokens(text: str, tokenizer=None) -> int:
    if tokenizer is not None:
        # contagem “real” via tokenizer HF
        return len(tokenizer.encode(text, add_special_tokens=False))
    # fallback heurístico
    return approx_tokens_nomic(text)

def chunk_text_smart(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
    *,
    tokenizer=None
) -> List[str]:
    """
    Chunking por frases/parágrafos respeitando orçamento de tokens.
    Usa tokenizer HF se disponível; caso contrário, heurística ~chars/4.
    Garante overlap por frases (não corta a meio de frase).
    """
    text = (text or "").strip()
    if not text:
        return []

    sents = _split_sentences(text)
    if not sents:
        return []

    chunks: List[str] = []
    cur: List[str] = []
    cur_tokens = 0

    def tok(s: str) -> int:
        return _count_tokens(s, tokenizer=tokenizer)

    for s in sents:
        stoks = tok(s)
        # se a frase sozinha já estoura o orçamento, fazemos um “hard wrap” por tokens
        if stoks > max_tokens:
            # quebra dentro da frase usando tokenizer se houver, senão por tamanho
            if tokenizer is not None:
                ids = tokenizer.encode(s, add_special_tokens=False)
                start = 0
                while start < len(ids):
                    end = min(start + max_tokens, len(ids))
                    piece_ids = ids[start:end]
                    chunk = tokenizer.decode(piece_ids).strip()
                    if chunk:
                        # se houver contexto acumulado, fecha-o antes
                        if cur:
                            chunks.append(" ".join(cur).strip())
                            cur, cur_tokens = [], 0
                        chunks.append(chunk)
                    if end >= len(ids):
                        break
                    start = max(end - overlap_tokens, end)
            else:
                # fallback por comprimento (aprox 4 chars/token)
                width = max(1, max_tokens * 4)
                step = max(1, max(1, (max_tokens - overlap_tokens) * 4))
                if cur:
                    chunks.append(" ".join(cur).strip())
                    cur, cur_tokens = [], 0
                for i in range(0, len(s), step):
                    piece = s[i:i+width].strip()
                    if piece:
                        chunks.append(piece)
            continue

        # cabe no chunk atual?
        if cur_tokens + stoks <= max_tokens or not cur:
            cur.append(s)
            cur_tokens += stoks
        else:
            # fecha chunk atual
            chunk = " ".join(cur).strip()
            if chunk:
                chunks.append(chunk)

            # prepara overlap: reaproveita frases finais até cobrir ~overlap_tokens
            overlap: List[str] = []
            t = 0
            for sent in reversed(cur):
                t_sent = tok(sent)
                if t + t_sent > overlap_tokens and overlap:
                    break
                overlap.insert(0, sent)
                t += t_sent

            cur = overlap + [s]
            cur_tokens = tok(" ".join(cur))

    if cur:
        chunk = " ".join(cur).strip()
        if chunk:
            chunks.append(chunk)

    # dedupe leve preservando ordem
    uniq: List[str] = []
    seen: set[str] = set()
    for ch in chunks:
        if ch and ch not in seen:
            uniq.append(ch)
            seen.add(ch)
    return uniq

# --------- Chunking por tokens (HF) “legacy” ---------
def _chunk_text_by_tokens(
    text: str,
    max_tokens: int = MAX_CHUNK_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
) -> List[str]:
    """
    Versão antiga: janelas fixas de tokens usando tokenizer HF (se houver).
    Mantida como utilitário; o fluxo principal usa chunk_text_smart.
    """
    text = (text or "").strip()
    if not text:
        return []

    if _TOKENIZER is None:
        # sem tokenizer → delega para o smart (heurístico)
        return chunk_text_smart(text, max_tokens, overlap_tokens, tokenizer=None)

    ids = _TOKENIZER.encode(text, add_special_tokens=False)
    n = len(ids)
    if n == 0:
        return []

    chunks: List[str] = []
    start = 0
    while start < n:
        end = min(start + max_tokens, n)
        piece_ids = ids[start:end]
        chunk = _TOKENIZER.decode(piece_ids).strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap_tokens, end)
    return chunks

# --------- Vector Store em memória ---------
class InMemoryVectorStore:
    def __init__(
        self,
        *,
        max_tokens: int = MAX_CHUNK_TOKENS,
        overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
        use_hf_tokenizer: bool = True
    ):
        """
        max_tokens / overlap_tokens: orçamento aproximado de tokens por chunk.
        use_hf_tokenizer: se True e disponível, usa tokenizer HF para contagem “real”.
        """
        self.docs: List[str] = []             # cada item é um CHUNK de texto
        self.mat: np.ndarray | None = None    # shape (n_chunks, dim)
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.tokenizer = _TOKENIZER if (use_hf_tokenizer and _TOKENIZER is not None) else None

    def _chunk(self, d: str) -> List[str]:
        # chunking por frases + orçamento; usa tokenizer se existir
        return chunk_text_smart(
            d,
            self.max_tokens,
            self.overlap_tokens,
            tokenizer=self.tokenizer
        )

    def upsert(self, new_docs: List[str]) -> None:
        """
        Recebe documentos “longos”, aplica chunking smart e indexa cada chunk.
        Faz deduplicação simples por conteúdo. Se o embedder falhar por excesso,
        aplica backoff (rechunk com orçamento 0.8x) e tenta novamente.
        """
        # deduplicar mantendo ordem (a nível de CHUNK)
        seen: set[str] = set(self.docs)
        all_chunks: List[str] = []
        for d in new_docs or []:
            if not d or not d.strip():
                continue
            for ch in self._chunk(d):
                if ch not in seen:
                    all_chunks.append(ch)
                    seen.add(ch)

        if not all_chunks:
            return

        # tentar embed; se falhar, fazer backoff e re-chunk
        def _embed_with_backoff(chunks: List[str]) -> np.ndarray:
            try:
                return embed(chunks)
            except Exception:
                # backoff: reduzir orçamento e re-chunk
                shrink = 0.8
                tmp = InMemoryVectorStore(
                    max_tokens=max(100, int(self.max_tokens * shrink)),
                    overlap_tokens=max(0, int(self.overlap_tokens * shrink)),
                    use_hf_tokenizer=self.tokenizer is not None
                )
                rechunked: List[str] = []
                for c in chunks:
                    rechunked.extend(tmp._chunk(c))
                return embed(rechunked)

        mat_new = _embed_with_backoff(all_chunks)

        self.docs.extend(all_chunks)
        self.mat = mat_new if self.mat is None else np.vstack([self.mat, mat_new])

    def search(self, query: str, k: int = 5) -> List[Tuple[int, float, str]]:
        """Retorna top-k (idx, score, texto) por similaridade cosseno (sobre CHUNKS)."""
        if self.mat is None or len(self.docs) == 0:
            return []
        q = embed([query])[0]  # shape (d,)
        A = self.mat
        qn = q / (np.linalg.norm(q) + 1e-12)
        An = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-12)
        sims = An @ qn
        idx = np.argsort(-sims)[:k]
        return [(int(i), float(sims[i]), self.docs[int(i)]) for i in idx]
