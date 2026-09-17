from app.database import Chunk
from app.persona import build_deep_think_prompt, build_system_prompt, format_context


def _chunk(text: str, category: str = "story", speaker: str = "帕姆") -> Chunk:
    return Chunk(0, category, "s1", speaker, text, speaker == "帕姆", "")


def test_persona_mentions_identity_and_user_address():
    prompt = build_system_prompt([], [], "你好")
    assert "帕姆" in prompt
    assert "列车长" in prompt
    assert "开拓者" in prompt
    assert "开拓者乘客" in prompt
    assert "语气词" in prompt
    assert "自称「帕姆」或「列车长」" in prompt  # official corpus never uses 本帕姆


def test_persona_usage_rules_for_pa_particle():
    prompt = build_system_prompt([], [], "你好")
    assert "代替" in prompt  # 帕 replaces a sentence-final particle
    assert "不要单独" in prompt
    assert "不要与别的语气词连用" in prompt  # never model combined 哦帕 usage


def test_persona_names_knowledge_base_zhi_ku_and_no_real_world():
    prompt = build_system_prompt([], [], "你好")
    assert "列车智库" in prompt
    assert "不要提及现实世界" in prompt
    assert "不要提及智库" in prompt  # self questions must not deflect to 智库


def test_persona_grounding_rules_prevent_fabrication():
    prompt = build_system_prompt([], [], "你好")
    assert "必须能在检索片段中找到依据" in prompt
    assert "不要用游戏外" in prompt or "不要自行补充" in prompt
    assert "玩家可选台词" in prompt


def test_persona_allows_grounded_inference_with_hedging():
    prompt = build_system_prompt([], [], "黑塔空间站是谁建造的？")
    assert "有依据的推断" in prompt
    assert "要说清这是推测" in prompt
    assert "凭空编造" in prompt  # 底线仍在


def test_persona_answers_from_own_knowledge_without_archive_voice():
    prompt = build_system_prompt([], [], "你好")
    assert "直接回答" in prompt
    assert "不要以「根据" in prompt
    assert "第一人称" in prompt
    assert "领航员" in prompt  # crew roles are Pom-Pom's own knowledge


def test_persona_limits_internet_slang_but_allows_a_few_nicknames():
    prompt = build_system_prompt([], [], "银狼怎么配装？")
    assert "网络流行语" in prompt
    assert "别称" in prompt  # 允许少量社区别称
    assert "狼尊" in prompt  # 举例说明可以用
    assert "首次提到用官方名" in prompt
    assert "合计最多两处" in prompt  # 用量限制（按出现次数算，不按不同别称算）
    assert "同一个别称不要反复出现" in prompt


def test_persona_stays_formal_but_keeps_pom_pom_cuteness():
    prompt = build_system_prompt([], [], "你好")
    assert "傲娇" in prompt  # canon personality survives
    assert "「帕」" in prompt  # the particle is not a slang word
    assert "正式礼貌" in prompt


def test_persona_includes_retrieved_context():
    chunks = [_chunk("星穹列车曾经到访过雅利洛-VI", category="story", speaker="姬子")]
    prompt = build_system_prompt(chunks, [], "列车去过哪些星球")
    assert "雅利洛-VI" in prompt
    assert "姬子" in prompt


def test_persona_includes_pom_pom_style_samples():
    samples = [_chunk("列车马上就要出发了哦！", category="story", speaker="帕姆")]
    prompt = build_system_prompt([], samples, "介绍一下自己")
    assert "列车马上就要出发了哦" in prompt


def test_format_context_marks_category():
    text = format_context([_chunk("内容", category="books", speaker="")])
    assert "书籍" in text
    assert "内容" in text


def test_format_context_marks_new_categories():
    text = format_context([_chunk("遗器描述", category="items", speaker="")])
    assert "物品" in text
    assert "遗器描述" in text


def test_format_context_marks_rogue_category():
    text = format_context([_chunk("模拟宇宙的对白", category="rogue", speaker="")])
    assert "模拟宇宙" in text


def test_format_context_marks_player_lines_as_non_canon():
    chunk = _chunk("她是个性格洒脱的摇滚明星", category="messages", speaker="Player")
    text = format_context([chunk])
    assert "玩家台词，非正典" in text


def test_persona_includes_web_guide_results_when_provided():
    prompt = build_system_prompt(
        [],
        [],
        "银狼如何配装？",
        [
            {
                "title": "银狼配装攻略",
                "url": "https://example.com/guide",
                "snippet": "推荐遗器主词条选效果命中。",
            }
        ],
    )
    assert "星间攻略情报" in prompt
    assert "银狼配装攻略" in prompt
    assert "https://example.com/guide" in prompt
    assert "仅供参考" in prompt


def test_deep_think_prompt_keeps_persona_and_adds_loop_rules():
    prompt = build_deep_think_prompt([], max_steps=4)
    assert "列车长" in prompt  # persona core survives
    assert "【内部检索流程（开拓者看不到）】" in prompt
    assert "search_knowledge_base" in prompt
    assert "4" in prompt
    assert "游戏里根本没有" in prompt  # explicit ban on over-claiming
    assert "不要出现「工具」" in prompt  # no tool jargon in the final answer


def test_deep_think_prompt_puts_voice_rules_after_process_rules():
    prompt = build_deep_think_prompt([], max_steps=4)
    process_at = prompt.index("【内部检索流程（开拓者看不到）】")
    voice_at = prompt.index("【说话方式（最重要，回答前再确认一遍）】")
    assert voice_at > process_at  # voice rules sit closest to generation
    assert "高于前面所有流程说明" in prompt
    assert "Markdown" in prompt
    assert "加粗" in prompt
    assert "项目符号" in prompt
    assert "仅供参考" in prompt  # boilerplate tone is banned


def test_deep_think_voice_rules_are_last_and_compact():
    prompt = build_deep_think_prompt([], max_steps=4)
    voice = prompt[prompt.index("【说话方式（最重要，回答前再确认一遍）】") :]
    assert "高于前面所有流程说明" in voice
    assert "Markdown" in voice
    assert "项目符号" in voice
    assert "傲娇" in voice  # cuteness stays, but only the short reminder
    assert len(voice) < 380  # 末尾只留强调，不重复 PERSONA_CORE 的整段规则


def test_persona_rules_are_not_duplicated():
    prompt = build_deep_think_prompt([], max_steps=4)
    # 压缩目标：同一条规则只写一次（PERSONA_CORE 负责），尾部不复读
    assert prompt.count("网络流行语") == 1
    assert prompt.count("专属光锥") == 1
    assert prompt.count("列车补给凭证") == 1


def test_deep_think_prompt_includes_style_samples():
    samples = [_chunk("列车马上就要出发了帕！", category="story", speaker="帕姆")]
    prompt = build_deep_think_prompt(samples)
    assert "列车马上就要出发了帕！" in prompt
    assert "【帕姆台词风格参考】" in prompt


def test_deep_think_prompt_requires_searching_before_giving_up():
    prompt = build_deep_think_prompt([], max_steps=4)
    assert "也要先查" in prompt


def test_deep_think_prompt_requires_the_standard_no_record_sentence():
    prompt = build_deep_think_prompt([], max_steps=4)
    assert "原样带上「智库里没有这段记录」" in prompt
