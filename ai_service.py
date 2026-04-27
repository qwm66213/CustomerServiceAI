import json
import logging
import os
import time

from openai import OpenAI

from faq_service import load_faq, _is_after_sales, _detect_negation_topic

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = (
    "你是一个电商平台的**售前**客服助手，主要帮助用户了解商品信息、促销活动、下单流程、发货物流等售前问题。"
    "请用礼貌、简洁的中文回答用户的问题。\n\n"
    "重要规则：\n"
    "1. 如果用户提出售后复杂问题（退货退款、质量投诉、维修赔偿等），请先提供基本指引，"
    "然后主动引导用户联系人工客服处理：\"如需进一步处理，请输入「转人工」或拨打 400-888-0000，人工客服会为您妥善解决。\"\n"
    "2. 如果用户表达了否定意愿（如\"不退货\"\"不想退款\"），请确认用户的真实需求，不要主动提供退货退款操作指引。\n"
    "3. 如果问题超出电商客服范围，请礼貌地说明你只能回答购物相关问题。"
)

MAX_RETRIES = 2
RETRY_DELAY = 1
AI_TIMEOUT = 15


def _build_system_prompt(negation_topic: str | None = None) -> str:
    """将 FAQ 知识库注入 system prompt，让 AI 回答与已有政策一致。"""
    faq_list = load_faq()
    faq_lines = []
    for faq in faq_list:
        faq_lines.append(f"Q: {faq['question']} A: {faq['answer']}")
    faq_block = "\n".join(faq_lines)

    prompt = (
        f"{BASE_SYSTEM_PROMPT}\n\n"
        "以下是平台常见问题及标准回答，请优先参考这些内容，确保回答一致：\n"
        f"{faq_block}\n\n"
        "如果用户的问题在以上列表中有对应条目，请基于该回答进行回复，不要编造不同政策。"
    )

    if negation_topic:
        prompt += (
            f"\n\n注意：用户输入中检测到对「{negation_topic}」的否定表达，"
            "请确认用户的真实意图，不要主动提供该话题的操作指引。"
        )

    return prompt


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY 环境变量未设置")
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL"),
        timeout=AI_TIMEOUT,
    )


def ask_ai(question: str, history: list[dict] | None = None) -> str:
    # 检测否定话题
    negation_topic = _detect_negation_topic(question)

    messages = [{"role": "system", "content": _build_system_prompt(negation_topic)}]

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": question})

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client = _get_client()
            response = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
                messages=messages,
                max_tokens=512,
                temperature=0.7,
            )
            answer = response.choices[0].message.content

            # 售后复杂问题追加转人工引导
            if _is_after_sales(question):
                transfer_hint = "\n\n如需进一步处理，请输入「转人工」或拨打 400-888-0000，人工客服会为您妥善解决。"
                if transfer_hint.strip() not in answer:
                    answer += transfer_hint

            return answer
        except Exception as e:
            last_error = e
            logger.warning(f"AI 调用失败 (第{attempt}次): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    logger.error(f"AI 调用全部失败: {last_error}")
    return None
