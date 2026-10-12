"""What a public deployment needs: a sign-in page for the dashboard, and demo data that restores itself."""

import httpx
import pytest
from sqlalchemy import func, select

from app import auth
from app.demo import TASKS, TEAM, seed_if_empty
from app.main import create_app
from app.models import Task, User

PASSWORD = "open-sesame"
HTML = {"Accept": "text/html"}


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """A wrong password is answered slowly on purpose. Tests skip the wait."""
    monkeypatch.setattr(auth, "WRONG_PASSWORD_DELAY_SECONDS", 0)


@pytest.fixture
async def locked(settings):
    app = create_app(settings.model_copy(update={"dashboard_password": PASSWORD}))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    async with app.router.lifespan_context(app), client:
        yield client


async def sign_in(client, password=PASSWORD, **kwargs):
    return await client.post("/login", data={"password": password}, **kwargs)


# --- no password configured: a laptop, nothing to protect ------------------------


async def test_without_a_password_set_everything_is_open_as_before(client):
    assert (await client.get("/api/tasks")).status_code == 200
    assert (await client.get("/api/status")).json()["protected"] is False


async def test_without_a_password_the_sign_in_page_just_sends_you_to_the_dashboard(client):
    response = await client.get("/login")
    assert response.status_code == 303 and response.headers["location"] == "/"


# --- password configured ---------------------------------------------------------


async def test_opening_the_dashboard_signed_out_takes_you_to_the_sign_in_page(locked):
    response = await locked.get("/", headers=HTML)
    assert response.status_code == 303 and response.headers["location"] == "/login"


async def test_the_api_refuses_signed_out_requests_without_a_browser_popup(locked):
    for path in ("/api/tasks", "/api/runs", "/api/status", "/static/js/main.js", "/openapi.json"):
        response = await locked.get(path)
        assert response.status_code == 401, path
        # No WWW-Authenticate header: that header is what makes browsers show their own password box.
        assert "www-authenticate" not in response.headers
    assert (await locked.post("/api/chat", json={"user_id": 1, "text": "hi"})).status_code == 401


async def test_the_sign_in_page_and_its_styles_are_reachable_signed_out(locked):
    page = await locked.get("/login")
    assert page.status_code == 200
    assert 'type="password"' in page.text and 'action="/login"' in page.text
    assert "not correct" not in page.text
    assert (await locked.get("/static/css/app.css")).status_code == 200
    assert (await locked.get("/health")).status_code == 200


async def test_a_wrong_password_shows_a_clear_message_and_signs_nobody_in(locked):
    response = await sign_in(locked, "guess")
    assert response.status_code == 401
    assert "That password is not correct" in response.text
    assert "set-cookie" not in response.headers
    assert (await locked.get("/api/tasks")).status_code == 401


async def test_the_right_password_signs_you_in_and_the_dashboard_opens(locked):
    response = await sign_in(locked)
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=" in cookie

    assert (await locked.get("/", headers=HTML)).status_code == 200
    assert (await locked.get("/api/tasks")).status_code == 200
    assert (await locked.get("/api/status")).json()["protected"] is True
    # Already signed in: the sign-in page has nothing to ask.
    assert (await locked.get("/login")).status_code == 303


async def test_the_cookie_is_marked_secure_when_the_site_is_served_over_https(locked):
    response = await sign_in(locked, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in response.headers["set-cookie"].lower()


async def test_a_made_up_cookie_or_the_old_browser_password_box_gets_nowhere(locked):
    locked.cookies.set(auth.COOKIE_NAME, "f" * 64)
    assert (await locked.get("/api/tasks")).status_code == 401
    locked.cookies.clear()
    assert (await locked.get("/api/tasks", auth=("team", PASSWORD))).status_code == 401


async def test_signing_out_ends_the_session(locked):
    await sign_in(locked)
    response = await locked.post("/logout")
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert (await locked.get("/api/tasks")).status_code == 401


async def test_the_session_survives_a_restart_and_ends_when_the_password_changes(settings):
    async def tasks_status(password_now, cookie):
        app = create_app(settings.model_copy(update={"dashboard_password": password_now}))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        async with app.router.lifespan_context(app), client:
            client.cookies.set(auth.COOKIE_NAME, cookie)
            return (await client.get("/api/tasks")).status_code

    cookie = auth.session_token(PASSWORD)
    assert await tasks_status(PASSWORD, cookie) == 200
    assert await tasks_status("a-new-password", cookie) == 401


# --- demo data -------------------------------------------------------------------


async def test_demo_data_is_loaded_once_into_an_empty_database(db, settings):
    assert await seed_if_empty(db, settings) is True
    assert await seed_if_empty(db, settings) is False
    async with db.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Task)) == len(TASKS)
        assert await s.scalar(select(func.count()).select_from(User)) == len(TEAM)


async def test_demo_data_is_not_added_to_a_database_that_has_tasks(db, settings):
    async with db.session_factory() as s:
        s.add(Task(title="A real task"))
        await s.commit()
    assert await seed_if_empty(db, settings) is False
    async with db.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Task)) == 1


async def test_the_app_loads_demo_data_at_startup_only_when_asked(settings):
    for wanted, expected in ((True, len(TASKS)), (False, 0)):
        app = create_app(settings.model_copy(update={"seed_demo_data": wanted}))
        async with app.router.lifespan_context(app), app.state.db.session_factory() as s:
            assert await s.scalar(select(func.count()).select_from(Task)) == expected
