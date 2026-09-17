"""RAG chat engine: retrieve -> prompt -> LLM -> persona fallback."""
from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from openai import OpenAI

from app.config import Settings
from app.database import Chunk, VectorDB
from app.deepthink import (
    FALLBACK_REPLY,
    DeepThinkEngine,
    NO_ANSWER_REPLY,
    clean_reply,
)
from app.persona import build_system_prompt
from app.retrieval import (
    JUNK_SPEAKERS,
    TRAVEL_CUE_PHRASES,
    agent_retrieve,
    cosine_similarity,
    entity_terms,
    expand_aliases,
    is_self_intro_question,
    is_travel_list_question,
    match_avatar_names,
    merge_ranked,
    scene_quota,
    select_diverse,
)
from app.websearch import build_web_query, fetch_page_text, is_build_question, search_web

_IDENTITY_CUES = ("列车长", "我是帕姆", "帕姆是", "负责", "报站")


NO_KEY_REPLY = (
    "开拓者，帕姆的列车广播还没接上信号呢。"
    "看起来列车长还没拿到通行证，等管理员配置好之后，再喊帕姆出发吧！"
)

LLM_TIMEOUT_SECONDS = 120
MIN_CONTEXT_CHUNKS = 5  # 进提示词的最少片段数（top_k 更小时按这个来）
OVER_FETCH_MULTIPLIER = 6  # 过取倍数：先多拿候选，再挑多样化的
LITERAL_MIN_SIMILARITY = 0.5  # 字面命中还必须和问题语义相关，才允许补进上下文
LITERAL_MIN_SIMILARITY_ALIAS = 0.45  # 别名是间接证据（如 乐子神 → 阿哈），门槛稍低
SPECIALTY_DISTANCE_BONUS = 0.03  # 专题通道（角色路由/旅行广播/字面）的优先偏置
LITERAL_MAX_GROUPS = 4  # 一次提问最多注入几组字面命中（别名展开后要限量）
LITERAL_SEATS = 2  # 字面命中保底席位（每个实体词 1 条），防止被随机候选挤掉
NEIGHBOR_BUDGET = 3  # 邻接扩展最多追加几条（同场景相邻片段，用于指代消解）


