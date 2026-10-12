"""What a public deployment needs: a password on the dashboard, and demo data that restores itself."""

import base64

import httpx
import pytest
from sqlalchemy import func, select

from app.demo import TASKS, TEAM, seed_if_empty
from app.main import create_app
from app.models import Task, User


def basic(password: str, user: str = "team") -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
async def locked(settings):
    app = create_app(settings.model_copy(update={"dashboard_password": "open-sesame"}))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    async with app.router.lifespan_context(app), client:
        yield client


async def test_without_a_password_set_everything_is_open_as_before(client):
    assert (await client.get("/api/tasks")).status_code == 200


async def test_a_locked_dashboard_asks_the_browser_for_a_password(locked):
    for path in ("/", "/api/tasks", "/api/runs", "/static/js/main.js", "/docs"):
        response = await locked.get(path)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"].startswith("Basic")


async def test_the_assistant_cannot_be_used_without_the_password(locked):
    response = await locked.post("/api/chat", json={"user_id": 1, "text": "hi"})
    assert response.status_code == 401


async def test_the_right_password_lets_you_in_and_the_wrong_one_does_not(locked):
    assert (await locked.get("/api/tasks", headers=basic("open-sesame"))).status_code == 200
    assert (await locked.get("/", headers=basic("open-sesame"))).status_code == 200
    assert (await locked.get("/api/tasks", headers=basic("guess"))).status_code == 401
    assert (await locked.get("/api/tasks", headers={"Authorization": "Basic not-base64!"})).status_code == 401
    assert (await locked.get("/api/tasks", headers={"Authorization": "Bearer open-sesame"})).status_code == 401


async def test_the_health_check_stays_open_so_the_host_can_see_the_app_is_up(locked):
    assert (await locked.get("/health")).status_code == 200


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
