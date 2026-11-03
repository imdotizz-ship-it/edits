"""BattlelogConnector: read-only data acquisition.

Sources:
  * battlelog-live  - anonymous GET requests to the public pages the Battlelog
                      web app itself loads (no login, no cookies, no tokens).
  * manual-import   - a HAR file or JSON bundle you captured in your own browser.

Hard rules enforced in code:
  * only GET is ever issued; there is no method parameter to change it;
  * Cookie / Authorization headers are never sent and are rejected if passed;
  * a minimum interval between requests (polite rate limit);
  * every response keeps its URL, HTTP status, timestamp and SHA-256.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .platforms import platform_int, platform_name

FORBIDDEN_HEADERS = {"cookie", "authorization", "x-csrf-token", "post-check-sum"}

# Endpoint catalogue. `evidence` documents how much we actually know.
ENDPOINTS = {
    "identity": {
        "path": "/{game}/user/{username}/",
        "purpose": "Resolve a username to userId + soldiers (personaId, platform)",
        "evidence": "PRIOR_KNOWLEDGE_UNVERIFIED",
    },
    "overview": {
        "path": "/{game}/warsawoverviewpopulate/{persona_id}/{platform}/",
        "purpose": "Overview stats (kills, deaths, skill, score, time, wins/losses ...)",
        "evidence": "PRIOR_KNOWLEDGE_UNVERIFIED",
    },
    "detailed": {
        "path": "/{game}/warsawdetailedstatspopulate/{persona_id}/{platform}/",
        "purpose": "Detailed general stats (headshots, shots fired/hit, rounds ...)",
        "evidence": "PRIOR_KNOWLEDGE_UNVERIFIED",
    },
    "dogtags": {
        "path": None,  # taken from Settings.dogtags_path
        "purpose": "Soldier dogtag page (catalogue + ownership, if exposed)",
        "evidence": "CANDIDATE_UNVERIFIED",
    },
}

# URL patterns used to pick the right entries out of a HAR capture.
HAR_PATTERNS = {
    "identity": re.compile(r"/(?P<game>bf[34h]|bfh)/user/(?P<username>[^/?#]+)/?(?:[?#]|$)"),
    "overview": re.compile(r"/(?P<game>bf4)/warsawoverviewpopulate/(?P<persona_id>\d+)/(?P<platform>\d+)/?"),
    "detailed": re.compile(r"/(?P<game>bf4)/warsawdetailedstatspopulate/(?P<persona_id>\d+)/(?P<platform>\d+)/?"),
    "dogtags": re.compile(r"/(?P<game>bf4)/.*dogtag[^/]*/.*?(?P<persona_id>\d{4,})/(?P<platform>\d+)/?", re.I),
}


class ConnectorError(Exception):
    """kind: network_blocked | network | http | not_json | battlelog_error | not_found | invalid_input"""

    def __init__(self, kind: str, message: str, url: str | None = None, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.url = url
        self.status = status

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": str(self), "url": self.url, "status": self.status}


@dataclass
class RawResponse:
    resource: str            # identity | overview | detailed | dogtags
    url: str
    status: int
    fetched_at: str
    sha256: str
    source_kind: str         # battlelog-live | manual-import
    body: Any = field(repr=False)

    def meta(self) -> dict:
        d = asdict(self)
        d.pop("body")
        return d


class Transport(Protocol):
    def get(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]: ...


class UrllibTransport:
    """Plain HTTPS GET. Uses the system trust store / proxy settings."""

    def get(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
        req = urllib.request.Request(url, headers=headers, method="GET")
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read() or b""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _classify_network_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "tunnel connection failed: 403" in text or ("403" in text and "tunnel" in text):
        return "network_blocked"
    return "network"


class BattlelogConnector:
    def __init__(self, settings: Settings, transport: Transport | None = None, clock=time.monotonic,
                 sleep=time.sleep):
        self.settings = settings
        self.transport = transport or UrllibTransport()
        self._clock = clock
        self._sleep = sleep
        self._last_request: float | None = None

    # ---- low level -------------------------------------------------------------
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            # Battlelog pages answer with JSON instead of HTML when the web app's
            # own AJAX navigation headers are present (PRIOR_KNOWLEDGE_UNVERIFIED).
            "X-AjaxNavigation": "1",
            "X-Requested-With": "XMLHttpRequest",
        }
        for k, v in (extra or {}).items():
            if k.lower() in FORBIDDEN_HEADERS:
                raise ConnectorError("invalid_input", f"header {k!r} is not allowed: the connector never "
                                     "sends credentials or session material")
            headers[k] = v
        return headers

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self.settings.min_request_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def get_json(self, resource: str, path: str) -> RawResponse:
        url = self.settings.base_url + path
        self._throttle()
        try:
            status, body = self.transport.get(url, self._headers(), self.settings.request_timeout)
        except ConnectorError:
            raise
        except Exception as exc:  # urllib.error.URLError, socket errors, TLS ...
            raise ConnectorError(_classify_network_error(exc), f"{type(exc).__name__}: {exc}", url) from exc
        digest = hashlib.sha256(body).hexdigest()
        if status == 404:
            raise ConnectorError("not_found", "Battlelog answered 404", url, status)
        if status >= 400:
            raise ConnectorError("http", f"Battlelog answered HTTP {status}", url, status)
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            snippet = body[:120].decode("utf-8", "replace")
            raise ConnectorError("not_json", f"response is not JSON (starts with {snippet!r})", url, status) from exc
        if isinstance(data, dict) and data.get("type") == "error":
            raise ConnectorError("battlelog_error", f"Battlelog error: {data.get('message')!r}", url, status)
        return RawResponse(resource, url, status, _now(), digest, "battlelog-live", data)

    # ---- resources -------------------------------------------------------------
    def _game(self) -> str:
        return urllib.parse.quote(self.settings.game, safe="")

    def fetch_identity(self, username: str) -> RawResponse:
        if not re.fullmatch(r"[A-Za-z0-9_\-. ]{1,32}", username or ""):
            raise ConnectorError("invalid_input", "username contains unexpected characters")
        path = ENDPOINTS["identity"]["path"].format(game=self._game(), username=urllib.parse.quote(username))
        return self.get_json("identity", path)

    def fetch_overview(self, persona_id: str, platform: str) -> RawResponse:
        path = ENDPOINTS["overview"]["path"].format(game=self._game(), persona_id=_pid(persona_id),
                                                    platform=platform_int(platform))
        return self.get_json("overview", path)

    def fetch_detailed(self, persona_id: str, platform: str) -> RawResponse:
        path = ENDPOINTS["detailed"]["path"].format(game=self._game(), persona_id=_pid(persona_id),
                                                    platform=platform_int(platform))
        return self.get_json("detailed", path)

    def fetch_dogtags(self, persona_id: str, persona_name: str, platform: str) -> RawResponse:
        path = self.settings.dogtags_path.format(
            game=self._game(), persona_id=_pid(persona_id), platform=platform_int(platform),
            persona_name=urllib.parse.quote(persona_name))
        return self.get_json("dogtags", path)

    def fetch_profile(self, persona_id: str, platform: str, persona_name: str | None = None,
                      include_dogtags: bool = True) -> tuple[list[RawResponse], list[dict]]:
        """Fetch everything available. Returns (responses, errors). Overview is required;
        the other resources are best effort and their failures are reported, not hidden."""
        responses = [self.fetch_overview(persona_id, platform)]
        errors: list[dict] = []
        optional = [("detailed", lambda: self.fetch_detailed(persona_id, platform))]
        if include_dogtags and persona_name:
            optional.append(("dogtags", lambda: self.fetch_dogtags(persona_id, persona_name, platform)))
        for name, fn in optional:
            try:
                responses.append(fn())
            except ConnectorError as e:
                errors.append({"resource": name, **e.to_dict()})
        return responses, errors


def _pid(persona_id: str) -> str:
    pid = str(persona_id).strip()
    if not pid.isdigit():
        raise ConnectorError("invalid_input", "persona id must be numeric")
    return pid


# ---- manual import ---------------------------------------------------------------
def load_har(path: Path) -> list[RawResponse]:
    """Extract Battlelog JSON responses from a browser HAR export.

    Request headers/cookies are ignored entirely (never stored)."""
    har = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[RawResponse] = []
    for entry in har.get("log", {}).get("entries", []):
        url = entry.get("request", {}).get("url", "")
        if "battlelog.battlefield.com" not in urllib.parse.urlparse(url).netloc:
            continue
        resource = next((name for name, pat in HAR_PATTERNS.items() if pat.search(url)), None)
        if not resource:
            continue
        content = entry.get("response", {}).get("content", {})
        text = content.get("text")
        if text is None:
            continue
        raw = base64.b64decode(text) if content.get("encoding") == "base64" else text.encode("utf-8")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue  # HTML page view, not the JSON payload
        out.append(RawResponse(resource, url, int(entry.get("response", {}).get("status", 0)),
                               entry.get("startedDateTime") or _now(), hashlib.sha256(raw).hexdigest(),
                               "manual-import", body))
    return out


def load_bundle(path: Path) -> list[RawResponse]:
    """JSON bundle: {"captured_at": iso, "responses": {"overview": {"url":..., "body": {...}}, ...}}"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    captured = data.get("captured_at") or _now()
    out = []
    for resource, item in (data.get("responses") or {}).items():
        if resource not in ENDPOINTS:
            continue
        body = item.get("body")
        raw = json.dumps(body, sort_keys=True).encode("utf-8")
        out.append(RawResponse(resource, item.get("url", f"file://{Path(path).name}"), int(item.get("status", 200)),
                               item.get("fetched_at", captured), hashlib.sha256(raw).hexdigest(),
                               "manual-import", body))
    return out


def load_import(path: Path) -> list[RawResponse]:
    p = Path(path)
    if p.suffix.lower() == ".har":
        return load_har(p)
    return load_bundle(p)


def persona_from_url(url: str) -> tuple[str | None, str | None]:
    for name in ("overview", "detailed", "dogtags"):
        m = HAR_PATTERNS[name].search(url)
        if m:
            return m.group("persona_id"), platform_name(m.group("platform"))
    return None, None
