import logging
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai_service import ask_ai
from faq_service import match_faq, _is_after_sales
from vector_service import build_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

sessions: dict[str, list[dict]] = defaultdict(list)
MAX_HISTORY = 10

# 匹配统计
match_stats = Counter()  # source -> count
query_log: list[dict] = []  # 最近查询记录
MAX_LOG = 1000

HUMAN_TRANSFER = (
    "您的问题已超出售前客服范围，建议联系人工客服获得专业帮助。\n"
    "请输入「转人工」或拨打售后电话 400-888-0000，工作时间 9:00-21:00。"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("电商AI客服系统启动，正在构建 FAQ 向量索引...")
    build_index()
    logger.info("系统就绪")
    yield
    logger.info("电商AI客服系统关闭")


app = FastAPI(title="电商AI客服", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/stats")
async def stats():
    """匹配统计接口，供运营查看各层级命中率和热点问题。"""
    return {
        "match_counts": dict(match_stats),
        "recent_queries": query_log[-50:],
    }


@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "").strip()
    session_id = body.get("session_id", "default")

    if not message:
        return {"reply": "请输入您的问题。", "source": "system", "session_id": session_id}

    start = time.time()

    # 检测"转人工"直接转接
    if message in ("转人工", "人工客服", "找人工", "真人客服"):
        answer = HUMAN_TRANSFER
        source = "transfer"
    else:
        # ① 关键词(含同义词) → ② 拼音 → ③ 向量 → ④ AI 兜底
        history = sessions[session_id][-MAX_HISTORY:]
        answer, source = match_faq(message, history=history)

        if answer is None:
            answer = ask_ai(message, history=history)
            source = "ai"

            if answer is None:
                answer = (
                    "抱歉，AI 服务暂时不可用，请输入「转人工」"
                    "或拨打售后电话 400-888-0000 联系人工客服。"
                )
                source = "fallback"

        # 售后复杂问题：FAQ 已回答的追加转人工引导，未命中 FAQ 的已在 AI 层处理
        if source in ("keyword", "pinyin", "vector") and _is_after_sales(message):
            transfer_hint = "\n\n如需进一步处理，请输入「转人工」或拨打 400-888-0000，人工客服会为您妥善解决。"
            if transfer_hint.strip() not in answer:
                answer += transfer_hint

    elapsed = time.time() - start

    # 统计
    match_stats[source] += 1
    query_log.append({
        "query": message[:50],
        "source": source,
        "elapsed": round(elapsed, 2),
    })
    if len(query_log) > MAX_LOG:
        query_log[:] = query_log[-MAX_LOG:]

    logger.info(f"[{source}] Q: {message} | A: {answer[:30]}... | {elapsed:.2f}s")

    sessions[session_id].append({"role": "user", "content": message})
    sessions[session_id].append({"role": "assistant", "content": answer})
    if len(sessions[session_id]) > MAX_HISTORY * 2:
        sessions[session_id] = sessions[session_id][-MAX_HISTORY * 2:]

    return {"reply": answer, "source": source, "session_id": session_id}
