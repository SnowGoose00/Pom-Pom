import json
from dataclasses import replace
from types import SimpleNamespace

from app.config import Settings
from app.database import Chunk
from app.deepthink import NO_ANSWER_REPLY, DeepThinkEngine, DeepThinkResult


def _settings(tmp_path, **overrides) -> Settings:
    base = Settings(
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
        deep_think_max_steps=6,
        deep_think_timeout_seconds=180.0,
        deep_think_llm_timeout_seconds=60.0,
        deep_think_max_tool_chars=1200,
        deep_think_max_fetches=2,
        deep_think_fetch_enabled=True,
    )
    return replace(base, **overrides)


def _chunk(text, speaker="姬子", category="avatars", cid=1) -> Chunk:
    return Chunk(cid, category, "1003", speaker, text, speaker == "帕姆", "")


class ScriptedLLM:
    """OpenAI-compatible shell that replays a script of tool calls / contents."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else {"content": "（脚本用尽）"}
        if isinstance(item, Exception):
            raise item
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
        message = SimpleNamespace(content=item.get("content", ""), tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _engine(settings, llm, kb=None, web=None, fetch=None, samples=None, **kwargs):
    return DeepThinkEngine(
        settings,
        llm,
        kb_search=kb or (lambda query, k=5: []),
        web_search=web or (lambda query, max_results=5: []),
        fetch_page=fetch or (lambda url, max_chars=4000: ""),
        samples_fn=samples or (lambda n=5: []),
        **kwargs,
    )


def test_searches_knowledge_base_then_answers(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "姬子"})]},
            {"content": "姬子是星穹列车的领航员帕。"},
        ]
    )
    kb_calls = []

    def kb(query, k=5):
        kb_calls.append(query)
        return [_chunk("我是星穹列车的领航员姬子")]

    engine = _engine(settings, llm, kb=kb)
    result = engine.run("姬子是谁？", [])

    assert result.reply == "姬子是星穹列车的领航员帕。"
    assert result.rounds == 2
    assert result.tool_calls == 1
    assert result.evidence == 1
    assert result.insufficient is False
    assert result.fallback is False
    assert kb_calls == ["姬子"]
    step = result.steps[0]
    assert step["action"] == "search_knowledge_base"
    assert step["input"] == "姬子"
    assert step["hits"] == 1
    assert step["new_hits"] == 1
    assert llm.calls[0]["tool_choice"] == "auto"
    system = llm.calls[0]["messages"][0]
    assert system["role"] == "system"
    assert "【内部检索流程（开拓者看不到）】" in system["content"]
    tool_messages = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_messages and "领航员" in tool_messages[0]["content"]


def test_uses_web_search_and_records_titles(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_web", {"query": "银狼 配装"})]},
            {"content": "星间情报说银狼用废土客帕。"},
        ]
    )
    web_calls = []

    def web(query, max_results=5):
        web_calls.append(query)
        return [
            {
                "title": "银狼配装攻略",
                "url": "https://example.com/guide",
                "snippet": "遗器选废土客",
            }
        ]

    engine = _engine(settings, llm, web=web)
    result = engine.run("银狼怎么配装？", [])

    assert web_calls == ["银狼 配装"]
    assert result.steps[0]["action"] == "search_web"
    assert "银狼配装攻略" in result.steps[0]["observation"]
    assert "https://example.com/guide" in result.steps[0]["observation"]
    assert result.evidence == 1


def test_duplicate_tool_call_is_not_re_executed(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "姬子"})]},
            {"tool_calls": [("search_knowledge_base", {"query": "姬子"})]},
            {"content": "姬子是领航员帕。"},
        ]
    )
    kb_calls = []

    def kb(query, k=5):
        kb_calls.append(query)
        return [_chunk("我是星穹列车的领航员姬子")]

    engine = _engine(settings, llm, kb=kb)
    result = engine.run("姬子是谁？", [])

    assert kb_calls == ["姬子"]  # executed once only
    assert result.steps[1]["hits"] == 0
    assert "刚刚查过" in result.steps[1]["observation"]
    assert result.reply == "姬子是领航员帕。"


def test_max_steps_forces_final_answer_without_tools(tmp_path):
    settings = _settings(tmp_path, deep_think_max_steps=2)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "姬子"})]},
            {"tool_calls": [("search_knowledge_base", {"query": "帕姆"})]},
            {"content": "收尾答案帕。"},
        ]
    )
    evidences = {
        "姬子": [_chunk("姬子是领航员", cid=1)],
        "帕姆": [_chunk("帕姆是列车长", speaker="帕姆", cid=2)],
    }

    engine = _engine(settings, llm, kb=lambda query, k=5: evidences.get(query, []))
    result = engine.run("车上都有谁？", [])

    assert result.reply == "收尾答案帕。"
    assert result.rounds == 2
    assert len(llm.calls) == 3
    assert "tools" not in llm.calls[-1]
    assert result.fallback is False


def test_stall_detection_stops_after_two_rounds_without_new_evidence(tmp_path):
    settings = _settings(tmp_path, deep_think_max_steps=6)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "XX星"})]},
            {"tool_calls": [("search_knowledge_base", {"query": "XX星 剧情"})]},
            {"content": "智库里没有这段记录帕。"},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [])
    result = engine.run("XX星的剧情是怎样的？", [])

    assert len(result.steps) == 2
    assert len(llm.calls) == 3  # two search rounds + one finalize call
    assert result.insufficient is True
    assert result.reply == "智库里没有这段记录帕。"


def test_no_evidence_round_instructs_model_not_to_fabricate(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "XX星"})]},
            {"tool_calls": [("search_knowledge_base", {"query": "XX星 剧情"})]},
            {"content": "智库里没有这段记录帕。"},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [])
    engine.run("XX星的剧情是怎样的？", [])

    finalize_prompt = " ".join(
        str(message.get("content", "")) for message in llm.calls[-1]["messages"]
    )
    assert "不要编造" in finalize_prompt
    assert "游戏里根本没有" in finalize_prompt


def test_deterministic_no_answer_reply_when_final_call_fails(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "XX星"})]},
            {"tool_calls": [("search_knowledge_base", {"query": "XX星 剧情"})]},
            RuntimeError("api down"),
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [])
    result = engine.run("XX星的剧情是怎样的？", [])

    assert result.reply == NO_ANSWER_REPLY
    assert "智库" in result.reply
    assert result.fallback is True
    assert result.insufficient is True


def test_first_round_failure_returns_transient_fallback(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM([RuntimeError("api down")])

    engine = _engine(settings, llm)
    result = engine.run("你好", [])

    assert result.fallback is True
    assert result.insufficient is False
    assert "帕姆" in result.reply
    assert len(llm.calls) == 1


def test_run_stream_yields_steps_then_final(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "姬子"})]},
            {"content": "姬子是领航员帕。"},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [_chunk("姬子是领航员")])
    events = list(engine.run_stream("姬子是谁？", []))

    assert [event["type"] for event in events] == ["step", "final"]
    assert events[0]["action"] == "search_knowledge_base"
    assert isinstance(events[-1]["result"], DeepThinkResult)
    assert events[-1]["result"].reply == "姬子是领航员帕。"


def test_web_tools_hidden_when_web_search_disabled(tmp_path):
    settings = _settings(tmp_path, web_search_enabled=False)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_web", {"query": "银狼配装"})]},
            {"content": "帕姆这边联网关着呢帕。"},
        ]
    )
    web_calls = []

    def web(query, max_results=5):
        web_calls.append(query)
        return [{"title": "不该出现", "url": "https://x", "snippet": ""}]

    engine = _engine(settings, llm, web=web)
    result = engine.run("银狼怎么配装？", [])

    names = [spec["function"]["name"] for spec in engine.tool_specs()]
    assert names == ["search_knowledge_base"]
    assert web_calls == []
    assert result.steps[0]["hits"] == 0
    assert "没有开启" in result.steps[0]["observation"]


def test_fetch_page_respects_limit(tmp_path):
    settings = _settings(tmp_path, deep_think_max_fetches=1)
    llm = ScriptedLLM(
        [
            {
                "tool_calls": [
                    ("fetch_page", {"url": "https://example.com/1"}),
                    ("fetch_page", {"url": "https://example.com/2"}),
                ]
            },
            {"content": "看完了帕。"},
        ]
    )
    fetched = []

    def fetch(url, max_chars=4000):
        fetched.append(url)
        return "攻略正文"

    engine = _engine(settings, llm, fetch=fetch)
    result = engine.run("看看这篇攻略", [])

    assert fetched == ["https://example.com/1"]
    assert result.steps[0]["hits"] == 1
    assert "不再打开" in result.steps[1]["observation"]


def test_history_is_passed_into_deep_messages(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM([{"content": "好的帕。"}])
    history = [
        {"role": "user", "content": "前一轮"},
        {"role": "assistant", "content": "帕姆在哦"},
    ]

    engine = _engine(settings, llm)
    engine.run("再问一次", history)

    messages = llm.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "再问一次"


def test_expired_budget_skips_loop_and_still_answers(tmp_path):
    settings = _settings(tmp_path, deep_think_timeout_seconds=0.0)
    llm = ScriptedLLM([{"content": "开普勒星没有这段记录帕。"}])

    engine = _engine(settings, llm)
    result = engine.run("XX星的剧情是怎样的？", [])

    assert result.reply == "开普勒星没有这段记录帕。"
    assert len(llm.calls) == 1
    assert "tools" not in llm.calls[0]


class _Clock:
    """Virtual clock so budget behaviour is deterministic."""

    def __init__(self, start: float = 0.0):
        self.value = start

    def __call__(self) -> float:
        return self.value

    def tick(self, seconds: float) -> None:
        self.value += seconds


def test_budget_stops_further_tools_inside_one_round(tmp_path):
    """A round may ask for several tools; once the budget is gone the rest must not run."""
    settings = _settings(tmp_path, deep_think_timeout_seconds=10.0)
    llm = ScriptedLLM(
        [
            {
                "tool_calls": [
                    ("search_knowledge_base", {"query": "姬子"}),
                    ("search_web", {"query": "姬子 攻略"}),
                    ("fetch_page", {"url": "https://example.com"}),
                ]
            },
            {"content": "先说说帕姆知道的部分。"},
        ]
    )
    clock = _Clock()
    kb_calls, web_calls, fetched = [], [], []

    def kb(query, k=5):
        clock.tick(20)  # this single tool burns the whole budget
        kb_calls.append(query)
        return [_chunk("姬子是星穹列车的领航员")]

    def web(query, max_results=5):
        web_calls.append(query)
        return []

    def fetch(url, max_chars=4000):
        fetched.append(url)
        return ""

    engine = _engine(settings, llm, kb=kb, web=web, fetch=fetch, now=clock)
    result = engine.run("姬子是谁？", [])

    assert kb_calls == ["姬子"]
    assert web_calls == [] and fetched == [], "预算用完后不能再开新工具"
    assert len(result.steps) == 3, "每个工具调用都要有观察结果，否则模型会收到残缺的调用序列"
    assert "预算" in result.steps[1]["observation"]
    assert result.reply == "先说说帕姆知道的部分。"


def test_tool_failure_becomes_an_observation_instead_of_crashing(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "姬子"})]},
            {"content": "智库好像有点卡帕。"},
        ]
    )

    def boom(query, k=5):
        raise RuntimeError("db locked")

    engine = _engine(settings, llm, kb=boom)
    result = engine.run("姬子是谁？", [])

    assert result.steps[0]["hits"] == 0
    assert "出了点问题" in result.steps[0]["observation"]
    assert result.reply == "智库好像有点卡帕。"


def test_no_answer_flag_is_set_when_reply_reports_no_record(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "XX星"})]},
            {"content": "帕姆把智库翻了一圈，也没找到叫这个名字的记录帕。"},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [_chunk("雅利洛-VI 的雪原")])
    result = engine.run("XX星的剧情是怎样的？", [])

    assert result.no_answer is True
    assert result.insufficient is False  # hits existed, they just were not usable


def test_no_answer_flag_handles_a_named_missing_record(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "瑟兰星"})]},
            {
                "content": (
                    "列车长翻了翻智库，又翻了几遍……嗯，"
                    "没有「瑟兰星」这段记录帕。"
                )
            },
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [_chunk("雅利洛-VI 的雪原")])
    result = engine.run("瑟兰星的剧情是怎样的？", [])

    assert result.no_answer is True


def test_no_answer_flag_handles_archive_not_written_phrasing(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "瑟兰星"})]},
            {
                "content": (
                    "唔……帕姆把智库翻了好几遍，瑟兰星这三个字好像还没写进去帕。"
                )
            },
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [_chunk("雅利洛-VI 的雪原")])
    result = engine.run("瑟兰星的剧情是怎样的？", [])

    assert result.no_answer is True


def test_no_answer_flag_is_clear_for_grounded_answers(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "姬子"})]},
            {"content": "姬子是星穹列车的领航员帕。"},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [_chunk("姬子是领航员")])
    result = engine.run("姬子是谁？", [])

    assert result.no_answer is False


LEAKY_CONTENT = (
    '<tool_call>\n<invoke name="search_web">\n'
    '<parameter name="query" string="true">银狼LV999 配装</parameter>\n'
    "</invoke>\n</tool_call>"
)

# Real leak captured from DeepSeek: its tool calls use the "DSML" dialect where the
# delimiters are full-width vertical bars (U+FF5C), not the ASCII form above.
LT = chr(0x3C)
GT = chr(0x3E)
BAR = chr(0xFF5C)
DSML = f"{BAR}{BAR}DSML{BAR}{BAR}"
DSML_LEAK = (
    f"{LT}{DSML} calls{GT}\n"
    f'{LT}{DSML} invoke name="search_web"{GT}\n'
    f'{LT}{DSML} parameter name="query" string="true"{GT}'
    f"银狼LV.999 遗器 光锥 攻略{LT}/{DSML} parameter{GT}\n"
    f"{LT}/{DSML} invoke{GT}\n"
    f"{LT}/{DSML} calls{GT}"
)


def test_dsml_tool_call_markup_is_detected_and_stripped():
    from app.deepthink import has_tool_markup, strip_tool_markup

    assert has_tool_markup(DSML_LEAK) is True
    assert strip_tool_markup(DSML_LEAK) == ""
    assert has_tool_markup("帕姆今天不查了帕。") is False
    assert has_tool_markup(LEAKY_CONTENT) is True

    mixed = "帕姆再去查查帕。\n" + DSML_LEAK
    assert has_tool_markup(mixed) is True
    assert strip_tool_markup(mixed) == "帕姆再去查查帕。"


def test_clean_reply_drops_stray_foreign_script_words():
    from app.deepthink import clean_reply

    # 模型偶尔会崩出一两个西里尔字母词，中文回答里不该出现
    assert clean_reply("要不要列车长再帮你去问问 подробности？") == "要不要列车长再帮你去问问？"
    assert clean_reply("帕姆去查查帕。") == "帕姆去查查帕。"


def test_clean_reply_keeps_a_few_community_nicknames():
    from app.deepthink import clean_reply

    # 别称现在允许少量保留（人设里限制用量，不再做强制替换）
    assert clean_reply("哦，是狼尊帕！") == "哦，是狼尊帕！"
    assert clean_reply("光锥按专属光锥优先。") == "光锥按专属光锥优先。"


def test_dsml_reply_is_never_returned_to_user(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "银狼"})]},
            {"content": DSML_LEAK},
            {"content": "帕姆没找到 LV.999 的配装情报帕。"},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [_chunk("银狼LV.999 角色信息")])
    result = engine.run("银狼lv999什么配装", [])

    assert result.reply == "帕姆没找到 LV.999 的配装情报帕。"
    assert "DSML" not in result.reply
    assert "invoke" not in result.reply
    assert result.text_tool_calls == 1


def test_dsml_leak_in_finalize_falls_back_to_persona_reply(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "a"})]},
            {"tool_calls": [("search_knowledge_base", {"query": "b"})]},
            {"content": DSML_LEAK},
            {"content": DSML_LEAK},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [])
    result = engine.run("银狼lv999什么配装", [])

    assert result.reply == NO_ANSWER_REPLY
    assert "DSML" not in result.reply
    assert result.fallback is True


def test_text_form_tool_call_in_content_is_never_the_answer(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "银狼"})]},
            {"content": LEAKY_CONTENT},
            {"content": "帕姆查了一圈，LV999 的配装情报还没找到帕。"},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [_chunk("银狼LV.999 角色信息")])
    result = engine.run("银狼lv999什么配装", [])

    assert result.reply == "帕姆查了一圈，LV999 的配装情报还没找到帕。"
    assert "<" not in result.reply
    assert result.text_tool_calls == 1
    nudged = any(
        message.get("role") == "system"
        and "不要用文字写出工具调用" in str(message.get("content", ""))
        for message in llm.calls[-1]["messages"]
    )
    assert nudged


def test_finalize_never_returns_tool_markup(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "a"})]},
            {"tool_calls": [("search_knowledge_base", {"query": "b"})]},
            {"content": LEAKY_CONTENT},
            {"content": "智库里没有这段记录帕。"},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [])
    result = engine.run("银狼lv999什么配装", [])

    assert result.reply == "智库里没有这段记录帕。"
    assert "<" not in result.reply
    assert result.fallback is False
    assert result.text_tool_calls == 1


def test_finalize_falls_back_when_model_keeps_leaking_markup(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "a"})]},
            {"tool_calls": [("search_knowledge_base", {"query": "b"})]},
            {"content": LEAKY_CONTENT},
            {"content": LEAKY_CONTENT},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [])
    result = engine.run("银狼lv999什么配装", [])

    assert result.reply == NO_ANSWER_REPLY
    assert "<" not in result.reply
    assert result.fallback is True
    assert result.text_tool_calls == 2


def test_strip_tool_markup_removes_markup_and_keeps_prose():
    from app.deepthink import has_tool_markup, strip_tool_markup

    mixed = "帕姆再去查一下帕。\n" + LEAKY_CONTENT + "\n查完就告诉开拓者。"
    assert has_tool_markup(mixed) is True
    cleaned = strip_tool_markup(mixed)
    assert "帕姆再去查一下帕。" in cleaned
    assert "查完就告诉开拓者。" in cleaned
    assert "tool_call" not in cleaned
    assert "invoke" not in cleaned
    assert has_tool_markup("帕姆今天不查了帕。") is False


MARKDOWN_ANSWER = (
    "**主词条推荐：**\n"
    "- **躯干**：效果命中\n"
    "- **脚部**：速度\n"
    "\n"
    "## 补充\n"
    "1. 效果命中堆到七八十即可\n"
    "\n"
    "具体数值视情况而定，仅供参考。"
)


def test_strip_markdown_fluff_flattens_report_formatting():
    from app.deepthink import strip_markdown_fluff

    cleaned = strip_markdown_fluff(MARKDOWN_ANSWER)

    assert "**" not in cleaned
    assert "#" not in cleaned
    assert "- " not in cleaned
    assert "1. " not in cleaned
    assert "主词条推荐：" in cleaned
    assert "躯干：效果命中" in cleaned
    assert "效果命中堆到七八十即可" in cleaned


def test_deep_reply_is_flattened_to_chat_text(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "银狼"})]},
            {"content": MARKDOWN_ANSWER},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [_chunk("银狼是量子虚无角色")])
    result = engine.run("银狼的遗器主词条怎么选？", [])

    assert "**" not in result.reply
    assert "\n- " not in result.reply
    assert "\n# " not in result.reply


def test_voice_reminder_is_sent_before_the_final_answer(tmp_path):
    settings = _settings(tmp_path)
    llm = ScriptedLLM(
        [
            {"tool_calls": [("search_knowledge_base", {"query": "银狼"})]},
            {"content": "银狼的遗器帕姆帮你看过啦，主词条就按效果命中来帕！"},
        ]
    )

    engine = _engine(settings, llm, kb=lambda query, k=5: [_chunk("银狼是量子虚无角色")])
    engine.run("银狼的遗器主词条怎么选？", [])

    answering_messages = llm.calls[-1]["messages"]
    reminder = answering_messages[-1]
    assert reminder["role"] == "system"
    assert "直接说给开拓者乘客听" in reminder["content"]
    assert "项目符号" in reminder["content"]
