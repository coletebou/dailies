"""Request guard, applied to every request before routing.

1. Host allowlist (DNS-rebinding guard): only 127.0.0.1 / localhost and the configured public hostnames are answered.
2. Requests for the public hostname arrive through the Cloudflare tunnel and must carry a valid Cloudflare Access
   JWT (Cf-Access-Jwt-Assertion header, or the CF_Authorization cookie): RS256, signed by the team's current keys,
   aud = this application's AUD tag, issuer = the team domain, unexpired, and (when configured) email on the
   allowlist. This is defence in depth on top of Access at the edge and cloudflared's own access check; with no AUD
   or no team domain configured it fails closed.
3. Mutating requests need the X-Dailies header, which a cross-site form or image can't send without a CORS preflight
   (and the server answers no preflights), so other sites can't drive the API through a viewer's cookies.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from .config import Settings

log = logging.getLogger("dailies.auth")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class AccessVerifier:
    def __init__(self, settings: Settings, key_resolver: Callable[[str], object] | None = None):
        self.s = settings
        self.issuer = f"https://{settings.access_team_domain}"
        self._resolver = key_resolver
        self._client: jwt.PyJWKClient | None = None
        self._lock = threading.Lock()

    def _key_for(self, token: str):
        if self._resolver:
            return self._resolver(token)
        with self._lock:
            if self._client is None:
                self._client = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600,
                                               timeout=10)
        return self._client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> dict:
        if not self.s.access_aud or not self.s.access_team_domain:
            raise PermissionError("Access AUD / team domain not configured; refusing public requests")
        key = self._key_for(token)
        claims = jwt.decode(token, key=key, algorithms=["RS256"], audience=self.s.access_aud, issuer=self.issuer,
                            options={"require": ["exp", "iat", "aud", "iss"]}, leeway=30)
        email = str(claims.get("email", "")).lower()
        if self.s.access_emails and email not in self.s.access_emails:
            raise PermissionError(f"{email or 'no email'} is not allowed")
        return claims


def _header(scope, name: bytes) -> str:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return ""


def host_of(scope) -> str:
    h = _header(scope, b"host").lower()
    if h.startswith("["):
        return h.split("]")[0] + "]"
    return h.rsplit(":", 1)[0] if ":" in h else h


SECURITY_HEADERS = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY")]


class GuardMiddleware:
    """Pure ASGI (no response buffering), so ranged video streams pass straight through."""

    def __init__(self, app, settings: Settings, verifier: AccessVerifier):
        self.app = app
        self.s = settings
        self.v = verifier
        self._fail_log = 0.0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        host = host_of(scope)
        user = "local"
        if host in self.s.public_hosts:
            request = Request(scope)
            token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization")
            if not token:
                return await PlainTextResponse("Cloudflare Access token required", status_code=403)(scope, receive, send)
            try:
                from starlette.concurrency import run_in_threadpool
                claims = await run_in_threadpool(self.v.verify, token)
                user = str(claims.get("email", "access"))
            except Exception as e:  # noqa: BLE001 - any verification failure is a 403
                if time.monotonic() - self._fail_log > 10:
                    log.warning("access token rejected: %s", e)
                    self._fail_log = time.monotonic()
                return await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)
        elif host not in self.s.local_hosts:
            return await PlainTextResponse("Unknown host", status_code=421)(scope, receive, send)
        if scope["method"] in MUTATING and _header(scope, b"x-dailies") != "1":
            return await JSONResponse({"detail": "missing X-Dailies header"}, status_code=403)(scope, receive, send)
        scope.setdefault("state", {})["user"] = user

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [h for h in SECURITY_HEADERS if h[0] not in names]
            await send(message)
        await self.app(scope, receive, send_wrapped)
