"""Agentic iterative retrieval: reformulate and reflect until the query is answerable."""
from __future__ import annotations

import re
from typing import Callable

from app.aliases import ALIAS_GROUPS
from app.database import Chunk

MIN_SIMILARITY = 0.6  # below this similarity one query is considered insufficient
MAX_LOCAL_QUERIES = 2  # extra local reformulations after the initial search
MAX_REFLECT_QUERIES = 2
MAX_TOTAL_SEARCHES = 5
MAX_PER_SCENE = 2  # 显式指定时的默认上限（不传则按「单场景不超过结果一半」动态算）
MMR_LAMBDA = 0.7  # MMR：相关性权重（1-lambda 用来惩罚与已选结果的相似度）


def scene_quota(k: int) -> int:
    """单场景最多进几条：不超过结果的一半（k=6 -> 3，k=5 -> 2）。"""
    return max(1, k // 2)

_QUOTED = re.compile(r"「([^」]+)」|《([^》]+)》|\"([^\"]+)\"")
_TRAILING_SUFFIXES = (
    "是谁",
    "是什么",
    "是怎样的",
    "怎么样",
    "怎么",
    "如何",
    "哪些",
    "哪些世界",
    "哪些星球",
    "多少",
    "谁",
    "吗",
    "呢",
    "吧",
    "啊",
    "呀",
    "介绍一下",
    "有没有",
    "是做什么用的",
    "做什么用的",
    "有什么用",
    "是干什么用的",
    "干什么用的",
    "有什么作用",
    "有什么技能",
    "有什么剧情",
)
_LEADING_PHRASES = ("请问", "帮我", "告诉我", "介绍一下", "讲讲", "说说", "关于", "那个叫")

# 拆实体词时丢掉这些：疑问词、常见动词、以及到处都有的通用词
_STOP_TERMS = {
    "什么",
    "是什么",
    "怎么",
    "怎么样",
    "怎样",
    "如何",
    "为什么",
    "哪些",
    "哪个",
    "多少",
    "一下",
    "介绍",
    "介绍一下",
    "告诉",
    "说说",
    "讲讲",
    "帕姆",
    "星穹铁道",
    "崩坏",
    "崩铁",
    "星铁",
    "游戏",
    "剧情",
    "故事",
    "列车",
    "世界",
    "星球",
    "玩家",
    "今天",
    "上海",
    "天气",
}
_TERM_SPLIT = re.compile(r"[的里中上下与和及跟对、，,。！？?…\s]+")
# 候选词尾部的疑问成分要去掉：「鲁珀特是什么」->「鲁珀特」
_TERM_TAIL = re.compile(
    r"(是?什么(东西|关系)?|是谁|怎么样|怎样|如何|怎么|为什么|哪些|多少|吗|呢|吧|啊|呀|的|了)+$"
)

# 太通用的词不适合做字面探针（会命中几千条，挤占上下文）；向量通道会负责这些词
_GENERIC_TERMS = {
    "列车",
    "星穹列车",
    "开拓者",
    "乘客",
    "世界",
    "星球",
    "游戏",
    "剧情",
    "故事",
    "任务",
    "物品",
    "怪物",
    "角色",
    "星神",
    "命途",
}

# 联合字面检索用的"线索词"：这类词本身太通用，但和实体一起出现时能精确定位事实
CONTEXT_TERMS = {
    "列车",
    "星穹列车",
    "空间站",
    "模拟宇宙",
    "差分宇宙",
    "圣杯战争",
    "幻月游戏",
    "星核",
    "命途",
    "星神",
}

TRAVEL_CUE_PHRASES = (
    "列车此行的目的地是",
    "本次列车的目的地是",
    "我们准备前往",
    "航线的下一站",
    "各位乘客应该都知道了帕？列车此行的目的地是",
)

SELF_INTRO_MARKERS = (
    "你是谁",
    "你是什么人",
    "你是哪一位",
    "自我介绍",
    "介绍你自己",
    "介绍下你自己",
    "介绍一下你",
    "说说你自己",
    "帕姆是谁",
    "列车长是谁",
    "介绍一下自己",
)
JUNK_SPEAKERS = {"未知", "？？？", "???", "？"}


def is_self_intro_question(question: str) -> bool:
    return any(marker in question for marker in SELF_INTRO_MARKERS)


def is_travel_list_question(question: str) -> bool:
    """Detect questions asking which worlds/places the train has visited."""
    verbs = ("去过哪些", "到访过哪些", "去过", "到访过", "抵达过", "游览过")
    places = ("世界", "星球", "地方", "站点", "目的地")
    return any(v in question for v in verbs) and any(p in question for p in places)


def insufficient(
    results: list[tuple[Chunk, float]], min_similarity: float = MIN_SIMILARITY
) -> bool:
    if not results:
        return True
    best_similarity = max(1.0 - distance for _, distance in results)
    return best_similarity < min_similarity


def quoted_names(text: str) -> list[str]:
    names: list[str] = []
    for match in _QUOTED.finditer(text):
        name = next((g for g in match.groups() if g), None)
        if name:
            names.append(name)
    return names


def entity_names_from_text(text: str) -> list[str]:
    return quoted_names(text)


def match_avatar_names(
    names: list[str], question: str, limit: int = 2
) -> list[str]:
    """Return avatar names mentioned in the question, longest first."""
    matched = [name for name in names if name and name in question]
    matched.sort(key=lambda name: (-len(name), name))
    return matched[:limit]


def merge_ranked(
    groups: list[list[tuple[Chunk, float]]],
) -> list[tuple[Chunk, float]]:
    """Merge result groups, keep the best distance per chunk, sort best-first."""
    best: dict[int, tuple[Chunk, float]] = {}
    for results in groups:
        for chunk, distance in results:
            if chunk.id not in best or distance < best[chunk.id][1]:
                best[chunk.id] = (chunk, distance)
    return sorted(best.values(), key=lambda item: item[1])


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)


