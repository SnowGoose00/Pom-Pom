"""Extract structured dialogue records from a StarRailData sparse checkout."""
from __future__ import annotations

import json
import itertools
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

CATEGORIES = (
    "story",
    "messages",
    "train_visitor",
    "books",
    "avatars",
    "missions",
    "items",
    "monsters",
    "rogue",
    "atlas",
    "story_summary",
    "places",
    "chronicle",
)
POM_POM_NAMES = {"帕姆", "Pom-Pom", "Pom Pom", "帕姆（Pom-Pom）"}


def clean_text(text: str) -> str:
    """Normalize placeholders and ruby tags to readable Chinese."""
    if not text or text == "N/A":
        return ""
    text = text.replace("{NICKNAME}", "开拓者")
    # {RUBY_B#注音}正文{RUBY_E#}：正文才是显示内容；但泰坦称谓只活在注音里
    # （如「纷争之泰坦」尼卡多利），这种保留成「正文（注音）」以免丢检索词。
    text = re.sub(
        r"\{RUBY_B#([^}]*)\}([^{]*?)\{RUBY_E#\}",
        lambda match: (
            f"{match.group(2)}（{match.group(1)}）"
            if match.group(1).endswith("泰坦")
            else match.group(2)
        ),
        text,
    )
    text = re.sub(r"\{RUBY_B#[^}]*\}", "", text)
    text = re.sub(r"\{RUBY_E#\}", "", text)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    # 原始数据里的换行是**字面**的 "\n" 两个字符，先还原再统一折叠空白
    text = text.replace("\\n", "\n").replace("\\r", "")
    return re.sub(r"\s+", " ", text).strip()


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _speaker_content(sent_id: Any, sent_map: dict, text_map: dict) -> dict | None:
    info = sent_map.get(str(sent_id))
    if not info:
        return None
    try:
        text_hash = info.get("TalkSentenceText", {}).get("Hash")
        if text_hash is None:
            return None
        content = clean_text(text_map[str(text_hash)])
        name_hash = info.get("TextmapTalkSentenceName", {}).get("Hash")
        speaker = clean_text(text_map[str(name_hash)]) if name_hash is not None else ""
    except (KeyError, TypeError):
        return None
    if not content:
        return None
    return {"speaker": speaker or "未知", "text": content}


def _build_sent_index(sent_map: Any) -> dict[str, dict]:
    """TalkSentenceConfig.json is a list of entries; index by TalkSentenceID."""
    if isinstance(sent_map, dict):
        return {str(k): v for k, v in sent_map.items()}
    out: dict[str, dict] = {}
    for entry in sent_map:
        sid = entry.get("TalkSentenceID")
        if sid is not None:
            out[str(sid)] = entry
    return out


def _index_by(items: Any, key: str) -> dict[str, Any]:
    """ExcelOutput configs are lists of entries; index them by their ID field."""
    if isinstance(items, dict):
        return {str(k): v for k, v in items.items()}
    out: dict[str, Any] = {}
    for item in items:
        k = item.get(key)
        if k is not None:
            out[str(k)] = item
    return out


def _resolve_hash(field: Any, text_map: dict) -> str:
    if not isinstance(field, dict):
        return ""
    h = field.get("Hash")
    if h is None:
        return ""
    return clean_text(text_map.get(str(h), ""))


def _parse_mission(info: dict, sent_map: dict, text_map: dict) -> Iterator[dict]:
    for sequence in info.get("OnStartSequece", []) or []:
        for element in sequence.get("TaskList", []) or []:
            if not isinstance(element, dict):
                continue
            # 按字段而不是按 $type 判断：主线用 PlayAndWaitSimpleTalk，
            # 模拟宇宙用 PlayRogueSimpleTalk / PlayAeonTalk 等变体，但都带 SimpleTalkList。
            if element.get("SimpleTalkList"):
                for talk in element["SimpleTalkList"]:
                    sc = _speaker_content(talk.get("TalkSentenceID"), sent_map, text_map)
                    if sc:
                        yield sc
            if element.get("OptionList"):
                # 只有带 TalkSentenceID 的选项是台词；Rogue 选项用 OptionTextmapID，跳过。
                for option in element["OptionList"]:
                    sc = _speaker_content(option.get("TalkSentenceID"), sent_map, text_map)
                    if sc:
                        yield sc
            if element.get("$type") in {
                "RPG.GameCore.WaitCustomString",
                "RPG.GameCore.TriggerCustomString",
            }:
                m = re.search(r"(\d+)", str(element.get("CustomString", {}).get("Value", "")))
                if m:
                    sc = _speaker_content(m.group(1), sent_map, text_map)
                    if sc:
                        yield sc


