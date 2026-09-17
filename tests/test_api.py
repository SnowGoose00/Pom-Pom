import json

from fastapi.testclient import TestClient

from app.main import create_app


class StubEngine:
    def __init__(self):
        self.mode = None

    def reply(self, message, history, mode="normal"):
        self.mode = mode
        return "本帕姆在哦！"

    def stream_reply(self, message, history, mode="normal"):
        self.mode = mode
        yield {
            "type": "step",
            "round": 1,
            "action": "search_knowledge_base",
            "input": "姬子",
            "hits": 2,
            "new_hits": 2,
            "observation": "【角色信息】姬子：领航员",
        }
        yield {
            "type": "reply",
            "reply": "姬子是领航员帕。",
            "mode": mode,
            "fallback": False,
            "insufficient": False,
            "rounds": 2,
            "steps": [{"action": "search_knowledge_base"}],
        }

    def last_diagnostics(self):
        return {
            "question": "姬子是谁？",
            "model": "test-model",
            "elapsed_ms": 12,
            "fallback": False,
            "mode": self.mode,
            "steps": [{"action": "search_knowledge_base", "input": "姬子"}],
            "llm_rounds": 2,
            "insufficient": False,
        }


def _sse_events(body: str) -> list[dict]:
    events = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def test_chat_endpoint_returns_reply():
    app = create_app()
    app.state.engine = StubEngine()
    client = TestClient(app)
    resp = client.post("/api/chat", json={"message": "你好", "history": []})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "本帕姆在哦！"


def test_chat_endpoint_requires_message():
    app = create_app()
    app.state.engine = StubEngine()
    client = TestClient(app)
    resp = client.post("/api/chat", json={"history": []})
    assert resp.status_code == 422


def test_health_endpoint():
    app = create_app()
    app.state.db_chunks = 123
    app.state.embedder_ready = True
    app.state.model_configured = True
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["db_chunks"] == 123
    assert data["embedder_ready"] is True


def test_debug_endpoints_require_debug_mode():
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/debug/info").status_code == 404
    assert client.get("/api/debug/logs").status_code == 404


def test_debug_endpoints_return_useful_info_when_enabled():
    app = create_app()
    app.state.debug_enabled = True
    app.state.engine = StubEngine()
    app.state.record_debug(
        {
            "question": "测试问题",
            "model": "test-model",
            "elapsed_ms": 123,
            "fallback": False,
            "searches": 2,
            "retrieved": [{"category": "story", "speaker": "帕姆", "text": "列车要出发了"}],
            "web_titles": [],
        }
    )
    client = TestClient(app)
    info = client.get("/api/debug/info")
    assert info.status_code == 200
    assert "model" in info.json()
    logs = client.get("/api/debug/logs")
    assert logs.status_code == 200
    assert logs.json()[-1]["question"] == "测试问题"


def test_debug_record_with_unicode_bullet_does_not_crash():
    app = create_app()
    app.state.debug_enabled = True
    app.state.record_debug(
        {
            "question": "姬子目前最佳队友是谁？",
            "reply_len": 20,
            "fallback": False,
            "retrieved": [{"category": "items", "speaker": "", "text": "物品「开拓者•欢愉」"}],
        }
    )
    client = TestClient(app)
    logs = client.get("/api/debug/logs")
    assert logs.status_code == 200
    assert "欢愉" in logs.json()[-1]["retrieved"][0]["text"]


def test_chat_passes_mode_and_returns_steps():
    app = create_app()
    app.state.engine = StubEngine()
    client = TestClient(app)
    resp = client.post(
        "/api/chat",
        json={"message": "姬子是谁？", "history": [], "mode": "deep"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "本帕姆在哦！"
    assert data["mode"] == "deep"
    assert data["steps"][0]["action"] == "search_knowledge_base"


def test_chat_defaults_to_normal_mode():
    app = create_app()
    engine = StubEngine()
    app.state.engine = engine
    client = TestClient(app)
    resp = client.post("/api/chat", json={"message": "你好", "history": []})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "normal"
    assert engine.mode == "normal"


def test_chat_rejects_unknown_mode():
    app = create_app()
    app.state.engine = StubEngine()
    client = TestClient(app)
    resp = client.post(
        "/api/chat",
        json={"message": "你好", "history": [], "mode": "turbo"},
    )
    assert resp.status_code == 422


def test_chat_stream_emits_step_reply_done():
    app = create_app()
    app.state.engine = StubEngine()
    client = TestClient(app)
    resp = client.post(
        "/api/chat/stream",
        json={"message": "姬子是谁？", "history": [], "mode": "deep"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp.text)
    assert [event["type"] for event in events] == ["step", "reply", "done"]
    assert events[0]["action"] == "search_knowledge_base"
    assert events[1]["reply"] == "姬子是领航员帕。"


def test_chat_stream_returns_503_without_engine():
    app = create_app()
    app.state.engine = None
    client = TestClient(app)
    resp = client.post(
        "/api/chat/stream",
        json={"message": "你好", "history": [], "mode": "deep"},
    )
    assert resp.status_code == 503


def test_debug_info_includes_deep_think_settings():
    app = create_app()
    app.state.debug_enabled = True
    client = TestClient(app)
    data = client.get("/api/debug/info").json()
    assert data["deep_think_max_steps"] >= 1
    assert data["deep_think_timeout_seconds"] > 0