def select_diverse(
    candidates: list[tuple[Chunk, float]],
    k: int,
    embeddings: dict[int, list[float]],
    max_per_scene: int | None = None,
    mmr_lambda: float = MMR_LAMBDA,
    seed: list[tuple[Chunk, float]] | None = None,
) -> list[tuple[Chunk, float]]:
    """从候选里挑 k 条：相关性优先，但用 MMR 惩罚与已选结果相似的、并限制单场景配额。

    动机：向量 top-k 常被同一话题的近似片段占满（如一堆「差分宇宙」事件），
    导致真正回答问题的片段被挤掉。
    """
    if k <= 0 or not candidates:
        return list(seed or [])
    if max_per_scene is None:
        max_per_scene = scene_quota(k)
    picked: list[tuple[Chunk, float]] = list(seed or [])
    scene_count: dict[str, int] = {}
    for chunk, _ in picked:
        scene_count[chunk.scene] = scene_count.get(chunk.scene, 0) + 1
    picked_ids = {chunk.id for chunk, _ in picked}
    ordered = [
        item for item in sorted(candidates, key=lambda item: item[1])
        if item[0].id not in picked_ids
    ]
    if not picked:
        picked = [ordered[0]]
        scene_count[ordered[0][0].scene] = 1
        ordered = ordered[1:]
    remaining = ordered

    while remaining and len(picked) < k:
        best_index = None
        best_score = None
        for index, (chunk, distance) in enumerate(remaining):
            if scene_count.get(chunk.scene, 0) >= max_per_scene:
                continue
            relevance = 1.0 - distance
            vector = embeddings.get(chunk.id)
            redundancy = 0.0
            if vector is not None:
                redundancy = max(
                    (
                        cosine_similarity(vector, embeddings[picked_chunk.id])
                        for picked_chunk, _ in picked
                        if picked_chunk.id in embeddings
                    ),
                    default=0.0,
                )
            score = mmr_lambda * relevance - (1.0 - mmr_lambda) * redundancy
            if best_score is None or score > best_score:
                best_score = score
                best_index = index
        if best_index is None:
            # 剩余候选全部撞配额：宁可少给几条，也不让同一场景重新占满上下文
            break
        chunk, distance = remaining.pop(best_index)
        picked.append((chunk, distance))
        scene_count[chunk.scene] = scene_count.get(chunk.scene, 0) + 1
    return picked


def entity_query(question: str) -> str | None:
    """Reduce a question to its core entity for a focused re-search."""
    quoted = quoted_names(question)
    if quoted:
        return quoted[0]
    cleaned = re.sub(r"[？！。，、,.!?…\s]+$", "", question)
    for prefix in _LEADING_PHRASES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    for suffix in _TRAILING_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    cleaned = re.sub(r"^(这个|那个|叫|是|有)\s*", "", cleaned)
    cleaned = re.sub(r"(是|的|了|呢|吗|吧|啊|呀)$", "", cleaned)
    cleaned = cleaned.strip("？！。，、,.!?… \t")
    if len(cleaned) >= 2 and cleaned != question:
        return cleaned
    return None


def expand_aliases(term: str, limit: int = 4) -> list[str]:
    """把实体扩展成同一实体的各种叫法（阿哈 ↔ 乐子神 ↔ 「欢愉」…）。"""
    for group in ALIAS_GROUPS:
        if term in group:
            return list(group[:limit])
    return [term]


