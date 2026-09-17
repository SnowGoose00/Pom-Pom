import json
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

from app.chat import ChatEngine
from app.config import Settings
from app.database import Chunk, VectorDB


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.chat = FakeLLM._Chat(self)

    class _Chat:
        def __init__(self, owner):
            self.owner = owner
            self.completions = FakeLLM._Completions(owner)

    class _Completions:
        def __init__(self, owner):
            self.owner = owner

        def create(self, **kwargs):
            return self.owner._create(**kwargs)

    def _create(self, **kwargs):
        self.calls.append(kwargs)

        class Msg:
            content = "本帕姆知道哦，星穹列车去过雅利洛-VI！"

        class Choice:
            message = Msg()

        class Resp:
            choices = [Choice()]

        return Resp()


class FakeEmbedder:
    def __init__(self, dim=4):
        self.dim = dim

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        db_path=str(tmp_path / "t.db"),
        data_dir="",
        dialogues_dir="",
        model_cache_dir="",
        top_k=6,
        history_turns=20,
        temperature=0.7,
    )


def test_reply_returns_llm_output_and_trims_history(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [Chunk(0, "story", "s1", "姬子", "星穹列车去过雅利洛-VI", False, "")],
        [[0.2, 0.2, 0.2, 0.2]],
    )
    llm = FakeLLM()
    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=llm)

    history = [{"role": "user", "content": "前一轮"} for _ in range(25)]
    reply = engine.reply("列车去过哪些星球？", history)

    assert "雅利洛-VI" in reply
    last_call = llm.calls[-1]
    messages = last_call["messages"]
    assert messages[0]["role"] == "system"
    assert "雅利洛-VI" in messages[0]["content"]
    assert len([m for m in messages if m["role"] == "user"]) <= 21  # 20 turns + current
    assert last_call["model"] == "test-model"
    assert last_call["timeout"] == 120
    db.close()


def test_fallback_when_llm_raises(tmp_path):
    class BoomLLM:
        def __init__(self):
            self.chat = FakeLLM._Chat(self)

        def _create(self, **kwargs):
            raise RuntimeError("api timeout")

    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=BoomLLM())
    reply = engine.reply("你好", [])
    assert "帕姆" in reply or "智库" in reply
    assert "timeout" not in reply.lower() and "RuntimeError" not in reply
    diag = engine.last_diagnostics()
    assert "last_error" in diag
    assert "RuntimeError" in diag["last_error"]
    db.close()


def test_missing_api_key_falls_back(tmp_path):
    settings = replace(_settings(tmp_path), api_key="")
    db = VectorDB(tmp_path / "t.db", dim=4)
    engine = ChatEngine(settings, db, FakeEmbedder())
    reply = engine.reply("你好", [])
    assert "api" not in reply.lower()
    db.close()


def test_retrieve_targets_speaker_when_question_mentions_avatar(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "avatars", "1003", "姬子", "我是星穹列车的领航员姬子", False, ""),
            Chunk(1, "avatars", "1005", "丹恒", "资料库管理员丹恒", False, ""),
        ],
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
    )
    original_search = db.search_speaker
    calls = []

    def counting(*args, **kwargs):
        calls.append(args[1] if len(args) > 1 else kwargs.get("speaker"))
        return original_search(*args, **kwargs)

    db.search_speaker = counting
    engine = ChatEngine(settings, db, FakeEmbedder())
    engine._retrieve("姬子是领航员吗？")
    assert any(name == "姬子" for name in calls)
    db.close()


def test_retrieve_excludes_player_joke_lines(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "messages", "m1", "Player", "她是个性格洒脱的摇滚明星", False, ""),
            Chunk(1, "avatars", "1003", "姬子", "我是星穹列车的领航员姬子", False, ""),
        ],
        [[0.2, 0.2, 0.2, 0.2], [0.2, 0.2, 0.2, 0.2]],
    )
    engine = ChatEngine(settings, db, FakeEmbedder())
    chunks = engine._retrieve("姬子是领航员吗？")
    assert not any(c.speaker == "Player" for c in chunks)
    assert any(c.speaker == "姬子" for c in chunks)
    db.close()


def test_retrieve_adds_literal_entity_hits(tmp_path):
    settings = replace(_settings(tmp_path), top_k=1)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "rogue", "s1", "未知", "差分宇宙：液金长河缓缓流动。", False, ""),
            Chunk(1, "rogue", "s2", "黑塔", "黑塔：鲁珀特二世掀起了第一次帝皇战争。", False, ""),
        ],
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
    )
    engine = ChatEngine(settings, db, FakeEmbedder())

    chunks = engine._retrieve("差分宇宙里的鲁珀特是什么？")

    # top_k=1 的向量检索只会拿到一条，字面实体检索必须把「鲁珀特」那条补回来
    assert any("鲁珀特" in chunk.text for chunk in chunks)
    db.close()


