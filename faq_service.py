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

# 售后关键词
AFTER_SALES_KEYWORDS = {
    "退货", "退款", "换货", "退换货", "投诉", "损坏", "坏了", "质量问题",
    "瑕疵", "破损", "残次", "售后", "维修", "赔偿", "补发",
}

# 闲聊关键词
CHAT_PATTERNS = re.compile(
    r"^(你好|嗨|hello|hi|谢谢|感谢|再见|拜拜|晚安|早安|哈哈|嘻嘻|嘿|哦|嗯|好的|ok|可以|没事|算了|不了)$",
    re.IGNORECASE,
)

# 代词列表
PRONOUNS = {"它", "他", "她", "那个", "这个", "那", "这", "其"}

# 语气词
PARTICLES = re.compile(r"[啊呢吧嘛哦哈呀哎罢哇呗嗯噢嚯]+")

# 标点
PUNCTUATION = re.compile(r"[^\w\s一-鿿]")


def load_faq() -> list[dict]:
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def preprocess_query(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = PUNCTUATION.sub("", text)
    text = PARTICLES.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _has_negation(text: str, keyword: str) -> bool:
    idx = text.find(keyword)
    if idx <= 0:
        return False
    prefix = text[max(0, idx - 2) : idx]
    for neg in NEGATION_WORDS:
        if neg in prefix:
            return True
    return False


def _detect_negation_topic(user_input: str) -> str | None:
    for kw in AFTER_SALES_KEYWORDS:
        if kw in user_input and _has_negation(user_input, kw):
            return kw
    return None


def _is_after_sales(user_input: str) -> bool:
    for kw in AFTER_SALES_KEYWORDS:
        if kw in user_input:
            if _has_negation(user_input, kw):
                continue
            return True
    return False


def _detect_intent(user_input: str) -> str:
    """意图分类：pre_sale / after_sale / chat / transfer。
    after_sale 和 transfer 由上层处理，chat 直接走 AI。"""
    stripped = user_input.strip()
    if stripped in ("转人工", "人工客服", "找人工", "真人客服"):
        return "transfer"
    if _is_after_sales(stripped):
        return "after_sale"
    if CHAT_PATTERNS.match(stripped):
        return "chat"
    return "pre_sale"


def _levenshtein(s1: str, s2: str) -> int:
    """计算两个字符串的编辑距离。"""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if c1 == c2 else 1)))
        prev = curr
    return prev[-1]


def _keyword_match(user_input: str) -> str | None:
    faq_list = load_faq()
    hits: list[tuple[int, dict]] = []
    for faq in faq_list:
        matched = False
        match_words = faq.get("keywords", []) + faq.get("synonyms", [])
        for word in match_words:
            if word in user_input:
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
    faq_list = load_faq()
    user_pinyin = " ".join(lazy_pinyin(user_input))
    hits: list[tuple[int, dict]] = []
    for faq in faq_list:
        match_words = faq.get("keywords", []) + faq.get("synonyms", [])
        for word in match_words:
            word_pinyin = " ".join(lazy_pinyin(word))
            if word_pinyin in user_pinyin or user_pinyin in word_pinyin:
                if _has_negation(user_input, word):
                    continue
                hits.append((len(word), faq))
                break
    if hits:
        hits.sort(key=lambda x: x[0], reverse=True)
        return hits[0][1]["answer"]
    return None


def _edit_distance_match(user_input: str) -> str | None:
    """编辑距离匹配：对用户输入中 2-4 字片段与关键词做编辑距离计算，≤1 视为匹配。"""
    faq_list = load_faq()
    hits: list[tuple[int, dict]] = []
    # 提取用户输入中 2-4 字片段
    user_segments = set()
    for size in (2, 3, 4):
        for i in range(len(user_input) - size + 1):
            user_segments.add(user_input[i : i + size])

    for faq in faq_list:
        match_words = faq.get("keywords", []) + faq.get("synonyms", [])
        for word in match_words:
            if len(word) < 2:
                continue
            for seg in user_segments:
                if abs(len(seg) - len(word)) > 1:
                    continue
                if _levenshtein(seg, word) <= 1:
                    if _has_negation(user_input, word):
                        continue
                    hits.append((len(word), faq))
                    break
            if hits and hits[-1][1] == faq:
                break
    if hits:
        hits.sort(key=lambda x: x[0], reverse=True)
        return hits[0][1]["answer"]
    return None


