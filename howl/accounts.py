"""
Howlscan site accounts — usernames + password login (non-custodial).

Accounts do NOT hold funds. Optional linked HOWL address / on-chain @name.
Stored under HOWL_PUBLIC_DATA/howl_accounts.json + howl_sessions.json.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_lock = threading.RLock()

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,19}$")
RESERVED = frozenset(
    {
        "admin",
        "howl",
        "howlcoin",
        "howlscan",
        "api",
        "app",
        "wallet",
        "null",
        "undefined",
        "system",
        "support",
        "official",
        "root",
        "login",
        "signup",
        "account",
        "user",
        "me",
        "public",
        "city",
        "play",
        "culture",
        "charts",
        "health",
        "contracts",
    }
)

SESSION_DAYS = 30
PBKDF2_ITERS = 120_000


def _data_dir() -> Path:
    raw = (
        os.environ.get("HOWL_PUBLIC_DATA")
        or os.environ.get("HOWL_DATA_DIR")
        or str(Path.home() / ".howlcoin")
    )
    p = Path(raw).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _accounts_path() -> Path:
    return _data_dir() / "howl_accounts.json"


def _sessions_path() -> Path:
    return _data_dir() / "howl_sessions.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def normalize_username(raw: str) -> str:
    return (raw or "").strip().lower().lstrip("@")


def validate_username(username: str) -> Tuple[bool, str]:
    u = normalize_username(username)
    if not USERNAME_RE.match(u):
        return False, "Username: 3–20 chars, start with a letter, a–z 0–9 _ only"
    if u in RESERVED:
        return False, "Username is reserved"
    return True, u


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERS, dklen=32
    )
    return f"pbkdf2_sha256${PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iters, dklen=32
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _public_user(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "username": row.get("username"),
        "display_name": row.get("display_name") or row.get("username"),
        "howl_address": row.get("howl_address") or "",
        "bio": row.get("bio") or "",
        "created_at": row.get("created_at"),
        "onchain_name": row.get("onchain_name") or "",
    }


def register(
    username: str,
    password: str,
    *,
    howl_address: str = "",
    display_name: str = "",
) -> Dict[str, Any]:
    ok, u_or_err = validate_username(username)
    if not ok:
        raise ValueError(u_or_err)
    u = u_or_err
    pw = password or ""
    if len(pw) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(pw) > 128:
        raise ValueError("Password too long")
    addr = (howl_address or "").strip()
    if addr and not addr.startswith("H"):
        raise ValueError("HOWL address should start with H")

    with _lock:
        data = _load_json(_accounts_path(), {"users": {}})
        users: Dict[str, Any] = data.get("users") or {}
        if u in users:
            raise ValueError("Username already taken")
        # optional uniqueness of address
        if addr:
            for row in users.values():
                if (row.get("howl_address") or "") == addr:
                    raise ValueError("That HOWL address is already linked to an account")
        now = int(time.time())
        users[u] = {
            "username": u,
            "display_name": (display_name or u).strip()[:40],
            "password_hash": _hash_password(pw),
            "howl_address": addr,
            "bio": "",
            "onchain_name": "",
            "created_at": now,
            "updated_at": now,
        }
        data["users"] = users
        _save_json(_accounts_path(), data)
        return _public_user(users[u])


def login(username: str, password: str) -> Dict[str, Any]:
    ok, u_or_err = validate_username(username)
    if not ok:
        # still try exact normalize
        u = normalize_username(username)
    else:
        u = u_or_err
    with _lock:
        data = _load_json(_accounts_path(), {"users": {}})
        users = data.get("users") or {}
        row = users.get(u)
        if not row or not _verify_password(password or "", row.get("password_hash") or ""):
            raise ValueError("Invalid username or password")
        token = secrets.token_urlsafe(32)
        sessions = _load_json(_sessions_path(), {"sessions": {}})
        sess = sessions.get("sessions") or {}
        # prune expired
        now = time.time()
        sess = {
            k: v
            for k, v in sess.items()
            if float(v.get("expires_at") or 0) > now
        }
        exp = now + SESSION_DAYS * 86400
        sess[token] = {"username": u, "created_at": now, "expires_at": exp}
        sessions["sessions"] = sess
        _save_json(_sessions_path(), sessions)
        return {
            "token": token,
            "expires_at": int(exp),
            "user": _public_user(row),
        }


def logout(token: str) -> bool:
    token = (token or "").strip()
    if not token:
        return False
    with _lock:
        sessions = _load_json(_sessions_path(), {"sessions": {}})
        sess = sessions.get("sessions") or {}
        if token in sess:
            del sess[token]
            sessions["sessions"] = sess
            _save_json(_sessions_path(), sessions)
            return True
    return False


def session_user(token: Optional[str]) -> Optional[Dict[str, Any]]:
    token = (token or "").strip()
    if not token:
        return None
    with _lock:
        sessions = _load_json(_sessions_path(), {"sessions": {}})
        sess = sessions.get("sessions") or {}
        row = sess.get(token)
        if not row:
            return None
        if float(row.get("expires_at") or 0) < time.time():
            del sess[token]
            sessions["sessions"] = sess
            _save_json(_sessions_path(), sessions)
            return None
        users = (_load_json(_accounts_path(), {"users": {}}) or {}).get("users") or {}
        user = users.get(row.get("username") or "")
        if not user:
            return None
        return _public_user(user)


def get_user(username: str) -> Optional[Dict[str, Any]]:
    u = normalize_username(username)
    with _lock:
        users = (_load_json(_accounts_path(), {"users": {}}) or {}).get("users") or {}
        row = users.get(u)
        if not row:
            return None
        return _public_user(row)


def update_profile(
    token: str,
    *,
    display_name: Optional[str] = None,
    bio: Optional[str] = None,
    howl_address: Optional[str] = None,
    onchain_name: Optional[str] = None,
) -> Dict[str, Any]:
    me = session_user(token)
    if not me:
        raise ValueError("Not logged in")
    u = me["username"]
    with _lock:
        data = _load_json(_accounts_path(), {"users": {}})
        users = data.get("users") or {}
        row = users.get(u)
        if not row:
            raise ValueError("Account not found")
        if display_name is not None:
            row["display_name"] = str(display_name).strip()[:40] or u
        if bio is not None:
            row["bio"] = str(bio).strip()[:280]
        if howl_address is not None:
            addr = str(howl_address).strip()
            if addr and not addr.startswith("H"):
                raise ValueError("HOWL address should start with H")
            if addr:
                for other_u, other in users.items():
                    if other_u != u and (other.get("howl_address") or "") == addr:
                        raise ValueError("Address already linked to another account")
            row["howl_address"] = addr
        if onchain_name is not None:
            row["onchain_name"] = normalize_username(onchain_name)[:20]
        row["updated_at"] = int(time.time())
        users[u] = row
        data["users"] = users
        _save_json(_accounts_path(), data)
        return _public_user(row)


def list_users(limit: int = 50) -> List[Dict[str, Any]]:
    limit = max(1, min(200, int(limit or 50)))
    with _lock:
        users = (_load_json(_accounts_path(), {"users": {}}) or {}).get("users") or {}
        rows = sorted(
            users.values(),
            key=lambda r: int(r.get("created_at") or 0),
            reverse=True,
        )
        return [_public_user(r) for r in rows[:limit]]


def extract_token(headers: Dict[str, str], cookies: Optional[Dict[str, str]] = None) -> str:
    """Read session from Authorization: Bearer … or Cookie howl_session=…"""
    auth = (headers.get("Authorization") or headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # custom header for SPA
    t = (headers.get("X-Howl-Session") or headers.get("x-howl-session") or "").strip()
    if t:
        return t
    if cookies:
        return (cookies.get("howl_session") or "").strip()
    return ""
