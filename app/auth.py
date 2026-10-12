"""Sign-in for the dashboard and API, for when the app is on a public address.

Without it, anyone with the link could change tasks and run the agents on the owner's LLM credits.
There is one shared password (DASHBOARD_PASSWORD) and a sign-in page; a correct password sets a
cookie. The Telegram bot is not affected: it is protected by the team join code instead.
"""

import asyncio
import hashlib
import hmac
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE_NAME = "taskpilot_session"
SESSION_DAYS = 7
# Slows down someone trying passwords one after another.
WRONG_PASSWORD_DELAY_SECONDS = 1.0

LOGIN_PAGE = Path(__file__).parent / "static" / "login.html"
ERROR_SLOT = "<!-- error -->"
ERROR_HTML = '<p id="signin-error" class="field-error" role="alert">That password is not correct. Try again.</p>'

# Reachable signed out: the host's health check, the sign-in page itself, and the styles it uses.
OPEN_PATHS = {"/health", "/login", "/logout"}
OPEN_PREFIXES = ("/static/css/",)


def session_token(password: str) -> str:
    """The cookie's value. It is derived from the password, so it survives a restart of the
    server and stops working the moment the password is changed. The password is never stored."""
    return hmac.new(password.encode(), b"taskpilot-session-v1", hashlib.sha256).hexdigest()


def signed_in(request: Request, password: str) -> bool:
    cookie = request.cookies.get(COOKIE_NAME, "")
    # compare_digest takes the same time whether the guess is close or not.
    return hmac.compare_digest(cookie.encode(), session_token(password).encode())


def _is_https(request: Request) -> bool:
    # Behind a hosting platform's proxy the original scheme arrives in this header.
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def require_sign_in(password: str):
    """Middleware: everything except the open paths needs the sign-in cookie."""

    async def middleware(request: Request, call_next):
        path = request.url.path
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES) or signed_in(request, password):
            return await call_next(request)
        # A person opening a page is sent to sign in. A script or the dashboard's own
        # background request gets a plain "not signed in" answer instead.
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": "Sign in to continue."}, status_code=401)

    return middleware


def build_auth_router(password: str) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    def page(error: bool = False, status_code: int = 200) -> HTMLResponse:
        html = LOGIN_PAGE.read_text(encoding="utf-8").replace(ERROR_SLOT, ERROR_HTML if error else "")
        return HTMLResponse(html, status_code=status_code)

    @router.get("/login")
    async def sign_in_page(request: Request):
        if not password or signed_in(request, password):
            return RedirectResponse("/", status_code=303)
        return page()

    @router.post("/login")
    async def sign_in(request: Request):
        if not password:
            return RedirectResponse("/", status_code=303)
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        given = form.get("password", [""])[0]
        if not hmac.compare_digest(given.encode(), password.encode()):
            await asyncio.sleep(WRONG_PASSWORD_DELAY_SECONDS)
            return page(error=True, status_code=401)

        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            session_token(password),
            max_age=SESSION_DAYS * 24 * 3600,
            httponly=True,  # page scripts cannot read it
            samesite="lax",  # other websites cannot use it to act as this person
            secure=_is_https(request),
        )
        return response

    @router.post("/logout")
    async def sign_out():
        response = RedirectResponse("/login" if password else "/", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    return router
