import threading

import numpy as np

from app.database import Chunk, VectorDB
from app.import_data import chunk_records


def _vec(v: float, dim: int = 4) -> list[float]:
    return [v] * dim


def test_chunk_records_groups_lines():
    records = [
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "一", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "二", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "三", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "四", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "五", "pom_pom": True, "meta": ""},
    ]
    chunks = chunk_records(records, max_lines=2)
    assert len(chunks) == 3
    assert "帕姆：一" in chunks[0].text
    assert "五" in chunks[-1].text
    assert chunks[0].pom_pom is True


def test_vector_db_roundtrip_and_search(tmp_path):
    db = VectorDB(tmp_path / "t.db", dim=4)
    chunks = [
        Chunk(0, "story", "s1", "帕姆", "列车出发", True, ""),
        Chunk(1, "books", "b1", "", "宇宙见闻", False, ""),
    ]
    db.insert_chunks(chunks, [_vec(1.0), _vec(-1.0)])

    results = db.search(_vec(0.9), k=2)
    assert len(results) == 2
    assert results[0][0].id == 0
    assert results[0][1] <= results[1][1]

    only_books = db.search(_vec(0.9), k=2, category="books")
    assert [c.id for c, _ in only_books] == [1]

    samples = db.pom_pom_samples(n=1)
    assert samples[0].speaker == "帕姆"
    assert db.count() == 2
    db.close()


def test_chunk_text_joins_speaker():
    records = [
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "本帕姆来报站了", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "开拓者", "text": "下一站是哪里", "pom_pom": False, "meta": ""},
    ]
    chunks = chunk_records(records, max_lines=4, max_chars=220)
    assert "帕姆：本帕姆来报站了" in chunks[0].text


def test_chunk_records_keeps_speaker_turns_separate():
    records = [
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "一", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "二", "pom_pom": True, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "姬子", "text": "三", "pom_pom": False, "meta": ""},
        {"category": "story", "scene": "1", "speaker": "帕姆", "text": "四", "pom_pom": True, "meta": ""},
    ]

    chunks = chunk_records(records, max_lines=4, max_chars=220)

    assert [chunk.speaker for chunk in chunks] == ["帕姆", "姬子", "帕姆"]
    assert chunks[0].text.count("帕姆：") == 2  # 同说话人的连续行仍会合并
    assert "姬子：三" in chunks[1].text
    assert chunks[1].pom_pom is False


def test_long_text_splits_on_sentence_boundaries():
    first = "甲" * 80 + "。"
    second = "乙" * 80 + "。"
    third = "丙" * 80 + "。"
    records = [
        {
            "category": "books",
            "scene": "1",
            "speaker": "",
            "text": first + second + third,
            "pom_pom": False,
            "meta": "",
        }
    ]

    chunks = chunk_records(records, max_lines=4, max_chars=220)

    assert [len(chunk.text) for chunk in chunks] == [len(first + second), len(third)]
    assert all(chunk.text.endswith("。") for chunk in chunks)  # 不从句中硬切
    assert all(len(chunk.text) <= 220 for chunk in chunks)


def test_vector_db_usable_across_threads(tmp_path):
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [Chunk(0, "story", "s1", "帕姆", "列车出发", True, "")],
        [[0.5, 0.5, 0.5, 0.5]],
    )
    results = []

    def worker():
        try:
            results.extend(db.search([0.4, 0.4, 0.4, 0.4], k=1))
        except Exception as exc:  # pragma: no cover - assertion below catches it
            results.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert len(results) == 1
    assert results[0][0].id == 0
    db.close()


