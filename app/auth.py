"""Sign-in for the dashboard and API, for when the app is on a public address.

Without it, anyone with the link could change tasks and run the agents on the owner's LLM credits.
There is one shared account (DASHBOARD_USERNAME and DASHBOARD_PASSWORD) and a sign-in page; correct
details set a cookie. The Telegram bot is not affected: it is protected by the team join code instead.
"""

import asyncio
import hashlib
import hmac
from html import escape
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
USERNAME_SLOT = "__USERNAME__"
# Deliberately does not say which of the two was wrong.
ERROR_HTML = '<p id="signin-error" class="field-error" role="alert">That username or password is not correct. Try again.</p>'

# Reachable signed out: the host's health check, the sign-in page itself, and the styles it uses.
OPEN_PATHS = {"/health", "/login", "/logout"}
OPEN_PREFIXES = ("/static/css/",)


def _same(a: str, b: str) -> bool:
    # compare_digest takes the same time whether the guess is close or not.
    return hmac.compare_digest(a.encode(), b.encode())


def _name(username: str) -> str:
    """Usernames ignore capitals and surrounding spaces. Passwords are compared exactly."""
    return username.strip().lower()


def session_token(username: str, password: str) -> str:
    """The cookie's value. It is derived from the account details, so it survives a restart of the
    server and stops working the moment either one is changed. Neither is stored in the cookie."""
    return hmac.new(password.encode(), f"taskpilot-session-v2:{_name(username)}".encode(), hashlib.sha256).hexdigest()


def signed_in(request: Request, username: str, password: str) -> bool:
    return _same(request.cookies.get(COOKIE_NAME, ""), session_token(username, password))


def _is_https(request: Request) -> bool:
    # Behind a hosting platform's proxy the original scheme arrives in this header.
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def require_sign_in(username: str, password: str):
    """Middleware: everything except the open paths needs the sign-in cookie."""

    async def middleware(request: Request, call_next):
        path = request.url.path
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES) or signed_in(request, username, password):
            return await call_next(request)
        # A person opening a page is sent to sign in. A script or the dashboard's own
        # background request gets a plain "not signed in" answer instead.
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": "Sign in to continue."}, status_code=401)

    return middleware


def build_auth_router(username: str, password: str) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    def page(typed_username: str = "", error: bool = False, status_code: int = 200) -> HTMLResponse:
        html = LOGIN_PAGE.read_text(encoding="utf-8")
        html = html.replace(ERROR_SLOT, ERROR_HTML if error else "")
        # Whatever was typed is escaped before it goes back into the page.
        html = html.replace(USERNAME_SLOT, escape(typed_username, quote=True))
        return HTMLResponse(html, status_code=status_code, headers={"Cache-Control": "no-store"})

    @router.get("/login")
    async def sign_in_page(request: Request):
        if not password or signed_in(request, username, password):
            return RedirectResponse("/", status_code=303)
        return page()

    @router.post("/login")
    async def sign_in(request: Request):
        if not password:
            return RedirectResponse("/", status_code=303)
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"), keep_blank_values=True)
        typed_username = form.get("username", [""])[0]
        typed_password = form.get("password", [""])[0]

        # Both are always checked, so the time taken does not reveal which one was wrong.
        name_ok = _same(_name(typed_username), _name(username))
        password_ok = _same(typed_password, password)
        if not (name_ok and password_ok):
            await asyncio.sleep(WRONG_PASSWORD_DELAY_SECONDS)
            return page(typed_username=_name(typed_username), error=True, status_code=401)

        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            session_token(username, password),
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
