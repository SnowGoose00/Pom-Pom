"""ReAct-style deep thinking: query tools repeatedly until the answer is grounded."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from app.config import Settings
from app.database import Chunk
from app.persona import build_deep_think_prompt, format_context
from app.retrieval import alias_hint
from app.websearch import SOURCE_LABELS


FALLBACK_REPLY = (
    "呜……列车智库好像有点卡，帕姆没听清开拓者的问题。"
    "稍等一下下，再问一次好不好？"
)

NO_ANSWER_REPLY = (
    "呜……帕姆把列车智库翻了个遍，也没找到跟这个问题有关的记录帕。"
    "开拓者要不要换个问法，或者等智库更新之后再问问看？"
)

_NO_EVIDENCE_INSTRUCTION = (
    "你刚才的查询都没有结果。请如实告知开拓者：智库里没有这段记录，不要编造，"
    "也不要说游戏里根本没有；如果联网查到了相关讨论，就说明智库还没更新到这段，"
    "并注明仅供参考。"
)

# Ways Pom-Pom honestly reports that her archive has no record (the name of the
# missing thing usually sits between "没有" and "记录").
_NO_ANSWER_PATTERNS = (
    re.compile(r"没有[^。！？\n]{0,16}(记录|记载|资料)"),
    re.compile(r"没(有)?找到"),
    re.compile(r"查不到|没查到"),
    re.compile(r"找不到"),
    re.compile(r"智库里没有"),
    re.compile(r"没听过这个名字"),
    re.compile(r"没(有)?(写|收录|载入)进去"),
    re.compile(r"没(有)?收录"),
    re.compile(r"没有这段"),
)

# Some models answer with tool calls written in the text channel (XML-ish markup)
# instead of the structured ``tool_calls`` field. Two dialects are seen in practice:
#   1. ``<tool_call><invoke name=...><parameter ...>`` (ASCII, Qwen-style)
#   2. ``<｜｜DSML｜｜invoke ...>`` (DeepSeek DSML: full-width bars U+FF5C)
# Either way that text must never reach the user.
_TOOL_MARKUP_RE = re.compile(
    r"<\s*/?\s*(?:[\uff5c|]{0,2}\s*DSML\s*[\uff5c|]{0,2}\s*)?"
    r"(?:tool_calls?|invoke|parameter|function_calls?)\b[^>]*>",
    re.I,
)
_TOOL_MARKUP_BLOCK_RE = re.compile(r"<\s*tool_call\s*>.*?</\s*tool_call\s*>", re.S | re.I)
_DSML_MARKUP_BLOCK_RE = re.compile(
    r"<\s*[\uff5c|]{1,2}\s*DSML\s*[\uff5c|]{1,2}.*?"
    r"<\s*/\s*[\uff5c|]{1,2}\s*DSML\s*[\uff5c|]{1,2}\s*[A-Za-z_]+\s*>",
    re.S | re.I,
)
_MAX_TEXT_TOOL_NUDGES = 2
_MAX_FINALIZE_ATTEMPTS = 2
_BUDGET_OBSERVATION = "（时间预算用完了帕，这次不再查新资料，直接用已经查到的内容作答）"

_TEXT_TOOL_INSTRUCTION = (
    "不要用文字写出工具调用（尖括号标记那一类），要用就直接调用工具功能；"
    "如果已经查够了，就立刻用帕姆的口吻把答案说出来。"
)
_LEAK_CORRECTION = (
    "刚才那段不是给开拓者看的回答。请不要输出任何工具调用语法或尖括号标记，"
    "直接用帕姆的口吻把现在能回答的内容说出来；查不到就如实说明。"
)
# Kept at the tail of the conversation so the answer comes out in Pom-Pom's voice.
_VOICE_REMINDER = (
    "现在这句是直接说给开拓者乘客听的：用帕姆的口吻讲，短句、口语，"
    "不要 Markdown、不要加粗、不要项目符号、不要栏目标题，也不要「仅供参考」这种免责声明腔。"
)

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]*")
_BULLET_RE = re.compile(r"(?m)^[ \t]*[-\*\u2022\u00b7][ \t]+")
_ORDERED_RE = re.compile(r"(?m)^[ \t]*\d+[.、)][ \t]+")
# 模型偶尔崩出西里尔字母词（中文回答里不该出现），连它旁边的空格一起去掉
_STRAY_CYRILLIC_RE = re.compile(r"\s*[\u0400-\u04ff]+\s*")


def has_tool_markup(text: str) -> bool:
    """True when a model answer leaked tool-call syntax into the text channel."""
    return bool(text) and bool(_TOOL_MARKUP_RE.search(text))


def strip_tool_markup(text: str) -> str:
    """Remove leaked tool-call markup so it can never be shown to the user."""
    if not text:
        return ""
    text = _TOOL_MARKUP_BLOCK_RE.sub(" ", text)
    text = _DSML_MARKUP_BLOCK_RE.sub(" ", text)
    text = _TOOL_MARKUP_RE.sub(" ", text)
    text = re.sub(r"<\s*/?\s*[\uff5c|]{1,2}\s*DSML\b[^>]*>", " ", text, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def strip_markdown_fluff(text: str) -> str:
    """Drop report-style Markdown markers so answers read like chat, not a guide."""
    if not text:
        return ""
    text = _BOLD_RE.sub(r"\1", text)
    text = _STRIKE_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _HEADING_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _ORDERED_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean_reply(text: str) -> str:
    """Last line of defence for both chat modes."""
    cleaned = strip_markdown_fluff(strip_tool_markup(text))
    cleaned = _STRAY_CYRILLIC_RE.sub("", cleaned)
    return cleaned

_KB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": (
            "查询列车智库（本地《崩坏：星穹铁道》剧情语料库）。"
            "剧情事实的依据只能来自这个工具返回的片段。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索词，尽量具体（人物、地点、事件名）",
                },
                "k": {"type": "integer", "description": "返回片段数量，默认 5"},
            },
            "required": ["query"],
        },
    },
}

_WEB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "联网搜索《崩坏：星穹铁道》攻略情报（养成、配装、配队、强度等）。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "检索词"}},
            "required": ["query"],
        },
    },
}

_FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_page",
        "description": "打开一条搜索结果的网页，读取正文（摘要不够用时再用）。",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "http(s) 网址"}},
            "required": ["url"],
        },
    },
}


@dataclass
class DeepThinkResult:
    reply: str
    steps: list[dict] = field(default_factory=list)
    rounds: int = 0
    tool_calls: int = 0
    evidence: int = 0
    insufficient: bool = False
    no_answer: bool = False
    fallback: bool = False
    text_tool_calls: int = 0


@dataclass
class _State:
    seen_calls: set[str] = field(default_factory=set)
    evidence_keys: set[str] = field(default_factory=set)
    steps: list[dict] = field(default_factory=list)
    evidence: int = 0
    tool_calls: int = 0
    rounds: int = 0
    stalled: int = 0
    fetches: int = 0
    text_tool_calls: int = 0
    last_web_source: str = ""
    last_error: str = ""


class DeepThinkEngine:
    """Runs a tool loop (knowledge base / web / page reader) until the model answers."""

    def __init__(
        self,
        settings: Settings,
        llm: Any,
        kb_search: Callable[[str, int], list[Chunk]],
        web_search: Callable[[str, int], list[dict]] | None = None,
        fetch_page: Callable[..., str] | None = None,
        samples_fn: Callable[..., list[Chunk]] | None = None,
        now: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self._llm = llm
        self._kb_search = kb_search
        self._web_search = web_search
        self._fetch_page = fetch_page
        self._samples_fn = samples_fn or (lambda n=5: [])
        self._now = now

    # ---- public API -------------------------------------------------

    def tool_specs(self) -> list[dict]:
        specs = [dict(_KB_TOOL)]
        if self._web_enabled():
            specs.append(dict(_WEB_TOOL))
            if self.settings.deep_think_fetch_enabled:
                specs.append(dict(_FETCH_TOOL))
        return specs

    def run(self, question: str, history: list[dict]) -> DeepThinkResult:
        for event in self.run_stream(question, history):
            if event.get("type") == "final":
                return event["result"]
        return DeepThinkResult(reply=FALLBACK_REPLY, fallback=True)

    def run_stream(self, question: str, history: list[dict]) -> Iterator[dict]:
        """Yield step events, then a final event carrying the DeepThinkResult."""
        state = _State()
        messages = self._build_messages(question, history)
        deadline = self._now() + max(0.0, self.settings.deep_think_timeout_seconds)
        max_steps = max(1, int(self.settings.deep_think_max_steps))
        reply = ""
        fallback = False

        for round_index in range(1, max_steps + 1):
            if self._now() >= deadline:
                break
            try:
                message = self._call_llm(
                    messages,
                    tools=self.tool_specs(),
                    timeout=self._round_timeout(deadline),
                )
            except Exception as exc:
                state.last_error = f"{type(exc).__name__}: {exc}"
                if not state.evidence and not state.tool_calls:
                    yield {
                        "type": "final",
                        "result": self._result(FALLBACK_REPLY, state, fallback=True),
                    }
                    return
                break
            state.rounds = round_index
            calls = list(getattr(message, "tool_calls", None) or [])
            if not calls:
                content = (message.content or "").strip()
                if has_tool_markup(content):
                    # Text-form tool call: nudge the model instead of showing markup.
                    state.text_tool_calls += 1
                    messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {"role": "system", "content": _TEXT_TOOL_INSTRUCTION}
                    )
                    if state.text_tool_calls >= _MAX_TEXT_TOOL_NUDGES:
                        break
                    continue
                reply = content
                break
            messages.append(_assistant_message(message))
            gained = 0
            for call in calls:
                name = str(getattr(getattr(call, "function", None), "name", "") or "")
                if self._now() >= deadline:
                    # 预算已经用完：不再执行剩余工具，但每个工具调用仍要有一个观察结果，
                    # 否则模型收到的是一段没有回应的工具调用序列。
                    observation = _BUDGET_OBSERVATION
                    step = self._step(
                        state, name, _summary_input(name, {}), 0, 0, observation
                    )
                    state.steps.append(step)
                    yield {"type": "step", **step}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": getattr(call, "id", ""),
                            "content": observation,
                        }
                    )
                    continue
                step, observation, new_evidence = self._execute(call, state)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": getattr(call, "id", ""),
                        "content": observation,
                    }
                )
                state.steps.append(step)
                yield {"type": "step", **step}
                gained += new_evidence
            state.stalled = state.stalled + 1 if gained == 0 else 0
            _append_voice_reminder(messages)
            if state.stalled >= 2 or self._now() >= deadline:
                break

        if not reply:
            reply, fallback = self._finalize(messages, state, deadline)
        reply = clean_reply(reply)
        if not reply:
            reply = NO_ANSWER_REPLY if not state.evidence else FALLBACK_REPLY
            fallback = True
        yield {"type": "final", "result": self._result(reply, state, fallback)}

    # ---- internals --------------------------------------------------

    def _web_enabled(self) -> bool:
        return bool(self.settings.web_search_enabled and self._web_search)

    def _build_messages(self, question: str, history: list[dict]) -> list[dict]:
        samples = self._samples_fn(5)
        system = build_deep_think_prompt(
            samples, max_steps=int(self.settings.deep_think_max_steps)
        )
        limit = max(0, self.settings.history_turns)
        # 一轮 = 用户 + 帕姆两条消息，保持与普通模式一致
        trimmed = history[-2 * limit:] if limit else []
        return [
            {"role": "system", "content": system},
            *trimmed,
            {"role": "user", "content": question},
        ]

    def _call_llm(self, messages: list[dict], timeout: float, tools=None) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "timeout": timeout,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = self._llm.chat.completions.create(**kwargs)
        return response.choices[0].message

    def _round_timeout(self, deadline: float) -> float:
        remaining = max(1.0, deadline - self._now())
        return min(float(self.settings.deep_think_llm_timeout_seconds), remaining)

    def _execute(self, call: Any, state: _State) -> tuple[dict, str, int]:
        name = str(getattr(getattr(call, "function", None), "name", "") or "")
        args = _parse_args(getattr(getattr(call, "function", None), "arguments", ""))
        state.tool_calls += 1
        signature = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
        if signature in state.seen_calls:
            observation = "（这个查询刚刚查过了，结果没有变化；换一个查询词，或者直接作答帕）"
            step = self._step(state, name, _summary_input(name, args), 0, 0, observation)
            return step, observation, 0
        state.seen_calls.add(signature)

        try:
            if name == "search_knowledge_base":
                observation, hits, fresh = self._run_kb(args, state)
            elif name == "search_web":
                observation, hits, fresh = self._run_web(args, state)
            elif name == "fetch_page":
                observation, hits, fresh = self._run_fetch(args, state)
            else:
                observation, hits, fresh = (f"（没有叫 {name} 的查询方式）", 0, 0)
        except Exception:
            # A failing tool must never break the loop; report it as an observation.
            observation, hits, fresh = ("（这次查询出了点问题，换一个查询词再试试帕）", 0, 0)

        limit = max(80, int(self.settings.deep_think_max_tool_chars))
        observation = observation[:limit]
        state.evidence += fresh
        return self._step(
            state, name, _summary_input(name, args), hits, fresh, observation
        ), observation, fresh

    def _run_kb(self, args: dict, state: _State) -> tuple[str, int, int]:
        query = str(args.get("query", "")).strip()
        if not query:
            return "（检索词是空的）", 0, 0
        try:
            k = int(args.get("k") or 5)
        except (TypeError, ValueError):
            k = 5
        k = max(1, min(k, 10))
        chunks = list(self._kb_search(query, k) or [])
        if not chunks:
            hint = alias_hint(query)
            note = f"\n【别名提示】{hint}，可以换这些叫法再查一次。" if hint else ""
            return f"（列车智库中没有找到相关记录）{note}", 0, 0
        fresh = 0
        for chunk in chunks:
            key = f"kb:{chunk.id}:{chunk.text[:60]}"
            if key not in state.evidence_keys:
                state.evidence_keys.add(key)
                fresh += 1
        observation = format_context(chunks)
        hint = alias_hint(query)
        if hint:
            observation += f"\n【别名提示】{hint}，换这些叫法再查可能更准。"
        return observation, len(chunks), fresh

    def _run_web(self, args: dict, state: _State) -> tuple[str, int, int]:
        if not self._web_enabled():
            return "（联网检索没有开启）", 0, 0
        query = str(args.get("query", "")).strip()
        if not query:
            return "（检索词是空的）", 0, 0
        results = list(self._web_search(query, 5) or [])
        if not results:
            return "（没有搜到相关情报）", 0, 0
        state.last_web_source = str(results[0].get("source", ""))
        lines: list[str] = []
        fresh = 0
        for item in results:
            url = str(item.get("url", ""))
            label = SOURCE_LABELS.get(str(item.get("source", "")), "")
            mark = f"（{label}）" if label else ""
            key = f"web:{url or item.get('title', '')}"
            if key not in state.evidence_keys:
                state.evidence_keys.add(key)
                fresh += 1
            lines.append(
                f"- 《{item.get('title', '')}》{mark} {url}\n  {item.get('snippet', '')}"
            )
        return "\n".join(lines), len(results), fresh

    def _run_fetch(self, args: dict, state: _State) -> tuple[str, int, int]:
        if not (self._web_enabled() and self.settings.deep_think_fetch_enabled):
            return "（联网检索没有开启）", 0, 0
        url = str(args.get("url", "")).strip()
        if not url:
            return "（没有给出网址）", 0, 0
        if state.fetches >= max(0, int(self.settings.deep_think_max_fetches)):
            return "（本次不再打开新的网页了）", 0, 0
        state.fetches += 1
        limit = max(200, int(self.settings.deep_think_max_tool_chars))
        text = ""
        try:
            text = (self._fetch_page(url, limit) or "").strip()
        except Exception:
            text = ""
        if not text:
            return "（网页打开失败）", 0, 0
        key = f"page:{url}"
        fresh = 0 if key in state.evidence_keys else 1
        state.evidence_keys.add(key)
        return text, 1, fresh

    def _finalize(
        self, messages: list[dict], state: _State, deadline: float
    ) -> tuple[str, bool]:
        prompt = list(messages)
        if not state.evidence and state.tool_calls:
            prompt.append({"role": "system", "content": _NO_EVIDENCE_INSTRUCTION})
        _append_voice_reminder(prompt)
        for _ in range(_MAX_FINALIZE_ATTEMPTS):
            try:
                message = self._call_llm(prompt, timeout=self._round_timeout(deadline))
            except Exception as exc:
                state.last_error = f"{type(exc).__name__}: {exc}"
                break
            content = (message.content or "").strip()
            if content and not has_tool_markup(content):
                return content, False
            if not content:
                continue
            # Leaked tool syntax again: ask once more, never return it.
            state.text_tool_calls += 1
            prompt.append({"role": "assistant", "content": content})
            prompt.append({"role": "system", "content": _LEAK_CORRECTION})
        if not state.evidence:
            return NO_ANSWER_REPLY, True
        return FALLBACK_REPLY, True

    def _step(
        self,
        state: _State,
        name: str,
        input_value: str,
        hits: int,
        new_hits: int,
        observation: str,
    ) -> dict:
        step = {
            "round": state.rounds,
            "action": name,
            "input": input_value,
            "hits": hits,
            "new_hits": new_hits,
            "observation": observation[:300],
        }
        if name == "search_web" and state.last_web_source:
            step["source"] = state.last_web_source
        return step

    def _result(
        self, reply: str, state: _State, fallback: bool
    ) -> DeepThinkResult:
        return DeepThinkResult(
            reply=reply,
            steps=list(state.steps),
            rounds=state.rounds,
            tool_calls=state.tool_calls,
            evidence=state.evidence,
            insufficient=state.evidence == 0 and state.tool_calls > 0,
            no_answer=any(pattern.search(reply) for pattern in _NO_ANSWER_PATTERNS),
            fallback=fallback,
            text_tool_calls=state.text_tool_calls,
        )


def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _summary_input(name: str, args: dict) -> str:
    if name == "fetch_page":
        return str(args.get("url", ""))
    return str(args.get("query", ""))


def _append_voice_reminder(messages: list[dict]) -> None:
    """Keep exactly one voice reminder, always as the last message."""
    messages[:] = [
        message
        for message in messages
        if not (
            message.get("role") == "system"
            and message.get("content") == _VOICE_REMINDER
        )
    ]
    messages.append({"role": "system", "content": _VOICE_REMINDER})


def _assistant_message(message: Any) -> dict:
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in message.tool_calls
        ],
    }
