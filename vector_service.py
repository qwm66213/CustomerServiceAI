import json
import logging
import os
from pathlib import Path

import faiss
import numpy as np

logger = logging.getLogger(__name__)

FAQ_PATH = Path(__file__).parent / "faq.json"
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "shibing624/text2vec-base-chinese")
SIMILARITY_THRESHOLD = 0.55  # 低于此置信度视为未命中

_index: faiss.IndexFlatIP | None = None
_faq_answers: list[str] = []
_faq_questions: list[str] = []
_model = None


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


def build_index():
    """启动时调用：将 FAQ 全量向量化并构建 FAISS 索引。"""
    global _index, _faq_answers, _faq_questions

    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        faq_list = json.load(f)

    _faq_questions = [faq["question"] for faq in faq_list]
    _faq_answers = [faq["answer"] for faq in faq_list]

    # 用 question + keywords 拼接作为表示文本，增加语义覆盖
    corpus = []
    for faq in faq_list:
        parts = [faq["question"]]
        parts.extend(faq.get("keywords", []))
        corpus.append(" ".join(parts))

    vectors = _encode(corpus)

    dim = vectors.shape[1]
    _index = faiss.IndexFlatIP(dim)  # 内积 = 余弦相似度（向量已归一化）
    _index.add(vectors)

    logger.info(f"FAQ 向量索引构建完成: {len(_faq_answers)} 条, 维度={dim}")


def vector_search(query: str) -> tuple[str | None, float]:
    """对用户输入做向量检索，返回 (answer, score)。
    未命中返回 (None, score)。"""
    if _index is None:
        build_index()

    q_vec = _encode([query])
    scores, indices = _index.search(q_vec, k=1)

    score = float(scores[0][0])
    idx = int(indices[0][0])

    if score < SIMILARITY_THRESHOLD or idx < 0:
        return None, score

    return _faq_answers[idx], score
