import json
import logging
import os
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai_service import ask_ai
from auth import create_token, decode_token, hash_password, verify_password
from database import get_db, init_db
from faq_service import load_faq, match_faq, _is_after_sales
from session_store import append_message, get_messages
from vector_service import build_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

FAQ_PATH = Path(__file__).parent / "faq.json"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

MAX_HISTORY = 10

match_stats = Counter()
query_log: list[dict] = []
MAX_LOG = 1000

HUMAN_TRANSFER = (
    "您的问题已超出售前客服范围，建议联系人工客服获得专业帮助。\n"
    "请输入「转人工」或拨打售后电话 400-888-0000，工作时间 9:00-21:00。"
)


def sync_faq_json():
    """将 DB 中的 FAQ 数据写回 faq.json，再重建向量索引。"""
    db = get_db()
    rows = db.execute("SELECT question, keywords, synonyms, answer FROM faq ORDER BY id").fetchall()
    db.close()
    faq_list = [
        {
            "question": r["question"],
            "keywords": json.loads(r["keywords"]),
            "synonyms": json.loads(r["synonyms"]),
            "answer": r["answer"],
        }
        for r in rows
    ]
    with open(FAQ_PATH, "w", encoding="utf-8") as f:
        json.dump(faq_list, f, ensure_ascii=False, indent=2)
    build_index()
    logger.info(f"faq.json 已同步 ({len(faq_list)} 条)，向量索引已重建")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("电商AI客服系统启动...")
    init_db()
    build_index()
    logger.info("系统就绪")
    yield
    logger.info("电商AI客服系统关闭")


app = FastAPI(title="电商AI客服", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ============ 页面 ============

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/admin")
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


# ============ 用户认证 ============

@app.post("/auth/register")
async def register(request: Request):
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password or len(password) < 6:
        return JSONResponse({"ok": False, "error": "用户名不能为空，密码至少6位"}, 400)
    hashed_pw, salt = hash_password(password)
    db = get_db()
    try:
        db.execute("INSERT INTO users (username, hashed_pw, salt) VALUES (?,?,?)",
                   (username, hashed_pw, salt))
        db.commit()
        user_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    except Exception:
        db.close()
        return JSONResponse({"ok": False, "error": "用户名已存在"}, 400)
    db.close()
    token = create_token(user_id, username)
    return {"ok": True, "token": token, "username": username}


@app.post("/auth/login")
async def login(request: Request):
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    db = get_db()
    row = db.execute("SELECT id, hashed_pw, salt FROM users WHERE username=?", (username,)).fetchone()
    db.close()
    if not row or not verify_password(password, row["hashed_pw"], row["salt"]):
        return JSONResponse({"ok": False, "error": "用户名或密码错误"}, 401)
    token = create_token(row["id"], username)
    return {"ok": True, "token": token, "username": username}


# ============ 聊天 ============

@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "").strip()
    session_id = body.get("session_id", "default")

    # 可选用户认证
    user = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        user = decode_token(auth_header[7:])
    user_id = user["user_id"] if user else None

    if not message:
        return {"reply": "请输入您的问题。", "source": "system", "session_id": session_id}

    start = time.time()

    if message in ("转人工", "人工客服", "找人工", "真人客服"):
        answer = HUMAN_TRANSFER
        source = "transfer"
    else:
        history = get_messages(session_id)[-MAX_HISTORY:]
        answer, source = match_faq(message, history=history)

        # 闲聊意图直接走 AI
        if source == "chat":
            answer = None
        # 转人工意图
        if source == "transfer":
            answer = HUMAN_TRANSFER

        if answer is None and source != "transfer":
            answer = ask_ai(message, history=history)
            source = "ai"

            if answer is None:
                answer = (
                    "抱歉，AI 服务暂时不可用，请输入「转人工」"
                    "或拨打售后电话 400-888-0000 联系人工客服。"
                )
                source = "fallback"

        if source in ("keyword", "pinyin", "edit_distance", "vector", "vector_low") and _is_after_sales(message):
            transfer_hint = "\n\n如需进一步处理，请输入「转人工」或拨打 400-888-0000，人工客服会为您妥善解决。"
            if transfer_hint.strip() not in answer:
                answer += transfer_hint

    elapsed = time.time() - start

    match_stats[source] += 1
    query_log.append({"query": message[:50], "source": source, "elapsed": round(elapsed, 2)})
    if len(query_log) > MAX_LOG:
        query_log[:] = query_log[-MAX_LOG:]

    logger.info(f"[{source}] Q: {message} | A: {answer[:30]}... | {elapsed:.2f}s")

    append_message(session_id, "user", message, user_id=user_id)
    append_message(session_id, "assistant", answer, user_id=user_id)

    return {"reply": answer, "source": source, "session_id": session_id}


# ============ 建议 ============

