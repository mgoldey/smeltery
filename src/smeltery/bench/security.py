"""The request guard in front of every Bench route: Host allow-list, Origin check, and a token off loopback.

- Host: a request whose Host header names anything but a loopback name, the bound host, or a host the user allowed is
  refused with 400. This is what stops DNS rebinding: a page on evil.example that re-resolves to 127.0.0.1 still
  sends `Host: evil.example`.
- Origin: an unsafe method (POST/PUT/PATCH/DELETE) carrying an Origin header whose host is not an allowed host is
  refused with 403. No Origin (curl, scripts) and same-origin requests pass.
- Token: when one is configured (required for a non-loopback bind), every request except `/healthz` and `/livez`
  needs `Authorization: Bearer <token>` or the cookie that visiting `/?token=<token>` sets. Compared in constant time.
- Read-only: with `read_only`, an unsafe method that got past the checks above is refused with 403, whatever the
  route. There is no safelist: a POST that only computes (an edit preview, a structure lookup) is refused too, so
  a public demo cannot write, and cannot be made to fetch or compute on a visitor's say-so.

`SecurityConfig` is plain data passed to `build_app`, so tests and other composition roots configure it the same way.
"""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse, Response

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Paths a probe may hit without the token: they answer a constant and reveal nothing.
OPEN_PATHS = frozenset({"/healthz", "/livez"})
COOKIE = "smeltery_bench_token"
TOKEN_ENV = "SMELTERY_BENCH_TOKEN"


def _bare(host: str) -> str:
    """A host name without brackets or port, lowercased: `[::1]:8765` -> `::1`, `Example:80` -> `example`."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host[1:]
    if host.count(":") == 1:  # name:port; a bare IPv6 address has more than one colon
        return host.split(":", 1)[0]
    return host


def is_loopback(host: str) -> bool:
    h = _bare(host)
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class SecurityConfig:
    """Who may talk to Bench. `bind_host` is the address uvicorn listens on; `allowed_hosts` are extra names (e.g.
    the machine's DNS name) the browser may use to reach it; `token`, when set, is required on every request."""

    bind_host: str = "127.0.0.1"
    allowed_hosts: tuple[str, ...] = ()
    token: str | None = None
    read_only: bool = False

    def hosts(self) -> frozenset[str]:
        return LOOPBACK_NAMES | {_bare(self.bind_host)} | {_bare(h) for h in self.allowed_hosts}

    def host_allowed(self, host: str) -> bool:
        return is_loopback(host) or _bare(host) in self.hosts()


def _problem(status: int, reason: str) -> Response:
    return JSONResponse({"reason": reason}, status_code=status)


def _token_ok(given: str | None, token: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), token.encode())


class SecurityMiddleware:
    """Pure ASGI, so it wraps streaming and static responses without buffering them."""

    def __init__(self, app: ASGIApp, config: SecurityConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        conn, method = HTTPConnection(scope), scope.get("method", "GET")
        response = self.check(conn, method)
        if response is None and self.config.read_only and method in UNSAFE_METHODS:
            response = _problem(403, f"this Bench is read-only: {method} {conn.url.path} is refused")
        if response is None:
            await self.app(scope, receive, send)
        else:
            await response(scope, receive, send)

    def check(self, conn: HTTPConnection, method: str) -> Response | None:
        """None to let the request through, or the response that refuses it."""
        cfg = self.config
        host = conn.headers.get("host", "")
        if not cfg.host_allowed(host):
            return _problem(400, f"Host {host!r} is not allowed; allowed: {', '.join(sorted(cfg.hosts()))}")
        origin = conn.headers.get("origin")
        if method in UNSAFE_METHODS and origin is not None:
            parsed = urlsplit(origin)
            if not parsed.hostname or not cfg.host_allowed(parsed.hostname):
                return _problem(403, f"cross-origin {method} from {origin!r} refused")
        if cfg.token is None or conn.url.path in OPEN_PATHS:
            return None
        given = conn.query_params.get("token")
        if given is not None and method == "GET" and not conn.url.path.startswith("/api"):
            if not _token_ok(given, cfg.token):
                return _problem(401, "wrong token")
            rest = [(k, v) for k, v in conn.query_params.multi_items() if k != "token"]
            target = conn.url.path + (f"?{urlencode(rest)}" if rest else "")
            r = RedirectResponse(target, status_code=303)  # drop the token from the address bar and history
            r.set_cookie(COOKIE, cfg.token, httponly=True, samesite="strict", path="/")
            return r
        auth = conn.headers.get("authorization", "")
        bearer = auth[7:] if auth[:7].lower() == "bearer " else None
        if _token_ok(bearer, cfg.token) or _token_ok(conn.cookies.get(COOKIE), cfg.token):
            return None
        return _problem(401, "this Bench needs its token: open the /?token=... URL it printed, or send a Bearer token")


def install(app: Any, config: SecurityConfig) -> None:
    app.add_middleware(SecurityMiddleware, config=config)
