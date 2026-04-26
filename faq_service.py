import json
import logging
import re
import unicodedata
from pathlib import Path

from pypinyin import lazy_pinyin

from vector_service import vector_search

logger = logging.getLogger(__name__)

FAQ_PATH = Path(__file__).parent / "faq.json"

# 否定词列表
NEGATION_WORDS = {"不", "别", "没", "无需", "不用", "不要", "不再", "无法", "不是", "没有", "并非"}

# 语气词列表（查询预处理时去除）
PARTICLES = re.compile(r"[啊呢吧嘛哦哈呀哎罢么哇呗嗯噢嚯]+")

# 标点符号（中文+英文）
PUNCTUATION = re.compile(r"[^\w\s一-鿿]")


def load_faq() -> list[dict]:
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def preprocess_query(text: str) -> str:
    """查询预处理：全角→半角、去标点、去语气词、去多余空格。"""
    # 全角转半角
    text = unicodedata.normalize("NFKC", text)
    # 去标点
    text = PUNCTUATION.sub("", text)
    # 去语气词
    text = PARTICLES.sub("", text)
    # 去多余空格
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _has_negation(text: str, keyword: str) -> bool:
    """检测关键词前是否有否定词。"""
    idx = text.find(keyword)
    if idx <= 0:
        return False
    # 检查关键词前1-2个字是否为否定词
    prefix = text[max(0, idx - 2) : idx]
    for neg in NEGATION_WORDS:
        if neg in prefix:
            return True
    return False


def _keyword_match(user_input: str) -> str | None:
    """第一级：精确关键词匹配（含同义词），带否定词检测。"""
    faq_list = load_faq()
    hits: list[tuple[int, dict]] = []  # (匹配词长度, faq)
    for faq in faq_list:
        matched = False
        # keywords + synonyms 统一参与匹配
        match_words = faq.get("keywords", []) + faq.get("synonyms", [])
        for word in match_words:
            if word in user_input:
                # 否定词检测
                if _has_negation(user_input, word):
                    continue
                hits.append((len(word), faq))
                matched = True
                break
        if matched:
            continue
    if hits:
        hits.sort(key=lambda x: x[0], reverse=True)
        return hits[0][1]["answer"]
    return None


def _pinyin_match(user_input: str) -> str | None:
    """拼音匹配：将用户输入和关键词都转拼音，做拼音级精确匹配，容忍错别字。"""
    faq_list = load_faq()
    user_pinyin = " ".join(lazy_pinyin(user_input))
    hits: list[tuple[int, dict]] = []
    for faq in faq_list:
        match_words = faq.get("keywords", []) + faq.get("synonyms", [])
        for word in match_words:
            word_pinyin = " ".join(lazy_pinyin(word))
            if word_pinyin in user_pinyin or user_pinyin in word_pinyin:
                # 否定词也要在拼音层检测
                if _has_negation(user_input, word):
                    continue
                hits.append((len(word), faq))
                break
    if hits:
        hits.sort(key=lambda x: x[0], reverse=True)
        return hits[0][1]["answer"]
    return None


def match_faq(user_input: str, history: list[dict] | None = None) -> tuple[str | None, str]:
    """四级递进匹配：
    ① 关键词匹配（含同义词 + 否定词检测）
    ② 拼音匹配（错别字容忍）
    ③ 向量匹配（top-K，上下文增强）
    ④ 返回 None（交给 AI 兜底）
    返回 (answer, source)。
    """
    # 查询预处理
    processed = preprocess_query(user_input)
    if not processed:
        return None, None

    # ① 关键词匹配
    answer = _keyword_match(processed)
    if answer:
        return answer, "keyword"

    # ② 拼音匹配
    answer = _pinyin_match(processed)
    if answer:
        return answer, "pinyin"

    # ③ 向量匹配（上下文增强）
    query = processed
    if history:
        # 拼接最近1-2轮用户消息作为上下文
        recent_user_msgs = [m["content"] for m in history if m["role"] == "user"][-2:]
        if recent_user_msgs:
            query = " ".join(recent_user_msgs) + " " + query

    answer, score = vector_search(query)
    if answer:
        logger.info(f"向量匹配命中, score={score:.3f}, Q={user_input[:20]}")
        return answer, "vector"
    logger.info(f"向量匹配未命中, score={score:.3f}, Q={user_input[:20]}")

    # ④ 无命中，交给 AI
    return None, None
