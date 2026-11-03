"""Panel authentication and roles.

* Tokens are generated with `python -m blpanel create-token`; only their
  SHA-256 digest goes into the environment.
* Login exchanges a token for a signed, HttpOnly, SameSite=Strict cookie.
* Roles: viewer (read only) and admin (sync, import, lab).
* Mutating requests must carry `X-Panel-Request: 1` (CSRF guard on top of SameSite).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from .config import Settings

COOKIE = "blp_session"
SESSION_TTL = 8 * 3600
ROLES = {"viewer": 1, "admin": 2}


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_digest(token)


class Auth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.secret = (settings.session_secret or secrets.token_hex(32)).encode("utf-8")

    @property
    def configured(self) -> bool:
        return bool(self.settings.admin_token_sha256)

    def role_for_token(self, token: str) -> str | None:
        d = token_digest(token or "")
        if self.settings.admin_token_sha256 and hmac.compare_digest(d, self.settings.admin_token_sha256):
            return "admin"
        if self.settings.viewer_token_sha256 and hmac.compare_digest(d, self.settings.viewer_token_sha256):
            return "viewer"
        return None

    def issue(self, role: str) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"r": role, "e": int(time.time()) + SESSION_TTL}).encode())
        sig = hmac.new(self.secret, payload, hashlib.sha256).hexdigest()
        return f"{payload.decode()}.{sig}"

    def verify(self, cookie: str | None) -> str | None:
        if not cookie or "." not in cookie:
            return None
        payload, sig = cookie.rsplit(".", 1)
        expected = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        except (ValueError, json.JSONDecodeError):
            return None
        if data.get("e", 0) < time.time() or data.get("r") not in ROLES:
            return None
        return data["r"]

    @staticmethod
    def allows(role: str | None, needed: str) -> bool:
        return role is not None and ROLES[role] >= ROLES[needed]
