import json
import logging
import os
from pathlib import Path

import faiss
import numpy as np

logger = logging.getLogger(__name__)

FAQ_PATH = Path(__file__).parent / "faq.json"
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "shibing624/text2vec-base-chinese")
SIMILARITY_THRESHOLD = 0.55

_index: faiss.IndexFlatIP | None = None
_faq_answers: list[str] = []
_faq_questions: list[str] = []
_faq_categories: list[str] = []  # pre-sale / after-sale
_model = None
_last_mtime: float = 0  # 用于热更新检测


def _load_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info(f"加载 embedding 模型: {MODEL_NAME}")
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("embedding 模型加载完成")
    return _model


def _encode(texts: list[str]) -> np.ndarray:
    model = _load_model()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.array(vectors, dtype="float32")


def _check_and_rebuild():
    """检测 faq.json 是否变更，自动重建索引。"""
    global _last_mtime
    try:
        mtime = FAQ_PATH.stat().st_mtime
    except FileNotFoundError:
        return
    if mtime != _last_mtime:
        logger.info("faq.json 已变更，重建向量索引...")
        build_index()


# 售后关键词集合（与 faq_service 保持一致）
_AFTER_SALES_KEYWORDS = {
    "退货", "退款", "换货", "退换货", "投诉", "损坏", "坏了", "质量问题",
    "瑕疵", "破损", "残次", "售后", "维修", "赔偿", "补发",
}


def _classify_faq(faq: dict) -> str:
    """判断 FAQ 是售前还是售后类型。"""
    text = faq["question"] + " ".join(faq.get("keywords", [])) + " ".join(faq.get("synonyms", []))
    for kw in _AFTER_SALES_KEYWORDS:
        if kw in text:
            return "after-sale"
    return "pre-sale"


def build_index():
    """将 FAQ 全量向量化并构建 FAISS 索引。"""
    global _index, _faq_answers, _faq_questions, _faq_categories, _last_mtime

    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        faq_list = json.load(f)

    _last_mtime = FAQ_PATH.stat().st_mtime
    _faq_questions = [faq["question"] for faq in faq_list]
    _faq_answers = [faq["answer"] for faq in faq_list]
    _faq_categories = [_classify_faq(faq) for faq in faq_list]

    # 只用 question 做向量表示，减少关键词拼接带来的噪声
    corpus = [faq["question"] for faq in faq_list]

    vectors = _encode(corpus)

    dim = vectors.shape[1]
    _index = faiss.IndexFlatIP(dim)
    _index.add(vectors)

    pre_count = sum(1 for c in _faq_categories if c == "pre-sale")
    after_count = sum(1 for c in _faq_categories if c == "after-sale")
    logger.info(f"FAQ 向量索引构建完成: {len(_faq_answers)} 条(售前{pre_count}/售后{after_count}), 维度={dim}")


TOP_K = 3
MULTI_HIT_MIN_SCORE = 0.50


def vector_search(query: str) -> tuple[str | None, float]:
    """对用户输入做向量检索，返回 (answer, score)。
    支持 top-K 多条合并：如果多条 FAQ 都超阈值且不同，拼接回答。
    未命中返回 (None, score)。"""
    if _index is None:
        build_index()

    _check_and_rebuild()

    q_vec = _encode([query])
    scores, indices = _index.search(q_vec, k=TOP_K)

    top_score = float(scores[0][0])
    top_idx = int(indices[0][0])

    if top_score < SIMILARITY_THRESHOLD or top_idx < 0:
        return None, top_score

    answers = [_faq_answers[top_idx]]
    seen = {top_idx}
    for i in range(1, TOP_K):
        s = float(scores[0][i])
        idx = int(indices[0][i])
        if s >= MULTI_HIT_MIN_SCORE and idx >= 0 and idx not in seen:
            answers.append(_faq_answers[idx])
            seen.add(idx)

    if len(answers) > 1:
        combined = "\n\n---\n\n".join(answers)
        return combined, top_score

    return answers[0], top_score