def test_retrieve_spreads_chunks_across_scenes(tmp_path):
    settings = replace(_settings(tmp_path), top_k=6)
    db = VectorDB(tmp_path / "t.db", dim=4)
    chunks = [
        Chunk(i, "story", "same_scene", "帕姆", f"同一场景的第 {i} 句", True, "")
        for i in range(6)
    ]
    chunks += [
        Chunk(10, "story", "other_a", "黑塔", "另一个场景的内容", False, ""),
        Chunk(11, "books", "other_b", "", "书籍里的内容", False, ""),
    ]
    db.insert_chunks(chunks, [[1.0, 0.0, 0.0, 0.0] for _ in chunks])
    engine = ChatEngine(settings, db, FakeEmbedder())

    result = engine._retrieve("姬子是领航员吗？")

    same_scene = [chunk for chunk in result if chunk.scene == "same_scene"]
    assert len(result) <= 6
    assert len(same_scene) <= 3  # 单场景不超过结果的一半（k=6）
    db.close()


def test_retrieve_brings_neighbouring_chunks_for_context(tmp_path):
    settings = replace(_settings(tmp_path), top_k=1)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "story", "s1", "帕姆", "阿基维利？：谁？", True, ""),
            Chunk(1, "story", "s1", "帕姆", "帕姆：那个把列车炸成两截的家伙帕。", True, ""),
        ],
        [[1.0, 0.0, 0.0, 0.0], [0.9, 0.1, 0.0, 0.0]],
    )
    engine = ChatEngine(settings, db, FakeEmbedder())

    chunks = engine._retrieve("那个家伙是谁？")

    # 命中的第一条会把它同场景的邻居一起带出来，供指代消解
    assert any("两截" in chunk.text for chunk in chunks)
    db.close()


def test_literal_hits_need_semantic_relevance(tmp_path):
    settings = replace(_settings(tmp_path), top_k=4)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "rogue", "s0", "黑塔", "鲁珀特二世掀起了第一次帝皇战争。", False, ""),
            # 含实体词，但向量与问题正交（语义无关）
            Chunk(1, "items", "s1", "", "物品「鲁珀特」：鲁珀特。", False, ""),
        ],
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
    )
    engine = ChatEngine(settings, db, FakeEmbedder())

    kept = engine._gated_literal_hits("鲁珀特", [1.0, 0.0, 0.0, 0.0])

    assert [chunk.id for chunk, _ in kept] == [0]  # 正交那条被拦掉
    db.close()


def test_retrieve_harvests_destination_broadcasts_for_travel_questions(tmp_path):
    settings = replace(_settings(tmp_path), top_k=1)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "story", "s1", "帕姆", "列车此行的目的地是「盛会之星」匹诺康尼。", True, ""),
            Chunk(1, "story", "s2", "帕姆", "准备好了就出发吧，期待各位的好消息帕。", True, ""),
            Chunk(2, "books", "b1", "", "一本与旅程无关的书籍。", False, ""),
        ],
        [[0.5, 0, 0, 0], [0.5, 0, 0, 0], [0.1, 0.1, 0.1, 0.1]],
    )
    engine = ChatEngine(settings, db, FakeEmbedder())
    chunks = engine._retrieve("星穹列车曾经去过哪些世界？")
    assert any("目的地" in c.text for c in chunks)
    assert len(chunks) >= 2
    db.close()


def test_self_intro_retrieval_prefers_pam_lines_and_filters_junk(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "story", "s1", "？？？", "你是谁？你是谁？", False, ""),
            Chunk(1, "story", "s2", "未知", "你…是谁？", False, ""),
            Chunk(2, "story", "s3", "帕姆", "帕姆是星穹列车的列车长，负责报站和照顾乘客。", True, ""),
        ],
        [[0.3, 0.3, 0.3, 0.3], [0.3, 0.3, 0.3, 0.3], [0.3, 0.3, 0.3, 0.3]],
    )
    engine = ChatEngine(settings, db, FakeEmbedder())
    chunks = engine._retrieve("你好，你是谁？")
    speakers = [c.speaker for c in chunks]
    assert "？？？" not in speakers and "未知" not in speakers
    assert "帕姆" in speakers
    assert "列车长" in chunks[0].text  # identity lines rank first
    db.close()


