import json

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
    assert [chunk.id for chunk in chunks] == [0, 1]
    assert chunks[0].text == MONSTER_LINE
    assert chunks[0].scene == "8002050"  # first occurrence is the representative
    meta = json.loads(chunks[0].meta)
    assert meta["aliases"] == ["800205001", "800205002"]
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