def _resolve_pronouns(user_input: str, history: list[dict] | None) -> str:
    """指代消解：用上一轮用户消息中的名词替换代词。"""
    if not history:
        return user_input

    has_pronoun = any(p in user_input for p in PRONOUNS)
    if not has_pronoun:
        return user_input

    # 取上一轮用户消息作为上下文
    recent_user_msgs = [m["content"] for m in history if m["role"] == "user"]
    if not recent_user_msgs:
        return user_input

    last_msg = recent_user_msgs[-1]

    # 从上轮消息提取名词性片段（2-4字）
    nouns = set()
    for size in (4, 3, 2):
        for i in range(len(last_msg) - size + 1):
            segment = last_msg[i : i + size]
            if not any(p in segment for p in PRONOUNS | {"吗", "呢", "的", "了", "是", "在", "有", "和"}):
                nouns.add(segment)

    # 选择最长且在上轮消息中出现位置最靠后的名词
    best_noun = ""
    best_pos = -1
    for noun in nouns:
        pos = last_msg.rfind(noun)
        if pos >= best_pos and len(noun) > len(best_noun):
            best_noun = noun
            best_pos = pos

    if not best_noun:
        return user_input

    # 替换代词
    resolved = user_input
    for pronoun in sorted(PRONOUNS, key=len, reverse=True):
        if pronoun in resolved:
            resolved = resolved.replace(pronoun, best_noun, 1)
            break

    logger.info(f"指代消解: '{user_input}' → '{resolved}' (上下文: '{last_msg}')")
    return resolved


def _cross_validate(user_input: str, faq_question: str, score: float) -> bool:
    """向量交叉验证：检查用户输入是否包含该 FAQ 的关键词/同义词。
    高分命中（≥0.7）直接通过；低分命中需要至少一个关键词重叠。"""
    if score >= 0.7:
        return True

    faq_list = load_faq()
    for faq in faq_list:
        if faq["question"] == faq_question:
            match_words = faq.get("keywords", []) + faq.get("synonyms", [])
            for word in match_words:
                if word in user_input:
                    return True
            # 无关键词重叠，但 score 在阈值以上，降级通过
            return score >= 0.60

    return True


def match_faq(user_input: str, history: list[dict] | None = None) -> tuple[str | None, str]:
    """六级递进匹配：
    意图分类 → ① 关键词 → ② 拼音 → ③ 编辑距离 → ④ 向量(指代消解+交叉验证) → ⑤ AI
    返回 (answer, source)。source 为 None 表示交给 AI。"""
    processed = preprocess_query(user_input)
    if not processed:
        return None, None

    # 意图分类前置
    intent = _detect_intent(processed)
    if intent == "chat":
        return None, "chat"
    if intent == "transfer":
        return None, "transfer"

    # ① 关键词匹配
    answer = _keyword_match(processed)
    if answer:
        return answer, "keyword"

    # ② 拼音匹配
    answer = _pinyin_match(processed)
    if answer:
        return answer, "pinyin"

    # ③ 编辑距离匹配
    answer = _edit_distance_match(processed)
    if answer:
        return answer, "edit_distance"

    # ④ 向量匹配（指代消解 + 上下文增强 + 交叉验证）
    resolved = _resolve_pronouns(processed, history)
    query = resolved
    if history:
        recent_user_msgs = [m["content"] for m in history if m["role"] == "user"][-2:]
        if recent_user_msgs:
            query = " ".join(recent_user_msgs) + " " + query

    answer, score = vector_search(query)
    if answer:
        # 交叉验证：低分命中且无关键词重叠时标记为低置信度
        if _cross_validate(processed, answer.split("\n")[0][:20], score):
            logger.info(f"向量匹配命中, score={score:.3f}, Q={user_input[:20]}")
            return answer, "vector"
        else:
            logger.info(f"向量交叉验证降级, score={score:.3f}, Q={user_input[:20]}")
            return answer, "vector_low"

    logger.info(f"向量匹配未命中, score={score:.3f}, Q={user_input[:20]}")

    # ⑤ 无命中，交给 AI
    return None, None