def test_search_filters_by_speaker_and_lists_avatar_names(tmp_path):
    db = VectorDB(tmp_path / "t.db", dim=4)
    chunks = [
        Chunk(0, "avatars", "1003", "姬子", "我是领航员姬子", False, ""),
        Chunk(1, "avatars", "8001", "开拓者", "我是开拓者", False, ""),
        Chunk(2, "story", "s1", "姬子", "某句剧情台词", False, ""),
    ]
    db.insert_chunks(chunks, [[0.5, 0, 0, 0], [0.3, 0, 0, 0], [0.2, 0, 0, 0]])

    only_jizi = db.search([0.5, 0, 0, 0], k=5, speaker="姬子")
    assert {c.id for c, _ in only_jizi} == {0, 2}

    only_avatars = db.search([0.5, 0, 0, 0], k=5, category="avatars", speaker="姬子")
    assert {c.id for c, _ in only_avatars} == {0}

    assert db.avatar_names() == ["姬子", "开拓者"]  # distinct, ordered by name
    db.close()


def test_search_speaker_ranks_within_speaker_subset(tmp_path):
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "story", "s1", "帕姆", "帕姆是列车长", True, ""),
            Chunk(1, "story", "s2", "姬子", "我是领航员姬子", False, ""),
        ],
        [[1.0, 0.0, 0.0, 0.0], [1.0, 0.9, 0.0, 0.0]],
    )
    # Query is closest to 姬子 overall, but must still find 帕姆's own line.
    results = db.search_speaker([1.0, 0.8, 0.0, 0.0], "帕姆", k=3)
    assert [c.id for c, _ in results] == [0]
    db.close()


def test_search_text_finds_literal_substring(tmp_path):
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "rogue", "s1", "黑塔", "黑塔：鲁珀特二世掀起了第一次帝皇战争。", False, ""),
            Chunk(1, "rogue", "s2", "未知", "差分宇宙的液金长河缓缓流动。", False, ""),
            Chunk(2, "story", "s3", "", "无关的剧情片段。", False, ""),
        ],
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]],
    )

    hits = db.search_text("鲁珀特", k=5)

    assert [chunk.id for chunk, _ in hits] == [0]
    assert hits[0][1] < 0.4  # 字面命中要排在「相似度足够」的一侧
    assert db.search_text("不存在的词", k=5) == []
    db.close()


def test_search_text_escapes_like_wildcards(tmp_path):
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "items", "s1", "", "物品「100%棉花糖」：甜甜的。", False, ""),
            Chunk(1, "items", "s2", "", "物品「棉花糖」：只有糖。", False, ""),
        ],
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
    )

    assert [c.id for c, _ in db.search_text("100%棉花糖", k=3)] == [0]
    # "%" 按字面处理：只命中真的有 % 的那条，而不是「匹配一切」
    assert [c.id for c, _ in db.search_text("%", k=3)] == [0]
    db.close()


def test_entity_vocabulary_collects_speakers_and_config_names(tmp_path):
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "story", "s1", "阿哈", "阿哈：你说什么？", False, ""),
            Chunk(1, "items", "s2", "", "物品「逐火铭牌」：古老的名牌。", False, ""),
            Chunk(2, "monsters", "s3", "", "怪物「序列扑满」：性温，味甘。", False, ""),
            Chunk(3, "story", "s4", "未知", "未知：……", False, ""),
        ],
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.5, 0.5, 0.0, 0.0]],
    )

    vocab = db.entity_vocabulary()

    assert "阿哈" in vocab
    assert "逐火铭牌" in vocab
    assert "序列扑满" in vocab
    assert "未知" not in vocab  # 噪音说话人不进词表
    assert "" not in vocab
    db.close()


def test_scene_neighbors_return_surrounding_chunks_only(tmp_path):
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(
        [
            Chunk(0, "story", "s1", "帕姆", "第一句", True, ""),
            Chunk(1, "story", "s1", "帕姆", "那个家伙干的帕。", True, ""),
            Chunk(2, "story", "s1", "帕姆", "第三句", True, ""),
            Chunk(3, "story", "s2", "帕姆", "别的场景", True, ""),
        ],
        [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]],
    )

    neighbors = db.scene_neighbors(1, before=1, after=1)

    assert [chunk.id for chunk in neighbors] == [0, 2]  # 隔壁场景不进
    assert db.scene_neighbors(1, before=1, after=0)[0].id == 0
    assert db.scene_neighbors(1, before=0, after=0) == []
    db.close()
