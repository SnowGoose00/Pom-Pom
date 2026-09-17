from app.database import Chunk
from app.retrieval import (
    agent_retrieve,
    entity_query,
    entity_terms,
    expand_aliases,
    entity_names_from_text,
    insufficient,
    is_travel_list_question,
    is_self_intro_question,
    match_avatar_names,
    merge_ranked,
    select_diverse,
    vocabulary_matches,
    MIN_SIMILARITY,
)


def _chunk(cid: int, text: str, category: str = "story") -> Chunk:
    return Chunk(cid, category, "s1", "", text, False, "")


def test_insufficient_on_empty_or_low_similarity():
    assert insufficient([], MIN_SIMILARITY) is True
    # cosine distance 0.8 => similarity 0.2, below threshold
    assert insufficient([(_chunk(0, "x"), 0.8)], MIN_SIMILARITY) is True
    # cosine distance 0.2 => similarity 0.8, sufficient
    assert insufficient([(_chunk(0, "x"), 0.2)], MIN_SIMILARITY) is False


def test_entity_names_from_text_picks_quoted_and_speaker_names():
    names = entity_names_from_text("开拓者拿到了物品「逐火铭牌」和《寻梦记》")
    assert "逐火铭牌" in names


def test_entity_query_extracts_core_entity():
    assert entity_query("虚妄之母是谁？") == "虚妄之母"
    assert entity_query("逐火铭牌是做什么用的？") == "逐火铭牌"
    assert entity_query("「辉光星」的剧情是怎样的？") == "辉光星"


def test_entity_terms_splits_location_phrases():
    # 「A 里的 B」这种结构，字面检索要能拿到 B，而不是整串
    terms = entity_terms("差分宇宙里的鲁珀特是什么？")
    assert "鲁珀特" in terms
    assert "差分宇宙" in terms
    assert terms.index("鲁珀特") < terms.index("差分宇宙")  # 越靠后越具体


def test_entity_terms_keeps_names_and_drops_stopwords():
    terms = entity_terms("三月七和帕姆是什么关系？")
    assert "三月七" in terms
    assert "帕姆" not in terms  # 停用词表里的大众角色名不做字面检索
    assert len(terms) <= 3
    assert entity_terms("鲁珀特是谁") == ["鲁珀特"]
    assert entity_terms("") == []


def test_expand_aliases_covers_aeon_nicknames():
    aha = expand_aliases("阿哈")
    assert "阿哈" in aha
    assert "乐子神" in aha  # 语料里把列车炸成两截的正是「乐子神」
    assert "阿哈" in expand_aliases("乐子神")  # 反向也成立
    assert expand_aliases("姬子") == ["姬子"]  # 不在表里就原样返回


def test_entity_terms_prefers_vocabulary_longest_match():
    vocab = {"阿哈", "列车", "螺丝咕姆", "黑塔", "白厄", "翁法罗斯"}

    # 「A在B上」句式：规则切分会废掉，词表匹配要能抽出 A（且排第一）
    assert entity_terms("阿哈在列车上干过什么？", vocab=vocab)[0] == "阿哈"
    # 长词优先于短词
    terms = entity_terms("白厄在翁法罗斯经历了什么？", vocab=vocab)
    assert terms[0] == "翁法罗斯"
    assert "白厄" in terms


def test_entity_terms_falls_back_to_rules_without_vocabulary():
    assert "逐火铭牌" in entity_terms("逐火铭牌是做什么用的？", vocab=set())


def test_vocabulary_matches_keeps_generic_terms_for_joint_search():
    vocab = {"阿哈", "列车", "乐子神", "姬子"}

    assert vocabulary_matches("阿哈在列车上干过什么？", vocab) == ["阿哈", "列车"]
    # entity_terms 仍然过滤通用词，避免把「列车」当成实体去展开
    assert entity_terms("阿哈在列车上干过什么？", vocab=vocab)[0] == "阿哈"
    assert vocabulary_matches("姬子在做什么？", vocab, limit=1) == ["姬子"]


def test_agent_retrieve_reformulates_when_first_round_empty():
    calls = []

    def search(q, k=6):
        calls.append(q)
        if len(calls) == 1:
            return []
        return [(_chunk(42, "命中内容：逐火铭牌", "items"), 0.1)]

    def reflect(question, context):
        raise AssertionError("reflect must not run when a local hit suffices")

    results = agent_retrieve(search, reflect, "逐火铭牌是什么？")
    assert len(calls) >= 2
    assert any(c[0].id == 42 for c in results)
    assert calls[-1] == "逐火铭牌"


def test_agent_retrieve_uses_reflection_when_local_rounds_fail():
    calls = []

    def search(q, k=6):
        calls.append(q)
        # initial query + entity reformulation both miss; reflection sub-query hits
        return [] if len(calls) <= 2 else [(_chunk(7, "补充片段", "books"), 0.15)]

    def reflect(question, context):
        assert len(calls) == 2  # after initial + quoted-entity reformulation
        return ["再查一次 A", "再查一次 B"]

    results = agent_retrieve(search, reflect, "「辉光星」是什么？")
    assert any(c[0].id == 7 for c in results)
    assert len(calls) == 3


