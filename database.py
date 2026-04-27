import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data.db"
FAQ_PATH = Path(__file__).parent / "faq.json"


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            hashed_pw TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            messages TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS faq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '[]',
            synonyms TEXT NOT NULL DEFAULT '[]',
            answer TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            rating TEXT NOT NULL CHECK(rating IN ('helpful','unhelpful')),
            source TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # 首次运行：从 faq.json 种子数据
    count = db.execute("SELECT COUNT(*) FROM faq").fetchone()[0]
    if count == 0:
        with open(FAQ_PATH, "r", encoding="utf-8") as f:
            faq_list = json.load(f)
        for faq in faq_list:
            db.execute(
                "INSERT INTO faq (question, keywords, synonyms, answer) VALUES (?,?,?,?)",
                (faq["question"], json.dumps(faq.get("keywords", []), ensure_ascii=False),
                 json.dumps(faq.get("synonyms", []), ensure_ascii=False), faq["answer"]),
            )
        logger.info(f"从 faq.json 导入 {len(faq_list)} 条种子数据")

    db.commit()
    db.close()
    logger.info("数据库初始化完成")