class ChatEngine:
    def __init__(
        self,
        settings: Settings,
        db: VectorDB,
        embedder: Any,
        llm_client: Any | None = None,
        web_search_fn: Any | None = None,
    ):
        self.settings = settings
        self.db = db
        self.embedder = embedder
        self._avatar_names = db.avatar_names()
        self._entity_vocab = db.entity_vocabulary()
        if web_search_fn is not None:
            self._web_search_fn = web_search_fn
        else:
            sources = tuple(settings.web_search_sources)
            self._web_search_fn = lambda query: search_web(query, sources=sources)
        self._last_diag: dict = {}
        self._stats: dict = {}
        self._llm = llm_client or (
            OpenAI(api_key=settings.api_key, base_url=settings.base_url)
            if settings.api_key
            else None
        )
        self._deep = DeepThinkEngine(
            settings,
            self._llm,
            kb_search=lambda query, k=5: self._retrieve(query)[:k],
            web_search=self._web_tool,
            fetch_page=fetch_page_text if settings.deep_think_fetch_enabled else None,
            samples_fn=lambda n=5: self.db.pom_pom_samples(n),
        )

    def _web_tool(self, query: str, max_results: int = 5) -> list[dict]:
        return self._web_search_fn(query)

    def _retrieve(self, query: str) -> list[Chunk]:
        if is_self_intro_question(query):
            return self._retrieve_self_intro(query)
        context_size = max(self.settings.top_k, MIN_CONTEXT_CHUNKS)
        over_k = max(context_size * OVER_FETCH_MULTIPLIER, context_size)
        query_vector = self.embedder.embed([query])[0]
        # 向量组单独拿：拥挤/冗余主要发生在这里，配额与 MMR 只作用在它上面
        primary = agent_retrieve(
            self._search,
            self._reflect_queries,
            query,
            top_k=over_k,
        )
        groups: list[list[tuple[Chunk, float]]] = []
        matched = match_avatar_names(self._avatar_names, query)
        if matched:
            for name in matched:
                groups.append(self.db.search_speaker(query_vector, name, k=4))
            self._stats["avatar_routed"] = matched
        if is_travel_list_question(query):
            for phrase in TRAVEL_CUE_PHRASES:
                groups.append(self.db.search(self.embedder.embed([phrase])[0], k=4))
        # 实体名不一定在向量 top-k 里（比如「差分宇宙里的鲁珀特」被大量同类片段挤掉），
        # 所以对实体词补一次字面检索；但它是"召回保险"，不参与排序：
        # ① 上下文里已经有该实体就跳过；② 命中必须通过语义相似度门控。
        literal_terms = entity_terms(query, vocab=self._entity_vocab)
        literal_groups: list[list[tuple[Chunk, float]]] = []
        injected = 0
        tried: set[str] = set()
        for term in literal_terms:
            # 同一实体的各种叫法（阿哈 / 乐子神 /「欢愉」）都要试一遍
            for alias in expand_aliases(term):
                if alias in tried:
                    continue
                tried.add(alias)
                threshold = (
                    LITERAL_MIN_SIMILARITY
                    if alias == term
                    else LITERAL_MIN_SIMILARITY_ALIAS
                )
                hits = self._gated_literal_hits(alias, query_vector, threshold)
                if hits:
                    literal_groups.append(hits)
                    injected += 1
                if injected >= LITERAL_MAX_GROUPS:
                    break
            if injected >= LITERAL_MAX_GROUPS:
                break
        if literal_terms:
            self._stats["literal_terms"] = literal_terms
            self._stats["literal_injected"] = injected
        # Player option/joke lines in message chats are not canon facts.
        def _keep(item: tuple[Chunk, float]) -> bool:
            chunk = item[0]
            return not (
                chunk.category == "messages"
                and chunk.speaker in {"Player", "PlayerAuto"}
            )

        primary = [item for item in primary if _keep(item)]
        # 专题通道（角色路由/旅行广播/字面命中）是规则触发的高精度信号，和向量组一起排序：
        # 它们自己的距离已经不错，但拥挤时会被向量组挤掉，所以给一点优先偏置。
        specialty = [
            (chunk, max(0.0, distance - SPECIALTY_DISTANCE_BONUS))
            for chunk, distance in merge_ranked(groups)
            if _keep((chunk, distance))
        ]
        specialty += [
            (chunk, max(0.0, distance - SPECIALTY_DISTANCE_BONUS))
            for chunk, distance in merge_ranked(literal_groups)
            if _keep((chunk, distance))
        ]
        merged = merge_ranked([primary, specialty])
        embeddings = self.db.chunk_embeddings(
            [chunk.id for chunk, _ in merged[: over_k * 2]]
        )
        # 统一配额：任何场景都不能超过结果的一半（专题通道也一样受限）
        # 字面精确命中是"召回保险"：每个实体词至少保证 1 条进上下文，
        # 否则它会在分数竞争里被模型反思检索（LLM，每次略有不同）带来的候选挤掉，
        # 表现为"同一个问题有时答得出、有时答不出"。
        seeds: list[tuple[Chunk, float]] = []
        seed_ids: set[int] = set()
        for group in literal_groups:
            for chunk, distance in group:
                if chunk.id in seed_ids:
                    continue
                seeds.append((chunk, distance))
                seed_ids.add(chunk.id)
                break
            if len(seeds) >= LITERAL_SEATS:
                break
        selected = select_diverse(
            merged, k=context_size, embeddings=embeddings, seed=seeds
        )
        # 邻接扩展：台词被切块后，指代（「那个家伙」）常常落在隔壁块里。
        # 给最相关的几条带上同场景相邻片段，缓解共指断裂。
        expanded = list(selected)
        seen_ids = {chunk.id for chunk, _ in expanded}
        quota = scene_quota(context_size)
        scene_counts = Counter(chunk.scene for chunk, _ in expanded)
        added_neighbors = 0
        for chunk, distance in selected:
            if added_neighbors >= NEIGHBOR_BUDGET:
                break
            for neighbor in self.db.scene_neighbors(chunk.id, before=1, after=1):
                if added_neighbors >= NEIGHBOR_BUDGET:
                    break
                if neighbor.id in seen_ids:
                    continue
                if scene_counts.get(neighbor.scene, 0) >= quota:
                    continue  # 邻接扩展也要守「单场景不超过一半」的规矩
                expanded.append((neighbor, distance))
                seen_ids.add(neighbor.id)
                scene_counts[neighbor.scene] = scene_counts.get(neighbor.scene, 0) + 1
                added_neighbors += 1
        selected = expanded
        self._stats["context_neighbors"] = added_neighbors
        scenes = [chunk.scene for chunk, _ in selected]
        self._stats["context_unique"] = len({chunk.text for chunk, _ in selected})
        self._stats["context_max_scene"] = (
            max(Counter(scenes).values()) if scenes else 0
        )
        return [chunk for chunk, _ in selected]

    def _gated_literal_hits(
        self,
        term: str,
        query_vector: list[float],
        min_similarity: float = LITERAL_MIN_SIMILARITY,
    ) -> list[tuple[Chunk, float]]:
        """字面命中必须同时语义相关才注入（否则「物品「鲁珀特」」这种会占名额）。"""
        hits = self.db.search_text(term, k=3, query_vector=query_vector)
        if not hits:
            return []
        embeddings = self.db.chunk_embeddings([chunk.id for chunk, _ in hits])
        kept = []
        for chunk, distance in hits:
            vector = embeddings.get(chunk.id)
            similarity = (
                cosine_similarity(query_vector, vector) if vector is not None else 0.0
            )
            if similarity >= min_similarity:
                kept.append((chunk, distance))
        return kept

    def _retrieve_self_intro(self, query: str) -> list[Chunk]:
        """Identity questions use Pom-Pom's own lines, not generic story scenes."""
        vec = self.embedder.embed([query])[0]
        results = self.db.search_speaker(vec, "帕姆", k=6)
        # Deduplicate identical lines repeated across scenes; drop unknown speakers.
        seen_texts: set[str] = set()
        kept: list[tuple[Chunk, float]] = []
        for chunk, _ in results:
            if chunk.speaker in JUNK_SPEAKERS or chunk.text in seen_texts:
                continue
            seen_texts.add(chunk.text)
            kept.append((chunk, 0.0))
        # Prefer lines where Pom-Pom states her role/duties.
        kept.sort(
            key=lambda item: (
                0 if any(cue in item[0].text for cue in _IDENTITY_CUES) else 1,
            )
        )
        return [chunk for chunk, _ in kept]

    def _search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        self._stats["searches"] = self._stats.get("searches", 0) + 1
        vec = self.embedder.embed([query])[0]
        return self.db.search(vec, k=k)

    def _reflect_queries(self, question: str, context_texts: list[str]) -> list[str]:
        """Ask the model what to search next when retrieved context is insufficient."""
        self._stats["reflect_rounds"] = self._stats.get("reflect_rounds", 0) + 1
        if self._llm is None:
            return []
        prompt = (
            "你是检索规划助手。根据用户问题与已检索片段，判断仅凭这些片段能否回答问题。\n"
            "能回答：只输出 {\"answerable\": true}\n"
            "不能回答：输出 {\"answerable\": false, \"queries\": [\"更具体的检索词1\", \"更具体的检索词2\"]}\n"
            "只输出 JSON，不要多余文字。"
        )
        context = "\n".join(f"- {text[:120]}" for text in context_texts[:8]) or "（暂无片段）"
        try:
            resp = self._llm.chat.completions.create(
                model=self.settings.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"问题：{question}\n\n已检索片段：\n{context}"},
                ],
                temperature=0.0,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            content = resp.choices[0].message.content or ""
            start, end = content.find("{"), content.rfind("}")
            if start == -1 or end == -1:
                return []
            data = json.loads(content[start : end + 1])
            if data.get("answerable") is False:
                queries = data.get("queries") or []
                return [str(q) for q in queries[:2] if str(q).strip()]
        except Exception:
            return []
        return []

    def _build_messages(self, user_message: str, history: list[dict]) -> list[dict]:
        context = self._retrieve(user_message)
        samples = self.db.pom_pom_samples(n=5)
        web_results: list[dict] = []
        if self.settings.web_search_enabled and is_build_question(user_message):
            web_results = self._web_search_fn(build_web_query(user_message))
            self._stats["web_titles"] = [item.get("title", "") for item in web_results]
            self._stats["web_source"] = (
                web_results[0].get("source", "") if web_results else ""
            )
        system = build_system_prompt(context, samples, user_message, web_results)
        self._stats["retrieved"] = [
            {
                "category": chunk.category,
                "speaker": chunk.speaker,
                "text": chunk.text[:80],
            }
            for chunk in context[:6]
        ]

        max_history = max(0, self.settings.history_turns)
        trimmed = history[-max_history:] if max_history else []
        messages = [{"role": "system", "content": system}]
        messages.extend(trimmed)
        messages.append({"role": "user", "content": user_message})
        return messages

    def stream_reply(
        self, user_message: str, history: list[dict], mode: str = "normal"
    ):
        """Yield ``step`` events (deep mode) and exactly one ``reply`` event."""
        self._stats = {
            "searches": 0,
            "reflect_rounds": 0,
            "avatar_routed": [],
            "web_titles": [],
            "retrieved": [],
            "steps": [],
        }
        started = time.time()
        if self._llm is None:
            self._record_diag(user_message, started, NO_KEY_REPLY, True, mode)
            yield self._reply_event(NO_KEY_REPLY, mode, fallback=True)
            return

        if mode == "deep":
            for event in self._deep.run_stream(user_message, history):
                if event.get("type") != "step":
                    result = event["result"]
                    self._stats["llm_rounds"] = result.rounds
                    self._stats["deep_think_tool_calls"] = result.tool_calls
                    self._stats["insufficient"] = result.insufficient
                    self._stats["no_answer"] = result.no_answer
                    self._stats["text_tool_calls"] = result.text_tool_calls
                    self._record_diag(
                        user_message, started, result.reply, result.fallback, mode
                    )
                    yield self._reply_event(
                        result.reply,
                        mode,
                        fallback=result.fallback,
                        insufficient=result.insufficient,
                        rounds=result.rounds,
                        steps=list(result.steps),
                        no_answer=result.no_answer,
                    )
                    return
                self._stats["steps"].append(event)
                yield event
            return

        messages = self._build_messages(user_message, history)
        attempts = 0
        for attempt in range(3):
            attempts += 1
            try:
                resp = self._llm.chat.completions.create(
                    model=self.settings.model,
                    messages=messages,
                    temperature=self.settings.temperature,
                    timeout=LLM_TIMEOUT_SECONDS,
                )
                # Never let tool-call markup or report-style Markdown reach the user.
                reply = clean_reply(resp.choices[0].message.content or "")
                self._stats["attempts"] = attempts
                if not reply:
                    self._record_diag(user_message, started, FALLBACK_REPLY, True, mode)
                    yield self._reply_event(FALLBACK_REPLY, mode, fallback=True, rounds=1)
                    return
                self._record_diag(user_message, started, reply, False, mode)
                yield self._reply_event(reply, mode, fallback=False, rounds=1)
                return
            except Exception as exc:  # transient API errors: retry briefly
                self._stats["last_error"] = f"{type(exc).__name__}: {exc}"
                if attempt < 2:
                    time.sleep(2)
        self._stats["attempts"] = attempts
        self._record_diag(user_message, started, FALLBACK_REPLY, True, mode)
        yield self._reply_event(FALLBACK_REPLY, mode, fallback=True, rounds=1)

    def _reply_event(
        self,
        reply: str,
        mode: str,
        fallback: bool,
        insufficient: bool = False,
        rounds: int = 0,
        steps: list[dict] | None = None,
        no_answer: bool = False,
    ) -> dict:
        return {
            "type": "reply",
            "reply": reply,
            "mode": mode,
            "fallback": fallback,
            "insufficient": insufficient,
            "rounds": rounds,
            "steps": steps if steps is not None else list(self._stats.get("steps", [])),
            "no_answer": no_answer,
        }

    def reply(
        self, user_message: str, history: list[dict], mode: str = "normal"
    ) -> str:
        reply = FALLBACK_REPLY
        for event in self.stream_reply(user_message, history, mode=mode):
            if event.get("type") == "reply":
                reply = str(event.get("reply") or FALLBACK_REPLY)
        return reply

    def _record_diag(
        self,
        question: str,
        started: float,
        reply: str,
        fallback: bool,
        mode: str = "normal",
    ) -> None:
        self._last_diag = {
            "question": question,
            "model": self.settings.model,
            "elapsed_ms": round((time.time() - started) * 1000),
            "fallback": fallback,
            "reply_len": len(reply),
            "mode": mode,
            "steps": list(self._stats.get("steps", [])),
            "llm_rounds": self._stats.get("llm_rounds", 0),
            "insufficient": self._stats.get("insufficient", False),
            "no_answer": self._stats.get("no_answer", False),
            "deep_think_tool_calls": self._stats.get("deep_think_tool_calls", 0),
            "text_tool_calls": self._stats.get("text_tool_calls", 0),
            **self._stats,
        }

    def last_diagnostics(self) -> dict:
        return dict(self._last_diag)
