"""从游戏配置生成 app/aliases.py（星神↔命途＋人工补充的语料别称）。

数据来源：data/StarRailData/ExcelOutput/RogueAeonDisplay.json（星神名 / 命途名 / 命途名+星神）
          + 本文件里的 MANUAL 补充（语料与社区常用叫法，如「乐子神」= 阿哈）

用法：
  .venv\\Scripts\\python.exe scripts/build_aliases.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "StarRailData"
TARGET = ROOT / "app" / "aliases.py"

# 语料/社区里出现、但配置里没有的叫法（key 必须是配置里的星神名）
MANUAL: dict[str, tuple[str, ...]] = {
    "阿哈": ("乐子神", "欢愉之神", "「欢愉」", "欢愉"),
    "克里珀": ("「存护」", "筑城者"),
    "纳努克": ("「毁灭」", "毁灭者"),
    "浮黎": ("「记忆」",),
    "Ⅸ": ("「虚无」", "第九机关"),
    "药师": ("「丰饶」",),
    "岚": ("「巡猎」",),
    "博识尊": ("「智识」",),
    "希佩": ("「同谐」",),
    "塔伊兹育罗斯": ("「繁育」",),
    "太一": ("「秩序」",),
    "奥博洛斯": ("「贪饕」",),
    "互": ("「均衡」",),
    "迷思": ("「神秘」",),
    "阿基维利": ("开拓星神", "「开拓」", "开拓"),
}


def resolve(text_map: dict, field) -> str:
    if not isinstance(field, dict):
        return ""
    return text_map.get(str(field.get("Hash")), "")


def load_aeon_groups() -> list[tuple[str, ...]]:
    text_map = json.loads(
        (DATA / "TextMap" / "TextMapCHS.json").read_text(encoding="utf-8")
    )
    data = json.loads(
        (DATA / "ExcelOutput" / "RogueAeonDisplay.json").read_text(encoding="utf-8")
    )
    items = data if isinstance(data, list) else list(data.values())
    groups: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for item in items:
        name = resolve(text_map, item.get("RogueAeonName"))
        if not name or name in seen:
            continue
        seen.add(name)
        members = [name]
        for key in ("RogueAeonPathName", "RogueAeonPathName2"):
            path = resolve(text_map, item.get(key))
            if path and path not in members:
                members.append(path)
        for extra in MANUAL.get(name, ()):
            if extra not in members:
                members.append(extra)
        groups.append(tuple(members))
    # 配置里没有、但语料常出现的（如阿基维利）
    for name, extras in MANUAL.items():
        if name in seen:
            continue
        groups.append((name, *extras))
    return groups


def render(groups: list[tuple[str, ...]]) -> str:
    lines = [
        '"""星神/命途别称表。',
        "",
        "由 scripts/build_aliases.py 从 ExcelOutput/RogueAeonDisplay.json 生成，",
        "外加人工补充的语料/社区别称。**不要手改**：改了配置后重跑生成脚本。",
        '"""',
        "from __future__ import annotations",
        "",
        "ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (",
    ]
    for group in groups:
        body = ", ".join(json.dumps(member, ensure_ascii=False) for member in group)
        lines.append(f"    ({body},),")
    lines.append(")")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    groups = load_aeon_groups()
    TARGET.write_text(render(groups), encoding="utf-8")
    print(f"已写入 {TARGET}（{len(groups)} 组）")
    for group in groups[:4]:
        print("   ", " / ".join(group))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
