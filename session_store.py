import json
import logging

from database import get_db

logger = logging.getLogger(__name__)

MAX_HISTORY = 10


def get_messages(session_id: str) -> list[dict]:
    db = get_db()
    row = db.execute("SELECT messages FROM sessions WHERE id = ?", (session_id,)).fetchone()
    db.close()
    if row:
        return json.loads(row["messages"])
    return []


def append_message(session_id: str, role: str, content: str, user_id: int | None = None):
    db = get_db()
    msgs = get_messages(session_id)
    msgs.append({"role": role, "content": content})
    if len(msgs) > MAX_HISTORY * 2:
        msgs = msgs[-(MAX_HISTORY * 2):]
    msg_json = json.dumps(msgs, ensure_ascii=False)
    db.execute(
        """INSERT INTO sessions (id, user_id, messages, updated_at) VALUES (?,?,?,datetime('now'))
           ON CONFLICT(id) DO UPDATE SET messages=excluded.messages, updated_at=datetime('now')""",
        (session_id, user_id, msg_json),
    )
    db.commit()
    db.close()
