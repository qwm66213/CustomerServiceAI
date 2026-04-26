import json
import logging
from pathlib import Path

from vector_service import vector_search

logger = logging.getLogger(__name__)

FAQ_PATH = Path(__file__).parent / "faq.json"


def load_faq() -> list[dict]:
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _keyword_match(user_input: str) -> str | None:
    """第一级：精确关键词匹配（最快）。"""
    faq_list = load_faq()
    hits: list[tuple[int, dict]] = []  # (关键词长度, faq)
    for faq in faq_list:
        for keyword in faq.get("keywords", []):
            if keyword in user_input:
                hits.append((len(keyword), faq))
                break
    if hits:
        hits.sort(key=lambda x: x[0], reverse=True)
        return hits[0][1]["answer"]
    return None


def match_faq(user_input: str) -> tuple[str | None, str]:
    """三级递进匹配：
    ① 关键词匹配 → ② 向量匹配 → ③ 返回 None（交给 AI 兜底）
    返回 (answer, source)，source 为 "keyword" / "vector" / None。
    """
    # ① 关键词
    answer = _keyword_match(user_input)
    if answer:
        return answer, "keyword"

    # ② 向量
    answer, score = vector_search(user_input)
    if answer:
        logger.info(f"向量匹配命中, score={score:.3f}, Q={user_input[:20]}")
        return answer, "vector"
    logger.info(f"向量匹配未命中, score={score:.3f}, Q={user_input[:20]}")

    # ③ 无命中，交给 AI
    return None, None
