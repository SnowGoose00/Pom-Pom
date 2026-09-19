import json

import pytest

from app.database import VectorDB
from app.import_data import chunk_records, import_from_dialogues

MONSTER_LINE = "怪物「序列扑满」（MinionLv2）：迷失在群星中的跨维度生物，性温，味甘，不可食用。"


def _rec(category, scene, speaker, text, pom=False):
    return {
        "category": category,
        "scene": scene,
        "speaker": speaker,
        "text": text,
        "pom_pom": pom,
        "meta": "",
    }


class FakeEmbedder:
    def __init__(self, dim=4):
        self.dim = dim

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


def test_identical_text_across_scenes_becomes_one_chunk_with_aliases():
    records = [
        _rec("monsters", "8002050", "", MONSTER_LINE),
        _rec("monsters", "800205001", "", MONSTER_LINE),
        _rec("monsters", "800205002", "", MONSTER_LINE),
        _rec("monsters", "1003010", "", "怪物「银鬃尉官」（Elite）：贝洛伯格的戍卫部队。"),
    ]

    chunks = chunk_records(records)

    assert len(chunks) == 2
    # ids are pre-dedupe positions: keeping the gaps lets alias_ids point at the
    # dropped duplicates so neighbour lookup can still reach their scenes
    assert [chunk.id for chunk in chunks] == [0, 3]
    assert chunks[0].text == MONSTER_LINE
    assert chunks[0].scene == "8002050"  # first occurrence is the representative
    meta = json.loads(chunks[0].meta)
    assert meta["aliases"] == ["800205001", "800205002"]
    assert meta["alias_ids"] == [1, 2]
    assert meta["alias_count"] == 3


def test_identical_lines_inside_one_scene_are_dropped_before_chunking():
    records = [
        _rec("avatars", "8009", "开拓者", "开拓者：技能「放心飞，开拓永相随！」：获得6个笑点。")
        for _ in range(5)
    ]

    chunks = chunk_records(records)

    assert len(chunks) == 1
    assert chunks[0].text.count("获得6个笑点") == 1
    assert "aliases" not in json.loads(chunks[0].meta)


def test_distinct_texts_are_all_kept():
    records = [
        _rec("items", "1", "", "物品「甲」：说明一。"),
        _rec("items", "2", "", "物品「乙」：说明二。"),
        _rec("story", "3", "姬子", "姬子：出发吧。"),
    ]

    chunks = chunk_records(records)

    assert len(chunks) == 3
    assert [chunk.id for chunk in chunks] == [0, 1, 2]


def test_alias_list_is_capped_but_count_is_kept():
    records = [_rec("monsters", f"900{i:03d}", "", MONSTER_LINE) for i in range(30)]

    chunks = chunk_records(records)

    assert len(chunks) == 1
    meta = json.loads(chunks[0].meta)
    assert len(meta["aliases"]) == 12  # bounded meta size
    assert meta["alias_count"] == 30


def test_import_from_dialogues_writes_deduped_chunks(tmp_path):
    dialogues = tmp_path / "dialogues"
    dialogues.mkdir()
    rows = [
        _rec("monsters", "8002050", "", MONSTER_LINE),
        _rec("monsters", "800205001", "", MONSTER_LINE),
        _rec("items", "1", "", "物品「甲」：说明一。"),
    ]
    (dialogues / "all.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    count = import_from_dialogues(dialogues, tmp_path / "t.db", FakeEmbedder(), dim=4)

    assert count == 2
    db = VectorDB(tmp_path / "t.db", dim=4)
    assert db.count() == 2
    texts = [chunk.text for chunk, _ in db.search([0.1, 0.1, 0.1, 0.1], k=5)]
    assert texts.count(MONSTER_LINE) == 1
    db.close()


def _write_dialogues(tmp_path, rows):
    dialogues = tmp_path / "dialogues"
    dialogues.mkdir(exist_ok=True)
    (dialogues / "all.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return dialogues


class ShortEmbedder:
    """Returns fewer vectors than texts -- must never silently import a subset."""

    dim = 4

    def embed(self, texts):
        return []


def test_failed_reimport_keeps_the_previous_knowledge_base(tmp_path):
    rows = [_rec("items", "1", "", "物品「甲」：说明一。")]
    dialogues = _write_dialogues(tmp_path, rows)
    db_path = tmp_path / "t.db"
    assert import_from_dialogues(dialogues, db_path, FakeEmbedder(), dim=4) == 1

    # a re-import whose vectors have the wrong dimension blows up mid-write
    with pytest.raises(ValueError):
        import_from_dialogues(dialogues, db_path, FakeEmbedder(dim=3), dim=4)

    db = VectorDB(db_path, dim=4)
    assert db.count() == 1, "失败的导入不能把旧知识库清空"
    assert db.search([0.1, 0.1, 0.1, 0.1], k=5)[0][0].text == "物品「甲」：说明一。"
    db.close()


def test_import_rejects_embedding_count_mismatch(tmp_path):
    rows = [_rec("items", "1", "", "物品「甲」：说明一。")]
    dialogues = _write_dialogues(tmp_path, rows)
    db_path = tmp_path / "t.db"
    assert import_from_dialogues(dialogues, db_path, FakeEmbedder(), dim=4) == 1

    with pytest.raises(ValueError):
        import_from_dialogues(dialogues, db_path, ShortEmbedder(), dim=4)

    db = VectorDB(db_path, dim=4)
    assert db.count() == 1
    db.close()


def test_neighbours_reach_the_scene_that_dedupe_removed(tmp_path):
    """A line repeated in two scenes must still expose both scenes' neighbours."""
    records = [
        _rec("story", "S1", "", "前句A。"),
        _rec("story", "S1", "", "重复的那句。"),
        _rec("story", "S1", "", "后句A。"),
        _rec("story", "S2", "", "前句B。"),
        _rec("story", "S2", "", "重复的那句。"),
        _rec("story", "S2", "", "后句B。"),
    ]
    chunks = chunk_records(records, max_lines=1)
    repeated = next(c for c in chunks if c.text == "重复的那句。")
    db = VectorDB(tmp_path / "t.db", dim=4)
    db.insert_chunks(chunks, [[0.1] * 4 for _ in chunks])

    texts = {c.text for c in db.scene_neighbors(repeated.id, before=1, after=1)}

    assert {"前句A。", "后句A。"} <= texts, "本场景的上下文"
    assert {"前句B。", "后句B。"} <= texts, "被去重合并掉的那个场景也要能取到上下文"
    db.close()
