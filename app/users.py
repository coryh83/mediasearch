import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from app import config

USERS_PATH: Path = config.DATA_DIR / "users.json"
OWNERS_PATH: Path = config.DATA_DIR / "owners.json"
SECRET_PATH: Path = config.DATA_DIR / "secret.key"

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{2,32}$")
MIN_PASSWORD = 8
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60

_failures: dict[str, list[float]] = {}


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


# ---------- passwords ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def _verify(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=32)
    except ValueError:
        return False
    return secrets.compare_digest(digest.hex(), digest_hex)


# ---------- users ----------

def load_users() -> dict:
    return _read_json(USERS_PATH)


def _save_users(users: dict) -> None:
    _write_private(USERS_PATH, json.dumps(users, indent=2))


def _check_username(username: str) -> str:
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise ValueError("usernames use 2-32 letters, numbers, dots, dashes or underscores")
    return username


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")


def bootstrap(admin_username: str, admin_password: str) -> None:
    """First run only: create the admin account from APP_USER / APP_PASSWORD."""
    if load_users():
        return
    if not admin_password:
        raise RuntimeError("APP_PASSWORD must be set for the first run.")
    _save_users({_check_username(admin_username): {"hash": hash_password(admin_password), "admin": True}})


def check_login(username: str, password: str) -> bool:
    username = username.strip().lower()
    recent = [t for t in _failures.get(username, []) if time.time() - t < LOCK_SECONDS]
    _failures[username] = recent
    if len(recent) >= MAX_FAILURES:
        return False  # locked out for a while after repeated failures

    rec = load_users().get(username)
    ok = bool(rec) and _verify(password, rec["hash"])
    if ok:
        _failures.pop(username, None)
    else:
        _failures[username] = recent + [time.time()]
    return ok


def create_user(username: str, password: str, admin: bool) -> str:
    username = _check_username(username)
    _check_password(password)
    users = load_users()
    if username in users:
        raise ValueError(f"{username} already exists")
    users[username] = {"hash": hash_password(password), "admin": admin}
    _save_users(users)
    return username


def delete_user(username: str, acting_user: str) -> None:
    users = load_users()
    if username not in users:
        raise ValueError("no such user")
    if username == acting_user:
        raise ValueError("you cannot delete your own account")
    if users[username]["admin"] and sum(1 for u in users.values() if u["admin"]) == 1:
        raise ValueError("cannot delete the last admin")
    del users[username]
    _save_users(users)


def set_password(username: str, password: str) -> None:
    _check_password(password)
    users = load_users()
    if username not in users:
        raise ValueError("no such user")
    users[username]["hash"] = hash_password(password)
    _save_users(users)


def change_own_password(username: str, current: str, new: str) -> None:
    rec = load_users().get(username)
    if not rec or not _verify(current, rec["hash"]):
        raise ValueError("current password is wrong")
    set_password(username, new)


# ---------- who added which torrent ----------

def record_owner(torrent_hash: str, username: str) -> None:
    if not torrent_hash:
        return
    owners = _read_json(OWNERS_PATH)
    owners[torrent_hash.lower()] = username
    _write_private(OWNERS_PATH, json.dumps(owners, indent=2))


def owner_of(torrent_hash: str) -> str:
    return _read_json(OWNERS_PATH).get((torrent_hash or "").lower(), "")


# ---------- session signing key ----------

def secret_key() -> bytes:
    if not SECRET_PATH.exists():
        _write_private(SECRET_PATH, secrets.token_hex(32))
    return SECRET_PATH.read_text().strip().encode()