@app.get("/suggestions")
async def suggestions(session_id: str = "default"):
    history = get_messages(session_id)
    recent = [m["content"] for m in history if m["role"] == "user"][-3:]

    faq_list = load_faq()
    candidates = []
    seen_questions = set()

    for faq in faq_list:
        all_kw = faq.get("keywords", []) + faq.get("synonyms", [])
        for msg in recent:
            if any(kw in msg for kw in all_kw):
                if faq["question"] not in seen_questions:
                    candidates.append(faq["question"])
                    seen_questions.add(faq["question"])
                break

    defaults = ["退款多久到账", "多久发货", "如何查询物流", "运费怎么算", "如何联系人工客服"]
    for q in defaults:
        if q not in seen_questions and len(candidates) < 5:
            candidates.append(q)
            seen_questions.add(q)

    return {"suggestions": candidates[:5]}


# ============ 反馈 ============

@app.post("/feedback")
async def submit_feedback(request: Request):
    body = await request.json()
    session_id = body.get("session_id", "")
    reply_text = body.get("reply_text", "")[:200]
    rating = body.get("rating", "")
    source = body.get("source", "")

    if rating not in ("helpful", "unhelpful"):
        return JSONResponse({"ok": False, "error": "无效评价"}, 400)

    db = get_db()
    db.execute(
        "INSERT INTO feedback (session_id, reply_text, rating, source) VALUES (?,?,?,?)",
        (session_id, reply_text, rating, source),
    )
    db.commit()
    db.close()
    return {"ok": True}


# ============ 统计 ============

@app.get("/stats")
async def stats():
    return {
        "match_counts": dict(match_stats),
        "recent_queries": query_log[-50:],
    }


# ============ 管理 API ============

@app.post("/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    if body.get("password", "") != ADMIN_PASSWORD:
        return JSONResponse({"ok": False, "error": "密码错误"}, 401)
    return {"ok": True}


@app.get("/admin/api/faq")
async def admin_list_faq():
    db = get_db()
    rows = db.execute("SELECT id, question, keywords, synonyms, answer, updated_at FROM faq ORDER BY id").fetchall()
    db.close()
    return [
        {
            "id": r["id"], "question": r["question"],
            "keywords": json.loads(r["keywords"]), "synonyms": json.loads(r["synonyms"]),
            "answer": r["answer"], "updated_at": r["updated_at"],
        }
        for r in rows
    ]


@app.post("/admin/api/faq")
async def admin_create_faq(request: Request):
    body = await request.json()
    question = body.get("question", "").strip()
    answer = body.get("answer", "").strip()
    if not question or not answer:
        return JSONResponse({"ok": False, "error": "问题和答案不能为空"}, 400)
    keywords = json.dumps(body.get("keywords", []), ensure_ascii=False)
    synonyms = json.dumps(body.get("synonyms", []), ensure_ascii=False)
    db = get_db()
    db.execute("INSERT INTO faq (question, keywords, synonyms, answer) VALUES (?,?,?,?)",
               (question, keywords, synonyms, answer))
    db.commit()
    db.close()
    sync_faq_json()
    return {"ok": True}


@app.put("/admin/api/faq/{faq_id}")
async def admin_update_faq(faq_id: int, request: Request):
    body = await request.json()
    question = body.get("question", "").strip()
    answer = body.get("answer", "").strip()
    if not question or not answer:
        return JSONResponse({"ok": False, "error": "问题和答案不能为空"}, 400)
    keywords = json.dumps(body.get("keywords", []), ensure_ascii=False)
    synonyms = json.dumps(body.get("synonyms", []), ensure_ascii=False)
    db = get_db()
    db.execute(
        "UPDATE faq SET question=?, keywords=?, synonyms=?, answer=?, updated_at=datetime('now') WHERE id=?",
        (question, keywords, synonyms, answer, faq_id),
    )
    db.commit()
    db.close()
    sync_faq_json()
    return {"ok": True}


@app.delete("/admin/api/faq/{faq_id}")
async def admin_delete_faq(faq_id: int):
    db = get_db()
    db.execute("DELETE FROM faq WHERE id=?", (faq_id,))
    db.commit()
    db.close()
    sync_faq_json()
    return {"ok": True}


@app.get("/admin/api/feedback/stats")
async def admin_feedback_stats():
    db = get_db()
    rows = db.execute(
        "SELECT rating, source, COUNT(*) as cnt FROM feedback GROUP BY rating, source ORDER BY rating, source"
    ).fetchall()
    total = db.execute("SELECT COUNT(*) as cnt FROM feedback").fetchone()["cnt"]
    db.close()
    breakdown = {}
    for r in rows:
        breakdown.setdefault(r["rating"], {})[r["source"]] = r["cnt"]
    return {"total": total, "breakdown": breakdown}


@app.get("/admin/api/faq-suggestions")
async def admin_faq_suggestions():
    """分析 query_log 中频繁走到 AI 的问题，建议补充 FAQ。"""
    from collections import defaultdict as _dd

    ai_queries = [q["query"] for q in query_log if q["source"] == "ai"]
    if not ai_queries:
        return {"suggestions": []}

    # 简单聚类：按查询文本聚合计数
    query_counts = _dd(int)
    for q in ai_queries:
        query_counts[q] += 1

    # 按频次排序，取 top 10
    sorted_queries = sorted(query_counts.items(), key=lambda x: -x[1])
    suggestions = [
        {"query": q, "count": cnt}
        for q, cnt in sorted_queries[:10]
        if cnt >= 2  # 至少出现 2 次才建议
    ]
    return {"suggestions": suggestions}
