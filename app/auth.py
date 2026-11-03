import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException, Request

from app import users

COOKIE = "ms_session"
MAX_AGE = 30 * 24 * 3600  # 30 days


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def make_token(username: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "exp": int(time.time()) + MAX_AGE}).encode()
    ).decode()
    sig = hmac.new(users.secret_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE, "")
    if "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(users.secret_key(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    rec = users.load_users().get(data.get("u", ""))
    if rec is None:
        return None  # account was deleted: the session stops working
    return {"username": data["u"], "admin": rec["admin"]}


def block_cross_site_posts(request: Request) -> None:
    """Browsers send session cookies on cross-site POSTs, so reject those."""
    if request.method == "POST" and request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="Cross-site request blocked")


def require_user(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise LoginRequired()
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not user["admin"]:
        raise Forbidden()
    return user