def _extract_story(data_dir: Path, sent_map: dict, text_map: dict) -> Iterator[dict]:
    roots = [
        data_dir / "Story" / "Mission",
        data_dir / "Story" / "Discussion",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            scene = path.parent.name
            try:
                info = _load_json(path)
            except json.JSONDecodeError:
                continue
            for line in _parse_mission(info, sent_map, text_map):
                yield {"category": "story", "scene": scene, **line, "meta": ""}


def _extract_train_visitor(data_dir: Path, sent_map: dict, text_map: dict) -> Iterator[dict]:
    base = data_dir / "Config" / "Level" / "Mission" / "TrainVisitor" / "Act"
    for path in sorted(base.rglob("*.json")):
        scene = path.stem
        try:
            info = _load_json(path)
        except json.JSONDecodeError:
            continue
        for line in _parse_mission(info, sent_map, text_map):
            yield {"category": "train_visitor", "scene": scene, **line, "meta": ""}


def _extract_mission_acts(data_dir: Path, sent_map: dict, text_map: dict) -> Iterator[dict]:
    """主线/支线任务的演出脚本（Config/Level/Mission/**/Act*.json）里的对白。

    这些脚本才是「谁在什么时候说了什么」的主干：`Story/**` 只覆盖其中一部分演出，
    任务里的成段对白（含大量早期世界剧情）都写在 Act 文件里。TrainVisitor 已由
    单独的提取器处理，这里跳过以免重复。
    """
    base = data_dir / "Config" / "Level" / "Mission"
    if not base.exists():
        return
    for path in sorted(base.rglob("Act*.json")):
        relative = path.relative_to(base)
        scene = relative.parts[0]
        if scene == "TrainVisitor":
            continue
        try:
            info = _load_json(path)
        except json.JSONDecodeError:
            continue
        for line in _parse_mission(info, sent_map, text_map):
            yield {"category": "story", "scene": scene, **line, "meta": ""}


def _extract_rogue(data_dir: Path, sent_map: dict, text_map: dict) -> Iterator[dict]:
    """模拟宇宙的对白：`Config/Level/Rogue**` 与 `Config/Level/RogueDialogue**` 的演出脚本。"""
    bases = (
        data_dir / "Config" / "Level" / "Rogue",
        data_dir / "Config" / "Level" / "RogueDialogue",
    )
    for base in bases:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.json")):
            parts = path.relative_to(base).parts
            scene = parts[-2] if len(parts) >= 2 else path.stem
            try:
                info = _load_json(path)
            except json.JSONDecodeError:
                continue
            for line in _parse_mission(info, sent_map, text_map):
                yield {"category": "rogue", "scene": scene, **line, "meta": ""}


def _extract_rogue_configs(data_dir: Path, text_map: dict) -> Iterator[dict]:
    """模拟宇宙配置文本（祝福、命途、星神故事等）：按文件聚合成一条可检索片段。"""
    excel = data_dir / "ExcelOutput"
    if not excel.exists():
        return
    for path in sorted(excel.glob("Rogue*.json")):
        try:
            data = _load_json(path)
        except json.JSONDecodeError:
            continue
        fields: list[str] = []
        _walk_hash_fields(data, fields)
        texts: list[str] = []
        for field in fields:
            resolved = clean_text(text_map.get(str(field), ""))
            if resolved and resolved not in texts:
                texts.append(resolved)
        if not texts:
            continue
        yield {
            "category": "rogue",
            "scene": path.stem,
            "speaker": "",
            "text": f"模拟宇宙配置「{path.stem}」：" + "；".join(texts)[:1500],
            "meta": "",
        }


def _collect_hash_texts(node: Any, text_map: dict, out: list[str] | None = None) -> list[str]:
    """按出现顺序收集节点里的文本（`{"Hash": n}` 形式），返回**未清洗**的原文。"""
    out = [] if out is None else out
    if isinstance(node, dict):
        if "Hash" in node:
            value = text_map.get(str(node.get("Hash")), "")
            if value and value != "N/A":
                out.append(value)
            return out
        for value in node.values():
            _collect_hash_texts(value, text_map, out)
    elif isinstance(node, list):
        for value in node:
            _collect_hash_texts(value, text_map, out)
    return out


# 图鉴之外、但仍属官方叙事文本的配置：文件 -> (类别, 提示词里的标签)
EXTERNAL_TEXT_SOURCES: dict[str, tuple[str, str]] = {
    "PerformanceSkipOverride.json": ("story_summary", "剧情概要"),
    "MappingInfo.json": ("places", "地点"),
    "LocalbookConfig.json": ("books", "书籍"),
    "ChronicleConclusion.json": ("chronicle", "编年史"),
    "LoadingDesc.json": ("places", "地点"),
    "MonsterAtlasExtraPhase.json": ("monsters", "怪物"),
    "MonsterAtlasExtraPhases.json": ("monsters", "怪物"),
}


def _extract_external_texts(
    data_dir: Path, text_map: dict, category_filter: str | None = None
) -> Iterator[dict]:
    """剧情概要 / 地点描述 / 书籍正文 / 编年史 / 加载界面描述 / 怪物图鉴额外描述。

    这些配置的字段名不统一（`Desc` / `BookContent` / `MonsterIntroduction` / `DescTextmapID`…），
    统一按「递归找 `{"Hash": n}` 并按出现顺序取文本」处理：
    短的当标题，长的按段落拆成独立记录。
    """
    excel = data_dir / "ExcelOutput"
    for name, (category, label) in EXTERNAL_TEXT_SOURCES.items():
        if category_filter and category != category_filter:
            continue
        path = excel / name
        if not path.exists():
            continue
        try:
            data = _load_json(path)
        except json.JSONDecodeError:
            continue
        entries = data if isinstance(data, list) else list(data.values())
        stem = path.stem
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            texts = _collect_hash_texts(entry, text_map)
            if not texts:
                continue
            title = next((t for t in texts if 2 <= len(t) <= 30), "")
            body = [t for t in texts if len(t) >= 25 and t != title]
            if not body:
                continue
            record_id = str(
                entry.get("ID")
                or entry.get("PerformanceID")
                or entry.get("BookID")
                or entry.get("MissionID")
                or index
            )
            if not title:
                title = record_id
            raw = "\n\n".join(body).replace("\\n", "\n").replace("\\r", "")
            paragraphs = [clean_text(part) for part in raw.split("\n\n")]
            for part_index, paragraph in enumerate(p for p in paragraphs if p):
                yield {
                    "category": category,
                    "scene": f"{stem}.{record_id}.{part_index}",
                    "speaker": "",
                    "text": f"{label}「{title}」：{paragraph}",
                    "meta": "",
                }


def _extract_atlas(data_dir: Path, text_map: dict) -> Iterator[dict]:
    """游戏内智库（图鉴）：`ExcelOutput/NounAtlas.json`。

    每条词条按段落拆成独立记录（各占一个 scene，避免被合并成一块），
    并把标题作为前缀带着——即使只检索到某一段，也知道它属于哪个词条。
    """
    path = data_dir / "ExcelOutput" / "NounAtlas.json"
    if not path.exists():
        return
    try:
        data = _load_json(path)
    except json.JSONDecodeError:
        return
    entries = data if isinstance(data, list) else list(data.values())
    for entry in entries:
        title = _resolve_hash(entry.get("NounTitle"), text_map)
        raw_body = (
            text_map.get(str((entry.get("NounDesc") or {}).get("Hash")), "")
            .replace("\\n", "\n")
            .replace("\\r", "")
        )
        if not title or not raw_body:
            continue
        entry_id = str(entry.get("ID") or title)
        # 先按段落切、再清洗：clean_text 会把换行折叠成空格
        paragraphs = [clean_text(part) for part in raw_body.split("\n\n")]
        paragraphs = [part for part in paragraphs if part]
        for index, paragraph in enumerate(paragraphs):
            yield {
                "category": "atlas",
                "scene": f"{entry_id}.{index}",
                "speaker": "",
                "text": f"智库「{title}」：{paragraph}",
                "meta": json.dumps(
                    {"atlas_type": entry.get("Type"), "sort": entry.get("SortID")},
                    ensure_ascii=False,
                ),
            }


def _extract_messages(data_dir: Path, text_map: dict) -> Iterator[dict]:
    excel = data_dir / "ExcelOutput"

    def get(path: str) -> dict[str, Any]:
        return _load_json(excel / path)

    contacts = _index_by(get("MessageContactsConfig.json"), "ID")
    for cid, info in contacts.items():
        name = clean_text(text_map.get(str(info.get("Name", {}).get("Hash", "")), ""))
        if name:
            info["_name"] = name
    groups = _index_by(get("MessageGroupConfig.json"), "ID")
    section_to_contacts: dict[int, list[str]] = {}
    for g in groups.values():
        for sid in g.get("MessageSectionIDList", []) or []:
            section_to_contacts.setdefault(int(sid), []).append(str(g.get("MessageContactsID")))

    sections = _index_by(get("MessageSectionConfig.json"), "ID")
    items = _load_json(excel / "MessageItemConfig.json")
    if isinstance(items, dict):
        items = list(items.values())
    for sid, info in sections.items():
        for item in items:
            if str(item.get("SectionID", "")) != str(sid):
                continue
            main = clean_text(text_map.get(str(item.get("MainText", {}).get("Hash", "")), ""))
            if not main:
                continue
            sender = ""
            if item.get("ContactsID") is not None:
                sender = contacts.get(str(item["ContactsID"]), {}).get("_name", "")
            elif item.get("Sender") == "NPC" and section_to_contacts.get(int(sid)):
                sender = contacts.get(section_to_contacts[int(sid)][0], {}).get("_name", "")
            else:
                sender = str(item.get("Sender", ""))
            yield {
                "category": "messages",
                "scene": str(sid),
                "speaker": sender or "未知",
                "text": main,
                "meta": "",
            }


def _walk_hash_fields(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        if "Hash" in obj:
            out.append(str(obj["Hash"]))
        for v in obj.values():
            _walk_hash_fields(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_hash_fields(v, out)


def _extract_books(data_dir: Path, text_map: dict) -> Iterator[dict]:
    path = data_dir / "ExcelOutput" / "BookSeriesConfig.json"
    if not path.exists():
        return
    books = _index_by(_load_json(path), "BookSeriesID")
    for bid, book in books.items():
        fields: list[str] = []
        _walk_hash_fields(book, fields)
        resolved = [text_map.get(h, "") for h in fields]
        text = " ".join(t for t in resolved if t)
        if text:
            yield {"category": "books", "scene": str(bid), "speaker": "", "text": text, "meta": ""}


def _extract_avatars(data_dir: Path, text_map: dict) -> Iterator[dict]:
    excel = data_dir / "ExcelOutput"
    avatars = _index_by(_load_json(excel / "AvatarConfig.json"), "AvatarID")
    name_by_id = {
        aid: _resolve_hash(av.get("AvatarName"), text_map)
        for aid, av in avatars.items()
        if _resolve_hash(av.get("AvatarName"), text_map)
    }
    skills = _load_json(excel / "AvatarSkillConfig.json")
    skill_by_id: dict[int, list[dict]] = {}
    if isinstance(skills, list):
        for s in skills:
            sid = s.get("SkillID")
            if sid is not None:
                skill_by_id.setdefault(sid, []).append(s)

    for aid, av in avatars.items():
        name = _resolve_hash(av.get("AvatarName"), text_map)
        if not name:
            continue
        full = _resolve_hash(av.get("AvatarFullName"), text_map)
        rarity = av.get("Rarity")
        damage_type = av.get("DamageType")
        base_type = av.get("AvatarBaseType")
        text = f"角色信息：{name}"
        if full:
            text += f"（全名：{full}）"
        if rarity:
            text += f"，{rarity}星"
        if damage_type:
            text += f"，{damage_type}属性"
        if base_type:
            text += f"，{base_type}类型角色"
        yield {"category": "avatars", "scene": aid, "speaker": name, "text": text, "meta": ""}

        for sid in av.get("SkillList", []) or []:
            for s in skill_by_id.get(sid, []):
                sname = _resolve_hash(s.get("SkillName"), text_map)
                if not sname:
                    continue
                sdesc = _resolve_hash(s.get("SkillDesc"), text_map)
                skill_text = f"技能「{sname}」" + (f"：{sdesc}" if sdesc else "")
                yield {
                    "category": "avatars",
                    "scene": aid,
                    "speaker": name,
                    "text": skill_text,
                    "meta": "",
                }

    voice_path = excel / "VoiceAtlas.json"
    if voice_path.exists():
        voice_data = _load_json(voice_path)
        if isinstance(voice_data, list):
            for entry in voice_data:
                if not isinstance(entry, dict):
                    continue
                aid = str(entry.get("AvatarID"))
                name = name_by_id.get(aid)
                if not name:
                    continue
                title = _resolve_hash(entry.get("VoiceTitle"), text_map)
                voice = _resolve_hash(entry.get("Voice_M"), text_map) or _resolve_hash(
                    entry.get("Voice_F"), text_map
                )
                if title and voice:
                    yield {
                        "category": "avatars",
                        "scene": aid,
                        "speaker": name,
                        "text": f"角色语音「{title}」：{voice}",
                        "meta": "",
                    }

    story_path = excel / "StoryAtlas.json"
    if story_path.exists():
        story_data = _load_json(story_path)
        if isinstance(story_data, list):
            for entry in story_data:
                if not isinstance(entry, dict):
                    continue
                aid = str(entry.get("AvatarID"))
                name = name_by_id.get(aid)
                if not name:
                    continue
                story = _resolve_hash(entry.get("Story"), text_map)
                if story:
                    yield {
                        "category": "avatars",
                        "scene": aid,
                        "speaker": name,
                        "text": f"角色档案：{story}",
                        "meta": "",
                    }

    camp_path = excel / "MessageContactsCamp.json"
    config_path = excel / "MessageContactsConfig.json"
    if camp_path.exists() and config_path.exists():
        camps = _index_by(_load_json(camp_path), "ContactsCamp")
        contacts = _index_by(_load_json(config_path), "ID")
        for camp_id, camp in camps.items():
            camp_name = _resolve_hash(camp.get("Name"), text_map)
            if camp_name not in {"星穹列车", "列车组", "星穹列车组"}:
                continue
            members = []
            for cid, contact in contacts.items():
                if str(contact.get("ContactsCamp")) == str(camp_id) and contact.get(
                    "ContactsType"
                ) in (None, 1):
                    name = _resolve_hash(contact.get("Name"), text_map)
                    if name and name not in members:
                        members.append(name)
            if members:
                yield {
                    "category": "avatars",
                    "scene": "crew",
                    "speaker": "",
                    "text": f"列车智库档案：星穹列车列车组成员名录（来自列车通讯录）：{'、'.join(members)}。",
                    "meta": "",
                }


def _extract_main_missions(data_dir: Path, text_map: dict) -> Iterator[dict]:
    excel = data_dir / "ExcelOutput"
    main = _index_by(_load_json(excel / "MainMission.json"), "MainMissionID")
    for mid, m in main.items():
        name = _resolve_hash(m.get("Name"), text_map)
        if not name:
            continue
        text = f"主线任务「{name}」"
        mtype = m.get("Type")
        if isinstance(mtype, str) and mtype:
            text += f"（类型：{mtype}）"
        yield {"category": "missions", "scene": str(mid), "speaker": "", "text": text, "meta": ""}

    subs = _load_json(excel / "SubMission.json")
    if isinstance(subs, list):
        for s in subs:
            sid = s.get("SubMissionID")
            if sid is None:
                continue
            target = _resolve_hash(s.get("TargetText"), text_map)
            desc = _resolve_hash(s.get("DescrptionText"), text_map)
            parts = []
            if target:
                parts.append(f"任务目标：{target}")
            if desc:
                parts.append(f"任务描述：{desc}")
            if parts:
                yield {
                    "category": "missions",
                    "scene": str(sid),
                    "speaker": "",
                    "text": "。".join(parts),
                    "meta": "",
                }


def _extract_items(data_dir: Path, text_map: dict) -> Iterator[dict]:
    excel = data_dir / "ExcelOutput"
    purpose_map: dict[Any, str] = {}
    purpose_path = excel / "ItemPurpose.json"
    if purpose_path.exists():
        for pid, p in _index_by(_load_json(purpose_path), "ID").items():
            purpose_map[str(pid)] = _resolve_hash(p.get("PurposeText"), text_map)

    for path in sorted(excel.glob("ItemConfig*.json")):
        data = _load_json(path)
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            iid = item.get("ID")
            if iid is None:
                continue
            name = _resolve_hash(item.get("ItemName"), text_map)
            if not name:
                continue
            desc = _resolve_hash(item.get("ItemDesc"), text_map) or _resolve_hash(
                item.get("ItemBGDesc"), text_map
            )
            purpose = purpose_map.get(str(item.get("PurposeType")), "")
            text = f"物品「{name}」"
            if desc:
                text += f"：{desc}"
            if purpose:
                text += f"。用途：{purpose}"
            yield {"category": "items", "scene": str(iid), "speaker": "", "text": text, "meta": ""}


def _extract_monsters(data_dir: Path, text_map: dict) -> Iterator[dict]:
    excel = data_dir / "ExcelOutput"
    tpl_by_id: dict[Any, dict] = {}
    tpl_data = _load_json(excel / "MonsterTemplateConfig.json")
    if isinstance(tpl_data, list):
        for t in tpl_data:
            tid = t.get("MonsterTemplateID")
            if tid is not None:
                tpl_by_id[tid] = t

    skill_by_id: dict[Any, dict] = {}
    skill_data = _load_json(excel / "MonsterSkillConfig.json")
    if isinstance(skill_data, list):
        for s in skill_data:
            sid = s.get("SkillID")
            if sid is not None:
                skill_by_id[sid] = s

    cfg_data = _load_json(excel / "MonsterConfig.json")
    if not isinstance(cfg_data, list):
        return
    for m in cfg_data:
        mid = m.get("MonsterID")
        if mid is None:
            continue
        name = _resolve_hash(m.get("MonsterName"), text_map)
        tpl = tpl_by_id.get(m.get("MonsterTemplateID"))
        if not name and tpl:
            name = _resolve_hash(tpl.get("MonsterName"), text_map)
        if not name:
            continue
        rank = tpl.get("Rank") if tpl else None
        text = f"怪物「{name}」"
        if isinstance(rank, str) and rank:
            text += f"（{rank}）"
        intro = _resolve_hash(m.get("MonsterIntroduction"), text_map)
        if intro:
            text += f"：{intro}"
        skills = []
        for sid in m.get("SkillList", []) or []:
            s = skill_by_id.get(sid)
            if not s:
                continue
            sname = _resolve_hash(s.get("SkillName"), text_map)
            if not sname:
                continue
            sdesc = _resolve_hash(s.get("SkillDesc"), text_map)
            skills.append(f"技能「{sname}」" + (f"：{sdesc}" if sdesc else ""))
        if skills:
            sep = "；" if intro else "："
            text += sep + "；".join(skills)
        yield {"category": "monsters", "scene": str(mid), "speaker": "", "text": text, "meta": ""}


def _tag_pom_pom(record: dict) -> dict:
    record["pom_pom"] = record.get("speaker", "") in POM_POM_NAMES
    return record


def extract_all(data_dir: str | Path, out_dir: str | Path) -> None:
    data_dir = Path(data_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    text_map = _load_json(data_dir / "TextMap" / "TextMapCHS.json")
    sent_path = data_dir / "ExcelOutput" / "TalkSentenceConfig.json"
    sent_map = _build_sent_index(_load_json(sent_path)) if sent_path.exists() else {}

    extractors: dict[str, Iterable[dict]] = {
        "story": itertools.chain(
            _extract_story(data_dir, sent_map, text_map),
            _extract_mission_acts(data_dir, sent_map, text_map),
        ),
        "story_summary": _extract_external_texts(data_dir, text_map, "story_summary"),
        "messages": _extract_messages(data_dir, text_map),
        "train_visitor": _extract_train_visitor(data_dir, sent_map, text_map),
        "books": itertools.chain(
            _extract_books(data_dir, text_map),
            _extract_external_texts(data_dir, text_map, "books"),
        ),
        "avatars": _extract_avatars(data_dir, text_map),
        "missions": _extract_main_missions(data_dir, text_map),
        "items": _extract_items(data_dir, text_map),
        "monsters": itertools.chain(
            _extract_monsters(data_dir, text_map),
            _extract_external_texts(data_dir, text_map, "monsters"),
        ),
        "rogue": itertools.chain(
            _extract_rogue(data_dir, sent_map, text_map),
            _extract_rogue_configs(data_dir, text_map),
        ),
        "atlas": _extract_atlas(data_dir, text_map),
        "places": _extract_external_texts(data_dir, text_map, "places"),
        "chronicle": _extract_external_texts(data_dir, text_map, "chronicle"),
    }

    with open(out_dir / "all.jsonl", "w", encoding="utf-8") as all_f:
        for category, records in extractors.items():
            with open(out_dir / f"{category}.jsonl", "w", encoding="utf-8") as cat_f:
                for record in records:
                    record = _tag_pom_pom(record)
                    line = json.dumps(record, ensure_ascii=False)
                    cat_f.write(line + "\n")
                    all_f.write(line + "\n")