def test_agent_retrieve_dedupes_results():
    calls = []

    def search(q, k=6):
        calls.append(q)
        return [(_chunk(1, "相同片段", "story"), 0.2)]

    def reflect(question, context):
        return []

    results = agent_retrieve(search, reflect, "问题")
    assert len(results) == 1
    assert results[0][0].id == 1


def test_match_avatar_names_longest_first_and_limited():
    names = match_avatar_names(["三月七", "姬子", "帕姆", "丹恒"], "三月七和帕姆是什么关系？")
    assert names == ["三月七", "帕姆"]
    assert match_avatar_names(["姬子", "帕姆"], "你认识姬子吗？", limit=1) == ["姬子"]
    assert match_avatar_names(["姬子", "帕姆"], "列车上有什么？") == []


def test_merge_ranked_dedupes_and_sorts():
    groups = [
        [(_chunk(1, "A"), 0.9), (_chunk(2, "B"), 0.5)],
        [(_chunk(1, "A"), 0.2)],
    ]
    merged = merge_ranked(groups)
    assert [(c.id, d) for c, d in merged] == [(1, 0.2), (2, 0.5)]


def _scene_chunk(cid: int, scene: str, text: str = "内容") -> Chunk:
    return Chunk(cid, "story", scene, "帕姆", text, True, "")


def test_select_diverse_caps_chunks_from_the_same_scene():
    # 6 条同一场景（距离都很近）+ 3 条别的场景
    same = [(_scene_chunk(i, "sceneA", f"A{i}"), 0.10 + i * 0.01) for i in range(6)]
    other = [
        (_scene_chunk(10 + i, f"sceneB{i}", f"B{i}"), 0.30 + i * 0.02)
        for i in range(3)
    ]
    embeddings = {chunk.id: [1.0, 0.0, 0.0, 0.0] for chunk, _ in same + other}

    picked = select_diverse(same + other, k=4, embeddings=embeddings, max_per_scene=2)

    assert len(picked) == 4
    scenes = [chunk.scene for chunk, _ in picked]
    assert scenes.count("sceneA") <= 2
    assert picked[0][0].id == 0  # 最相关的那条仍然排第一


def test_select_diverse_default_quota_is_half_of_k():
    # 不传 max_per_scene 时，单场景最多占结果的一半（k=6 -> 3 条）
    same = [(_scene_chunk(i, "sceneA", f"A{i}"), 0.10 + i * 0.01) for i in range(8)]
    other = [
        (_scene_chunk(20 + i, f"sceneB{i}", f"B{i}"), 0.40 + i * 0.02)
        for i in range(3)
    ]
    embeddings = {chunk.id: [1.0, 0.0, 0.0, 0.0] for chunk, _ in same + other}

    picked = select_diverse(same + other, k=6, embeddings=embeddings)

    scenes = [chunk.scene for chunk, _ in picked]
    assert len(picked) == 6
    assert scenes.count("sceneA") <= 3


def test_select_diverse_honours_seed_picks_and_their_scene_quota():
    seed_chunk = _scene_chunk(99, "seeded", "规则触发的证据")
    candidates = [
        (_scene_chunk(0, "seeded", "同场景的另一条"), 0.05),
        (_scene_chunk(1, "sceneB", "别的场景"), 0.30),
        (_scene_chunk(2, "sceneC", "再来一条"), 0.35),
    ]
    embeddings = {chunk.id: [1.0, 0.0, 0.0, 0.0] for chunk, _ in candidates}
    embeddings[99] = [1.0, 0.0, 0.0, 0.0]

    picked = select_diverse(
        candidates, k=3, embeddings=embeddings, max_per_scene=1, seed=[(seed_chunk, 0.2)]
    )

    assert picked[0][0].id == 99  # 种子保留且在最前
    assert [chunk.id for chunk, _ in picked[1:]] == [1, 2]  # 已占满配额的场景不再进


def test_select_diverse_prefers_dissimilar_candidates():
    candidates = [
        (_scene_chunk(0, "s0", "关于鲁珀特的核心片段"), 0.20),
        (_scene_chunk(1, "s1", "与首条几乎一样的近重复"), 0.21),
        (_scene_chunk(2, "s2", "角度完全不同的另一段"), 0.35),
    ]
    embeddings = {
        0: [1.0, 0.0, 0.0, 0.0],
        1: [0.99, 0.01, 0.0, 0.0],  # 与 0 近乎重合
        2: [0.0, 1.0, 0.0, 0.0],  # 与 0 正交
    }

    picked = select_diverse(
        candidates, k=2, embeddings=embeddings, max_per_scene=5, mmr_lambda=0.5
    )

    assert [chunk.id for chunk, _ in picked] == [0, 2]


def test_is_travel_list_question():
    assert is_travel_list_question("星穹列车曾经去过哪些世界？")
    assert is_travel_list_question("列车到访过哪些星球和世界？")
    assert not is_travel_list_question("姬子在列车上是做什么的？")
    assert not is_travel_list_question("介绍一下你自己")


def test_is_self_intro_question():
    assert is_self_intro_question("你好，你是谁？")
    assert is_self_intro_question("介绍一下你自己。")
    assert is_self_intro_question("帕姆是谁？")
    assert is_self_intro_question("列车长介绍一下自己吧")
    assert not is_self_intro_question("黑塔空间站是谁建造的？")
    assert not is_self_intro_question("银狼如何配装？")
