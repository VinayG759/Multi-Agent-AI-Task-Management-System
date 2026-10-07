from datetime import UTC, datetime, timedelta

from app.models import User


async def _add_user(app, name="Alice"):
    async with app.state.db.session_factory() as s:
        user = User(name=name)
        s.add(user)
        await s.commit()
        return user.id


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_create_then_fetch_with_history(client):
    created = (await client.post("/api/tasks", json={"title": "  Deploy frontend  ", "priority": "high"})).json()
    assert created["title"] == "Deploy frontend" and created["status"] == "todo"
    fetched = (await client.get(f"/api/tasks/{created['id']}")).json()
    assert fetched["priority"] == "high"
    assert [e["action"] for e in fetched["events"]] == ["created"]


async def test_complete_and_undo_through_patch(client):
    task_id = (await client.post("/api/tasks", json={"title": "Ship"})).json()["id"]
    done = (await client.patch(f"/api/tasks/{task_id}", json={"status": "done"})).json()
    assert done["status"] == "done" and done["completed_at"] is not None
    undone = (await client.patch(f"/api/tasks/{task_id}", json={"status": "todo"})).json()
    assert undone["status"] == "todo" and undone["completed_at"] is None


async def test_cancel_and_restore(client):
    task_id = (await client.post("/api/tasks", json={"title": "Maybe"})).json()["id"]
    assert (await client.patch(f"/api/tasks/{task_id}", json={"status": "cancelled"})).json()["status"] == "cancelled"
    assert (await client.patch(f"/api/tasks/{task_id}", json={"status": "todo"})).json()["status"] == "todo"


async def test_unknown_task_is_404(client):
    assert (await client.get("/api/tasks/999")).status_code == 404
    assert (await client.patch("/api/tasks/999", json={"title": "x"})).status_code == 404


async def test_validation_errors_name_the_field(client):
    cases = [
        ({"title": "ok", "priority": "sky-high"}, "priority"),
        ({"title": "   "}, "title"),
        ({"title": "x" * 201}, "title"),
        ({"title": "ok", "description": "d" * 2001}, "description"),
    ]
    for body, field in cases:
        response = await client.post("/api/tasks", json=body)
        assert response.status_code == 422, body
        assert response.json()["detail"][0]["loc"][-1] == field


async def test_filters_and_total(client, app):
    alice = await _add_user(app)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    for i in range(3):
        await client.post("/api/tasks", json={"title": f"late {i}", "assignee_id": alice, "due_at": past})
    await client.post("/api/tasks", json={"title": "late, not hers", "due_at": past})
    await client.post("/api/tasks", json={"title": "undated", "assignee_id": alice})

    body = (await client.get("/api/tasks", params={"overdue": "true", "assignee_id": alice, "limit": 2})).json()
    assert body["total"] == 3 and len(body["items"]) == 2
    assert all(item["overdue"] and item["assignee"]["name"] == "Alice" for item in body["items"])

    by_status = (await client.get("/api/tasks", params=[("status", "todo"), ("status", "done")])).json()
    assert by_status["total"] == 5


async def test_users_and_stats(client, app):
    alice = await _add_user(app)
    await client.post("/api/tasks", json={"title": "one", "assignee_id": alice})
    assert [u["name"] for u in (await client.get("/api/users")).json()] == ["Alice"]
    stats = (await client.get("/api/stats", params={"user_id": alice})).json()
    assert stats["open"] == 1 and stats["by_status"]["todo"] == 1
