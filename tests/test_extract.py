import json
import shutil
from pathlib import Path

from app.extract import clean_text, extract_all


FIXTURES = Path(__file__).parent / "fixtures" / "starrail_mini"


def _records(out_dir: Path) -> list[dict]:
    with open(out_dir / "all.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_extract_story_and_train_visitor_and_messages_and_books(tmp_path):
    extract_all(FIXTURES, tmp_path)
    records = _records(tmp_path)

    story = [r for r in records if r["category"] == "story"]
    visitor = [r for r in records if r["category"] == "train_visitor"]
    messages = [r for r in records if r["category"] == "messages"]
    books = [r for r in records if r["category"] == "books"]
    avatars = [r for r in records if r["category"] == "avatars"]
    missions = [r for r in records if r["category"] == "missions"]
    items = [r for r in records if r["category"] == "items"]
    monsters = [r for r in records if r["category"] == "monsters"]

    assert len(story) == 6  # 3 段 Story 演出 + 3 句 Mission Act 对白
    assert story[0] == {
        "category": "story",
        "scene": "1010101",
        "speaker": "帕姆",
        "text": "列车马上要出发了哦，开拓者！",
        "pom_pom": True,
        "meta": "",
    }
    assert story[1]["speaker"] == "卡芙卡"
    assert story[2]["scene"] == "2000201"
    assert story[2]["speaker"] == "卡芙卡"

    assert len(visitor) == 2
    assert visitor[0]["speaker"] == "艾丝妲"
    assert "空间望远镜" in visitor[0]["text"]

    assert len(messages) == 1
    assert messages[0]["speaker"] == "黑塔"
    assert messages[0]["text"] == "嘿，开拓者，我是黑塔，有好事找你"
    assert messages[0]["scene"] == "300"

    assert len(books) >= 1
    assert "见闻录" in books[0]["text"]

    assert len(avatars) == 6  # profile + skill + 2 voice + story + crew roster
    profile = next(r for r in avatars if "角色信息" in r["text"])
    assert profile["speaker"] == "姬子"
    assert "姬子·无垠星海" in profile["text"]
    assert any("无尽之刃" in r["text"] for r in avatars)
    assert any("我是星穹列车的领航员" in r["text"] for r in avatars)
    assert any("关于帕姆" in r["text"] for r in avatars)
    assert any("修复了星穹列车" in r["text"] for r in avatars)
    assert any("列车组成员名录" in r["text"] and "姬子" in r["text"] for r in avatars)

    assert len(missions) == 2
    assert any("追猎群星" in r["text"] for r in missions)
    assert any("任务目标：到达雅利洛-VI" in r["text"] for r in missions)

    assert len(items) == 1
    assert "物品「开拓捷径」" in items[0]["text"]
    assert "用于开拓新世界" in items[0]["text"]

    assert len(monsters) == 1
    assert "怪物「裂界造物」" in monsters[0]["text"]
    assert "技能「扑击」" in monsters[0]["text"]


def test_extract_never_crashes_on_missing_hash(tmp_path):
    extract_all(FIXTURES, tmp_path)  # hashes 111/112/113 absent
    assert True


def test_clean_text_keeps_the_base_text_of_ruby_annotations():
    # {RUBY_B#注音}正文{RUBY_E#}：正文才是要显示/检索的内容
    # 泰坦称谓只存在于注音里（正文是名字），这种要保留成「正文（注音）」
    assert (
        clean_text("{RUBY_B#纷争之泰坦}尼卡多利{RUBY_E#}鏖战粉霞天女")
        == "尼卡多利（纷争之泰坦）鏖战粉霞天女"
    )
    assert clean_text("等待{RUBY_B#死亡}塞纳托斯{RUBY_E#}引渡") == "等待塞纳托斯引渡"
    assert clean_text("去掉<unbreak>25000</unbreak>标记") == "去掉25000标记"


def test_extract_mission_acts_as_story_with_cleaned_rich_text(tmp_path):
    extract_all(FIXTURES, tmp_path)
    story = [r for r in _records(tmp_path) if r["category"] == "story"]
    acts = [r for r in story if r["scene"] == "8015104"]

    assert [r["speaker"] for r in acts] == ["黑天鹅", "未知", "艺术商"]
    assert acts[0]["text"] == "情况紧急，我只能破例用些手段，带你们一同穿过忆域。"
    assert acts[1]["text"] == "不买。"  # 玩家选项没有名字字段
    assert (
        acts[2]["text"]
        == "如何？您打算购入《尼卡多利（纷争之泰坦）鏖战粉霞天女》了吗？"
    )
    assert "{" not in acts[2]["text"] and "RUBY" not in acts[2]["text"]


def test_extract_does_not_double_count_train_visitor_acts(tmp_path):
    extract_all(FIXTURES, tmp_path)
    records = _records(tmp_path)
    visitor = [r for r in records if r["category"] == "train_visitor"]
    story = [r for r in records if r["category"] == "story"]

    assert {r["scene"] for r in visitor} == {"8000", "8001"}
    assert all(r["scene"] != "TrainVisitor" for r in story)
    assert not [r for r in story if "空间望远镜" in r["text"]]


def test_extract_rogue_dialogue_into_its_own_category(tmp_path):
    extract_all(FIXTURES, tmp_path)
    rogue = [r for r in _records(tmp_path) if r["category"] == "rogue"]
    scenes = {r["scene"] for r in rogue}

    assert "82101" in scenes  # Config/Level/RogueDialogue/82101
    assert "Event0010000" in scenes  # Config/Level/Rogue/RogueDialogue/Event0010000
    assert any(r["speaker"] == "螺丝咕姆" for r in rogue)
    assert any(
        r["text"] == "模拟宇宙里的一切都能被计算，包括你的选择。" for r in rogue
    )


def test_extract_rogue_configs_as_searchable_text(tmp_path):
    extract_all(FIXTURES, tmp_path)
    rogue = [r for r in _records(tmp_path) if r["category"] == "rogue"]

    config = next(r for r in rogue if r["scene"].startswith("RogueBuff"))
    assert "祝福" in config["text"]
    assert config["speaker"] == ""


def _fixture_copy_with_long_rogue(tmp_path: Path) -> Path:
    """Copy the fixtures and add a Rogue config whose texts overflow the old 1500-char cut."""
    data_dir = tmp_path / "data"
    shutil.copytree(FIXTURES, data_dir)
    excel = data_dir / "ExcelOutput"
    text_map_path = data_dir / "TextMap" / "TextMapCHS.json"
    text_map = json.loads(text_map_path.read_text(encoding="utf-8"))
    entries = []
    for index in range(10):
        key = str(900 + index)
        text_map[key] = f"祝福{index}：" + "描述内容" * 50
        entries.append(
            {
                "RogueBuffID": index + 1,
                "BuffName": {"Hash": int(key)},
                "BuffDesc": {"Hash": int(key)},
            }
        )
    (excel / "RogueLongBuff.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )
    text_map_path.write_text(json.dumps(text_map, ensure_ascii=False), encoding="utf-8")
    return data_dir


def test_rogue_config_entries_survive_the_whole_file(tmp_path):
    """Long configs used to be concatenated and cut at 1500 chars, dropping entries."""
    data_dir = _fixture_copy_with_long_rogue(tmp_path)

    extract_all(data_dir, tmp_path / "out")
    rogue = [r for r in _records(tmp_path / "out") if r["category"] == "rogue"]
    kept = "\n".join(r["text"] for r in rogue if r["scene"].startswith("RogueLongBuff"))

    assert "祝福0" in kept
    assert "祝福9" in kept, "文件末尾的条目不能在提取阶段被截断丢掉"


def test_extract_handles_rogue_simple_talk_and_ignores_option_text(tmp_path):
    extract_all(FIXTURES, tmp_path)
    rogue = [r for r in _records(tmp_path) if r["category"] == "rogue"]
    event_lines = [r for r in rogue if r["scene"] == "Event0010000"]

    # PlayRogueSimpleTalk 也走 SimpleTalkList，要能解析出来
    assert [r["speaker"] for r in event_lines] == ["黑塔"]
    assert event_lines[0]["text"].startswith("「祝福」")
    # PlayRogueOptionTalk 的选项文本（OptionTextmapID）不是台词，不应入库成对白
    assert len(event_lines) == 1


def test_extract_atlas_entries_as_their_own_category(tmp_path):
    extract_all(FIXTURES, tmp_path)
    atlas = [r for r in _records(tmp_path) if r["category"] == "atlas"]

    # 每条词条按段落拆成多条记录，标题作为前缀
    assert len(atlas) == 4
    aha = [r for r in atlas if "阿哈" in r["text"]]
    assert any("把宇宙当成一场玩笑" in r["text"] for r in aha)  # 富文本已清洗
    assert any("炸成了两截" in r["text"] for r in aha)
    assert all(r["text"].startswith("智库「「欢愉」，阿哈」：") for r in aha)
    assert {r["scene"] for r in aha} == {"1.0", "1.1"}  # 段落各自成块
    assert all(r["speaker"] == "" for r in atlas)

    danlun = [r for r in atlas if "丹轮寺" in r["text"]]
    assert len(danlun) == 2
    assert {r["scene"] for r in danlun} == {"2.0", "2.1"}


def test_extract_external_text_sources(tmp_path):
    extract_all(FIXTURES, tmp_path)
    records = _records(tmp_path)

    # 演出概要：没有标题字段，用记录号做标题，段落各自成条
    summary = [r for r in records if r["category"] == "story_summary"]
    assert len(summary) == 2
    assert all(r["text"].startswith("剧情概要「8001」：") for r in summary)
    assert any("卡芙卡来到「黑塔」空间站" in r["text"] for r in summary)

    # 书籍正文：标题取自 BookInsideName，正文里的**字面 \n** 要还原成真换行再按段落切
    books = [r for r in records if r["text"].startswith("书籍「星穹列车见闻录」：")]
    assert len(books) == 2
    assert any("第一，不许偷吃零食。" in r["text"] for r in books)
    assert any("第二，不许把列车当积木拆。" in r["text"] for r in books)