def test_reply_retries_transient_llm_failures(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)

    class FlakyLLM(FakeLLM):
        def __init__(self):
            super().__init__()
            self.failures_left = 2

        def _create(self, **kwargs):
            if self.failures_left > 0:
                self.failures_left -= 1
                raise RuntimeError("transient")
            return super()._create(**kwargs)

    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=FlakyLLM())
    reply = engine.reply("你好", [])
    assert "雅利洛" in reply  # came from successful third attempt
    db.close()


def test_build_question_triggers_web_search(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    calls = []

    def fake_web(query):
        calls.append(query)
        return [
            {
                "title": "银狼配装攻略",
                "url": "https://example.com/guide",
                "snippet": "推荐遗器选效果命中与速度。",
            }
        ]

    llm = FakeLLM()
    engine = ChatEngine(
        settings, db, FakeEmbedder(), llm_client=llm, web_search_fn=fake_web
    )
    reply = engine.reply("银狼如何配装？", [])
    assert "攻略" in reply or "雅利洛" in reply  # answered via FakeLLM
    assert calls == ["银狼如何配装？ 崩坏星穹铁道 攻略 推荐"]
    system = llm.calls[-1]["messages"][0]["content"]
    assert "星间攻略情报" in system
    assert "https://example.com/guide" in system
    db.close()


def test_reply_records_debug_diagnostics(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    llm = FakeLLM()
    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=llm)
    engine.reply("你好，你是谁？", [])
    diag = engine.last_diagnostics()
    assert diag["question"] == "你好，你是谁？"
    assert diag["model"] == "test-model"
    assert "elapsed_ms" in diag
    assert "fallback" in diag
    assert isinstance(diag["searches"], int)
    assert isinstance(diag["retrieved"], list)
    db.close()


def test_plot_question_does_not_trigger_web_search(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    calls = []

    def fake_web(query):
        calls.append(query)
        return []

    llm = FakeLLM()
    engine = ChatEngine(
        settings, db, FakeEmbedder(), llm_client=llm, web_search_fn=fake_web
    )
    engine.reply("黑塔空间站是谁建造的？", [])
    assert calls == []
    db.close()


class ScriptedLLM:
    """Replays a script of tool calls / final contents, OpenAI-compatible shell."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else {"content": "（脚本用尽）"}
        tool_calls = None
        if item.get("tool_calls"):
            tool_calls = [
                SimpleNamespace(
                    id=f"call_{index}",
                    type="function",
                    function=SimpleNamespace(
                        name=name, arguments=json.dumps(args, ensure_ascii=False)
                    ),
                )
                for index, (name, args) in enumerate(item["tool_calls"])
            ]
        message = SimpleNamespace(
            content=item.get("content", ""), tool_calls=tool_calls
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _deep_engine(tmp_path, llm, **kwargs):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(
                0,
                "avatars",
                "1003",
                "姬子",
                "我是星穹列车的领航员姬子",
                False,
                "",
            )
        ],
        [[0.2, 0.2, 0.2, 0.2]],
    )
    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=llm, **kwargs)
    return engine, db


def test_deep_mode_runs_tool_loop_and_reports_steps(tmp_path):
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "姬子"})]},
            {"content": "姬子是星穹列车的领航员帕。"},
        ]
    )
    engine, db = _deep_engine(tmp_path, llm)

    events = list(engine.stream_reply("姬子是谁？", [], mode="deep"))

    assert events[0]["type"] == "step"
    assert events[0]["action"] == "search_knowledge_base"
    assert events[0]["input"] == "姬子"
    assert events[-1]["type"] == "reply"
    assert events[-1]["reply"] == "姬子是星穹列车的领航员帕。"
    assert events[-1]["mode"] == "deep"
    assert events[-1]["insufficient"] is False
    diag = engine.last_diagnostics()
    assert diag["mode"] == "deep"
    assert diag["llm_rounds"] == 2
    assert diag["steps"][0]["action"] == "search_knowledge_base"
    assert diag["fallback"] is False
    db.close()


def test_deep_mode_uses_injected_web_search(tmp_path):
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_web", {"query": "银狼 配装"})]},
            {"content": "据星间情报，银狼用废土客帕。"},
        ]
    )
    queries = []

    def fake_web(query):
        queries.append(query)
        return [
            {
                "title": "银狼配装攻略",
                "url": "https://example.com/guide",
                "snippet": "废土客",
            }
        ]

    engine, db = _deep_engine(tmp_path, llm, web_search_fn=fake_web)
    events = list(engine.stream_reply("银狼怎么配装？", [], mode="deep"))

    assert queries == ["银狼 配装"]
    assert events[0]["action"] == "search_web"
    assert events[-1]["reply"] == "据星间情报，银狼用废土客帕。"
    db.close()


def test_deep_mode_web_search_reports_the_source(tmp_path, monkeypatch):
    import app.websearch as app_websearch

    app_websearch.reset_search_state()
    # keep the test hermetic: no real Bocha call even when .env carries a key
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    monkeypatch.setitem(
        app_websearch._SOURCE_FUNCTIONS,
        "duckduckgo",
        lambda query, max_results=5: [
            {
                "title": "银狼遗器词条选择推荐",
                "url": "https://ol.3dmgame.com/gl/234696.html",
                "snippet": "躯干效果命中，脚部速度",
            }
        ],
    )
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_web", {"query": "银狼 遗器"})]},
            {"content": "银狼的主词条帕姆看过啦，躯干效果命中、脚部速度帕。"},
        ]
    )
    engine, db = _deep_engine(tmp_path, llm)

    events = list(engine.stream_reply("银狼的遗器主词条怎么选？", [], mode="deep"))

    step = events[0]
    assert step["action"] == "search_web"
    assert step["source"] == "duckduckgo"
    assert step["hits"] == 1
    tool_message = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "3dmgame" in tool_message["content"]
    assert "躯干效果命中" in tool_message["content"]
    db.close()


def test_normal_mode_stream_reply_yields_single_reply_event(tmp_path):
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    llm = FakeLLM()
    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=llm)

    events = list(engine.stream_reply("你好", []))

    assert [event["type"] for event in events] == ["reply"]
    assert "雅利洛-VI" in events[0]["reply"]
    assert events[0]["mode"] == "normal"
    assert engine.last_diagnostics()["mode"] == "normal"
    db.close()


def test_deep_mode_without_api_key_uses_persona_fallback(tmp_path):
    settings = replace(_settings(tmp_path), api_key="")
    db = VectorDB(tmp_path / "t.db", dim=4)
    engine = ChatEngine(settings, db, FakeEmbedder())

    events = list(engine.stream_reply("你好", [], mode="deep"))

    assert [event["type"] for event in events] == ["reply"]
    assert "帕姆" in events[0]["reply"]
    assert engine.last_diagnostics()["fallback"] is True
    db.close()


def test_deep_mode_never_streams_tool_markup(tmp_path):
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "银狼"})]},
            {
                "content": (
                    '<tool_call><invoke name="search_web">'
                    '<parameter name="query">银狼LV999 配装</parameter>'
                    "</invoke></tool_call>"
                )
            },
            {"content": "帕姆没找到这份配装情报帕。"},
        ]
    )
    engine, db = _deep_engine(tmp_path, llm)

    events = list(engine.stream_reply("银狼lv999什么配装", [], mode="deep"))

    reply = events[-1]["reply"]
    assert reply == "帕姆没找到这份配装情报帕。"
    assert "<" not in reply and "tool_call" not in reply
    assert engine.last_diagnostics()["text_tool_calls"] == 1
    db.close()


def test_normal_mode_never_returns_tool_markup(tmp_path):
    bar = chr(0xFF5C)
    leak = (
        f"<{bar}{bar}DSML{bar}{bar} calls>\n"
        f'<{bar}{bar}DSML{bar}{bar} invoke name="search_web">\n'
        f"</{bar}{bar}DSML{bar}{bar} invoke>\n"
        f"</{bar}{bar}DSML{bar}{bar} calls>"
    )
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    # first script entry may be consumed by the retrieval-reflection call
    llm = ScriptedLLM([{"content": leak}, {"content": leak}])
    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=llm)

    reply = engine.reply("你好", [])

    assert "DSML" not in reply
    assert "invoke" not in reply
    assert "帕姆" in reply  # persona fallback instead of raw markup
    assert engine.last_diagnostics()["fallback"] is True
    db.close()


def test_normal_mode_flattens_markdown_report_formatting(tmp_path):
    report = (
        "**主词条推荐：**\n"
        "- **躯干**：效果命中\n"
        "- 脚部：速度\n"
        "\n"
        "具体数值视情况而定。"
    )
    settings = _settings(tmp_path)
    db = VectorDB(tmp_path / "t.db", dim=4)
    # first entry may be consumed by the retrieval-reflection call
    llm = ScriptedLLM([{"content": report}, {"content": report}])
    engine = ChatEngine(settings, db, FakeEmbedder(), llm_client=llm)

    reply = engine.reply("银狼的遗器主词条怎么选？", [])

    assert "**" not in reply
    assert "\n- " not in reply
    assert "主词条推荐：" in reply
    assert "躯干：效果命中" in reply
    db.close()
