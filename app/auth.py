"""One shared password for the dashboard and API, for when the app is on a public address.

Without it, anyone with the link could change tasks and run the agents on the owner's LLM credits.
It uses the browser's built-in sign-in prompt (HTTP Basic), so the dashboard needs no login page.
The Telegram bot is not affected: it is protected by the team join code instead.
"""

import base64
import binascii
import hmac

from fastapi import Request
from fastapi.responses import PlainTextResponse

# The host's health check has no password to send.
OPEN_PATHS = {"/health"}


def _password_from(header: str | None) -> str | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    _, _, password = decoded.partition(":")
    return password


def require_password(expected: str):
    """Middleware: every request except the health check must carry the shared password. Any username works."""

    async def middleware(request: Request, call_next):
        if request.url.path not in OPEN_PATHS:
            given = _password_from(request.headers.get("authorization"))
            # compare_digest takes the same time whether the guess is close or not.
            if given is None or not hmac.compare_digest(given.encode(), expected.encode()):
                return PlainTextResponse(
                    "This dashboard is password-protected.",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="TaskPilot", charset="UTF-8"'},
                )
        return await call_next(request)

    return middleware
