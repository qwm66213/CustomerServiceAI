import base64
import hashlib
import hmac
import json
import os
import time

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
TOKEN_EXPIRY = 86400  # 24 小时


def _gen_salt() -> str:
    return os.urandom(16).hex()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = _gen_salt()
    hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return hashed, salt


def verify_password(password: str, hashed_pw: str, salt: str) -> bool:
    computed, _ = hash_password(password, salt)
    return hmac.compare_digest(computed, hashed_pw)


def create_token(user_id: int, username: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": int(time.time()) + TOKEN_EXPIRY,
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode())
    sig = hmac.new(SECRET_KEY.encode(), raw, hashlib.sha256).digest()
    return f"{raw.decode()}.{base64.urlsafe_b64encode(sig).decode()}"


def decode_token(token: str) -> dict | None:
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(base64.urlsafe_b64decode(sig), expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
