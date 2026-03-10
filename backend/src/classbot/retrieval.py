# classbot/retrieval.py
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from classbot.vectors import InMemoryVectorStore

logger = logging.getLogger("classbot.retrieval")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

# --- defaults por modo ---
MODE_DEFAULTS = {
    "grade": {"k": 3, "max_snippet_chars": 700},
    "tutor": {"k": 5, "max_snippet_chars": 900},
}

# --- stores por UC/lesson ---
_VSTORES: dict[str, InMemoryVectorStore] = {}
_RAW_DOCS_COUNT: dict[str, int] = {}
_STORE_PARAMS = {"max_tokens": 800, "overlap_tokens": 200, "use_hf_tokenizer": True}


def set_store_params(
    max_tokens: Optional[int] = None,
    overlap_tokens: Optional[int] = None,
    use_hf_tokenizer: Optional[bool] = None,
) -> None:
    if max_tokens is not None:
        _STORE_PARAMS["max_tokens"] = int(max_tokens)
    if overlap_tokens is not None:
        _STORE_PARAMS["overlap_tokens"] = int(overlap_tokens)
    if use_hf_tokenizer is not None:
        _STORE_PARAMS["use_hf_tokenizer"] = bool(use_hf_tokenizer)

    for s in _VSTORES.values():
        s.max_tokens = _STORE_PARAMS["max_tokens"]
        s.overlap_tokens = _STORE_PARAMS["overlap_tokens"]
        s.tokenizer = s.tokenizer if _STORE_PARAMS["use_hf_tokenizer"] else None


def _ensure_store(uc: str) -> None:
    if uc not in _VSTORES:
        _VSTORES[uc] = InMemoryVectorStore(
            max_tokens=_STORE_PARAMS["max_tokens"],
            overlap_tokens=_STORE_PARAMS["overlap_tokens"],
            use_hf_tokenizer=_STORE_PARAMS["use_hf_tokenizer"],
        )
        _RAW_DOCS_COUNT.setdefault(uc, 0)


def corpus_stats(uc: str) -> dict:
    s = _VSTORES.get(uc)
    num_chunks = len(s.docs) if s and s.docs else 0
    return {"num_docs": _RAW_DOCS_COUNT.get(uc, 0), "num_chunks": num_chunks}


def add_to_corpus(
    docs: List[str],
    *,
    max_tokens: Optional[int] = None,
    overlap_tokens: Optional[int] = None,
    use_hf_tokenizer: Optional[bool] = None,
    uc: str = "Global",
) -> None:
    docs = [d for d in (docs or []) if d and d.strip()]
    if not docs:
        return

    _ensure_store(uc)

    if any(v is not None for v in [max_tokens, overlap_tokens, use_hf_tokenizer]):
        set_store_params(
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            use_hf_tokenizer=use_hf_tokenizer,
        )

    t0 = time.time()
    _VSTORES[uc].upsert(docs)
    dt = round(time.time() - t0, 3)

    _RAW_DOCS_COUNT[uc] = _RAW_DOCS_COUNT.get(uc, 0) + len(docs)
    logger.info(
        "corpus_upsert | uc=%s | raw_docs_added=%d | total_raw_docs=%d | t_upsert=%.3fs",
        uc,
        len(docs),
        _RAW_DOCS_COUNT.get(uc, 0),
        dt,
    )


def _cap_snippet(text: str, max_chars: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    return t[:max_chars].rstrip() + "…"


def retrieve_snippets(
    query: str,
    *,
    uc: str = "Global",
    mode: str = "grade",
    k: Optional[int] = None,
    max_snippet_chars: Optional[int] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Devolve:
      - block: string com Fontes numeradas para injectar no prompt
      - sources: lista [{idx, score, text, num}]
    """
    s = _VSTORES.get(uc)
    if not s:
        kk = k or MODE_DEFAULTS.get(mode, MODE_DEFAULTS["grade"])["k"]
        logger.info("retrieval | uc=%s | mode=%s | k=%d | hits=0 | no_store=True", uc, mode, kk)
        return "", []

    defaults = MODE_DEFAULTS.get(mode, MODE_DEFAULTS["grade"])
    kk = int(k if k is not None else defaults["k"])
    msc = int(max_snippet_chars if max_snippet_chars is not None else defaults["max_snippet_chars"])

    t0 = time.time()
    results = s.search(query, k=kk)
    t_ret = round(time.time() - t0, 4)

    if not results:
        logger.info("retrieval | uc=%s | mode=%s | k=%d | hits=0 | t_ret=%.4fs", uc, mode, kk, t_ret)
        return "", []

    sources: List[Dict[str, Any]] = []
    for i, (idx, score, text) in enumerate(results, start=1):
        sources.append(
            {
                "idx": int(idx),
                "score": float(score),
                "text": _cap_snippet(str(text), msc),
                "num": i,
            }
        )

    numbered = "\n".join(f"[{src['num']}] {src['text']}" for src in sources)
    block = f"\n\nFontes numeradas (use apenas se necessário):\n{numbered}"

    logger.info(
        "retrieval | uc=%s | mode=%s | k=%d | hits=%d | t_ret=%.4fs | query_chars=%d | block_chars=%d | max_snip_chars=%d",
        uc,
        mode,
        kk,
        len(sources),
        t_ret,
        len(query or ""),
        len(block or ""),
        msc,
    )
    return block, sources