import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai_service import ask_ai
from faq_service import match_faq
from vector_service import build_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

sessions: dict[str, list[dict]] = defaultdict(list)
MAX_HISTORY = 10

HUMAN_FALLBACK = (
    "抱歉，AI 服务暂时不可用，请拨打售后电话 400-888-0000 "
    "或输入「转人工」联系人工客服。"
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


@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "").strip()
    session_id = body.get("session_id", "default")

    if not message:
        return {"reply": "请输入您的问题。", "source": "system", "session_id": session_id}

    start = time.time()

    # ① 关键词(含同义词) → ② 拼音 → ③ 向量 → ④ AI 兜底
    history = sessions[session_id][-MAX_HISTORY:]
    answer, source = match_faq(message, history=history)

    if answer is None:
        answer = ask_ai(message, history=history)
        answer = ask_ai(message, history=history)
        source = "ai"

        if answer is None:
            answer = HUMAN_FALLBACK
            source = "fallback"

    elapsed = time.time() - start
    logger.info(f"[{source}] Q: {message} | A: {answer[:30]}... | {elapsed:.2f}s")

    sessions[session_id].append({"role": "user", "content": message})
    sessions[session_id].append({"role": "assistant", "content": answer})
    if len(sessions[session_id]) > MAX_HISTORY * 2:
        sessions[session_id] = sessions[session_id][-MAX_HISTORY * 2:]

    return {"reply": answer, "source": source, "session_id": session_id}