def alias_hint(text: str) -> str:
    """如果文本里出现已知别称组的成员，返回一句提示（给深度思考换词用）。"""
    for group in ALIAS_GROUPS:
        if any(member in text for member in group):
            others = [member for member in group[1:] if member not in text]
            if others:
                return f"{group[0]} 也叫 {' / '.join(others[:3])}（同一存在的不同叫法）"
    return ""


def vocabulary_matches(
    question: str, vocab: set[str], limit: int = 4, include_generic: bool = True
) -> list[str]:
    """用实体词表在问题里做最长匹配（长词优先，避免子串互相覆盖）。"""
    matches: list[tuple[int, int, str]] = []
    for term in vocab:
        if len(term) < 2:
            continue
        if not include_generic and term in _GENERIC_TERMS:
            continue
        position = question.find(term)
        if position >= 0:
            matches.append((len(term), position, term))
    matches.sort(key=lambda item: (-item[0], item[1]))
    picked: list[str] = []
    spans: list[tuple[int, int]] = []
    for length, position, term in matches:
        end = position + length
        if any(position < span_end and end > span_start for span_start, span_end in spans):
            continue  # 与已选词重叠（例如「阿哈」被「阿哈帕姆」覆盖）
        picked.append(term)
        spans.append((position, end))
        if len(picked) >= limit:
            break
    return picked


def entity_terms(
    question: str, vocab: set[str] | None = None, limit: int = 3
) -> list[str]:
    """问题里的实体候选词，供字面检索使用（「A 里的 B」要能拆出 B）。

    优先用**实体词表做最长匹配**（角色名/物品名/怪物名/星神），词表没命中才退回规则切分。
    返回长词在前（越长越具体），最多 limit 个。
    """
    if vocab:
        picked = vocabulary_matches(
            question, vocab, limit=limit, include_generic=False
        )
        if picked:
            # 词表命中优先，但**不丢弃**规则切分的候选：像「鲁珀特」这类
            # 不在词表里的实体只能靠规则抽出来（否则整条通道只查到「差分宇宙」）。
            extras = [
                term
                for term in _rule_based_terms(question, limit + len(picked))
                if term not in picked
                and not any(match in term for match in picked)
            ]
            return (picked + extras)[: max(limit, len(picked))]
    return _rule_based_terms(question, limit)


def _rule_based_terms(question: str, limit: int = 3) -> list[str]:
    """兜底：按「的/里/和」等切分再剥掉疑问词尾（词表没命中时才用）。"""
    terms: list[str] = []

    whole = question.strip("？！。，、,.!?… \t")

    def add(term: str) -> None:
        term = term.strip("「」《》\"'：: ")
        term = _TERM_TAIL.sub("", term).strip()
        if len(term) < 2 or term in _STOP_TERMS or term in terms or term == whole:
            return
        terms.append(term)

    for name in quoted_names(question):
        add(name)
    for piece in _TERM_SPLIT.split(question):
        add(piece)
    core = entity_query(question)
    if core:
        add(core)
    # 按长度升序保留最具体的若干个，最短的通常就是实体本身
    terms.sort(key=len)
    return terms[:limit]


def agent_retrieve(
    search: Callable[[str, int], list[tuple[Chunk, float]]],
    reflect: Callable[[str, list[str]], list[str]],
    question: str,
    top_k: int = 6,
    min_similarity: float = MIN_SIMILARITY,
) -> list[tuple[Chunk, float]]:
    """Iteratively search until context is sufficient or attempts run out."""
    seen: dict[int, tuple[Chunk, float]] = {}

    def merge(results: list[tuple[Chunk, float]]) -> None:
        for chunk, distance in results:
            if chunk.id not in seen or distance < seen[chunk.id][1]:
                seen[chunk.id] = (chunk, distance)

    def merged() -> list[tuple[Chunk, float]]:
        return sorted(seen.values(), key=lambda item: item[1])

    merge(search(question, top_k))
    attempts = 1

    if insufficient(merged(), min_similarity):
        candidates: list[str] = []

        def add_candidate(query: str) -> None:
            if query and query != question and query not in candidates:
                candidates.append(query)

        core = entity_query(question)
        if core:
            add_candidate(core)
        for name in entity_names_from_text(question):
            add_candidate(name)
        for chunk, _ in merged()[:3]:
            for name in entity_names_from_text(chunk.text)[:2]:
                add_candidate(name)

        for query in candidates[:MAX_LOCAL_QUERIES]:
            merge(search(query, top_k))
            attempts += 1
            if not insufficient(merged(), min_similarity):
                break

    if insufficient(merged(), min_similarity):
        queries = reflect(question, [chunk.text for chunk, _ in merged()]) or []
        for query in queries[:MAX_REFLECT_QUERIES]:
            if attempts >= MAX_TOTAL_SEARCHES:
                break
            merge(search(query, top_k))
            attempts += 1
            if not insufficient(merged(), min_similarity):
                break

    return merged()
