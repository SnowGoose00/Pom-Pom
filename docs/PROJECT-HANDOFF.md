# Pom-Pom Agent 项目交接文档

> 用途：在新的会话/新同事接手时，快速恢复上下文并继续开发。
> 最后更新：2026-09-13（对应分支 `feat/pom-pom-agent`，61 个提交，工作区干净）

---

## 1. 项目是什么

以《崩坏：星穹铁道》列车长**帕姆（Pom-Pom）**为原型的本地 AI 对话 Agent：

- 完整帕姆人设（人设规则由游戏语料分析得出，见 `docs/pom-pom-persona.md`）；
- 本地向量知识库（八类游戏语料）+ 智能体式迭代检索；
- 养成/配装/配队类问题自动联网检索攻略（搜狗）；
- 可选的**深度思考模式**：ReAct 循环反复查智库/联网/读网页，直到攒够依据再作答，查不到如实说明；
- FastAPI 后端 + 星穹列车风网页前端，支持公网临时分享（Cloudflare Quick Tunnel）；
- 模型走 OpenAI 兼容接口，当前默认 **DeepSeek V4 Flash**。

设计与实施背景：
- 需求/设计文档：`DESIGN.md`
- 实施计划（已完成）：`docs/superpowers/plans/2026-09-01-pom-pom-agent.md`
- 深度思考模式设计/计划：`docs/superpowers/specs/2026-09-10-deep-thinking-mode-design.md`、`docs/superpowers/plans/2026-09-10-deep-thinking-mode.md`
- 人设档案：`docs/pom-pom-persona.md`

---

## 2. 当前状态一览

| 项目 | 状态 |
|---|---|
| Git 分支 | `feat/pom-pom-agent`（62 个提交，工作区干净；尚未合并 main，无远程） |
| 自动化测试 | **165 passed**（`pytest`，结果自动存 `test-results/`） |
| 统一评测集 | `eval/testset.json` 65 题 / 10 轴：快档 **28/30**、普通档 **28/29**、深度档 **6/6**（3 条已知缺口记录在案） |
| 实体探针 | **25/25 命中**，平均返回 9.0 条、同场景最多平均 1.56（已并入统一集，脚本保留兼容） |
| 深度思考模式 | 已上线：ReAct 工具循环 + SSE 思考轨迹；真实链路 smoke **5/5 PASS**（`test-results/latest-deep-smoke.md`） |
| 联网攻略 | 多源链 **博查（带 key，优先）→ DuckDuckGo → 360 → 搜狗**，节流/冷却/缓存齐备 |
| 剧情事实探针 | **7/7 PASS**（重建库后重跑，`test-results/latest-plot-probes.md`） |
| 18 题验收 | 16 题正常 + 2 题因当时限流重试后兜底（`test-results/acceptance-2026-09-03_223323.md`） |
| 知识库 | 260,577 条记录 → **102,243 个向量片段**（SQLite + sqlite-vec，231.3 MB） |
| 对话覆盖率 | `TalkSentenceConfig` 240,488 条对话，本地能引用到 **125,518 条（52.2%）**，扩充前是 7.5% |
| 当前运行状态 | 本地服务运行中（`127.0.0.1:8000`，2026-09-14 21:0x 重启）；无 cloudflared 进程，公网链接已失效 |

数据分布（`data/dialogues/all.jsonl`）：

```
总计 260,577（2026-09-13 扩充剧情概要 / 地点 / 书籍正文 / 编年史后）
story 182,544 | messages 13,255 | rogue 12,925 | books 12,100 | avatars 11,923
missions 10,218 | items 5,017 | story_summary 3,688 | train_visitor 3,504
monsters 2,657 | places 1,894 | chronicle 434 | atlas 418
```

---

## 3. 目录结构与职责

```
D:\Pom-Pom
├── DESIGN.md                 原始设计文档（需求、验收集、决策）
├── README.md                 使用说明（运行、配置、测试、探针）
├── requirements.txt          Python 依赖
├── .env / .env.example       配置（.env 已被 gitignore，含真实 Key）
├── conftest.py               每次 pytest 自动保存结果到 test-results/
├── pytest.ini                测试配置（工作区临时目录、关闭 cacheprovider）
├── app/
│   ├── config.py             环境变量 → Settings（模型、Key、开关、DEBUG）
│   ├── embedder.py           本地 embedding（fastembed + bge-small-zh，512 维）
│   ├── database.py           SQLite + sqlite-vec；search / search_speaker / avatar_names
│   ├── extract.py            从 StarRailData 提取 13 类语料 → JSONL
│   ├── import_data.py        切块 + 向量化 + 幂等写库
│   ├── retrieval.py          智能体式迭代检索（重写查询、反思、旅行广播、实体提取）
│   ├── persona.py            帕姆人设提示词与上下文拼装
│   ├── websearch.py          联网检索多源链（博查 → DuckDuckGo → 360 → 搜狗）
│   ├── deepthink.py          深度思考：ReAct 工具循环（智库/联网/读网页）+ 无答案协议
│   ├── chat.py               RAG 对话引擎（检索→提示词→LLM→兜底，含诊断；stream_reply 事件流）
│   └── main.py               FastAPI：/api/chat /api/chat/stream /api/health /api/debug/*
├── static/                   index.html / style.css / app.js（前端）
├── scripts/
│   ├── fetch_data.ps1        拉取最小数据集（sparse checkout + raw 兜底）
│   ├── fetch_extra_files.py  并发拉取 sparse 之外的语料（--group mission|rogue；git 通道不通时用）
│   ├── run_tests.ps1         跑测试并保存完整日志
│   ├── run_eval.py           统一评测集入口（--tier fast|llm|deep，--axis / --id / --limit）
│   ├── run_acceptance.py     18 题验收（兼容保留，用例已并入统一集）
│   ├── run_plot_probes.py    剧情事实探针（兼容保留）
│   ├── run_deep_smoke.py     深度思考 smoke（兼容保留）
│   ├── run_entity_probes.py  实体检索探针（兼容保留）
│   ├── start_server.ps1      一行启动/停止服务（-Debug / -Background / -Stop / -BindHost / -Port）
│   ├── entity_probes.json    实体探针定义（must_any）
│   └── plot_probes.json      剧情探针定义（must_any / must_all / avoid）
├── eval/
│   └── testset.json          统一评测集（轴 × 档位，单一事实来源）
├── tests/                    10 个测试文件，165 用例
├── docs/
│   ├── pom-pom-persona.md    人设档案（统计证据 + 规则 + 提示词全文）
│   ├── PROJECT-HANDOFF.md    本文档
│   └── superpowers/plans/…   实施计划
├── test-results/             测试/验收/探针/服务器日志（gitignore）
├── data/                     StarRailData / dialogues / models / pom.db（gitignore）
└── tools/cloudflared.exe     公网隧道工具（gitignore，54MB）
```

---

## 4. 数据管线

**任务演出脚本扩充（2026-09-12）**：原先只取 `/Story` + `/Config/Level/Mission/TrainVisitor`，
而大量主线/支线对白只写在 `Config/Level/Mission/**/Act*.json`（上游 32,156 个文件 / 87.6 MB，
其中 `Act*` 14,746 个 / 61.7 MB 才是对白载体，`Mission*`/`MissionInfo*` 基本零对白）。
github.com 的 git 通道会 `Connection was reset`，因此新增 `scripts/fetch_mission_acts.py`：
用 GitHub tree API 取文件清单（缓存到 `data/StarRailData/.mission_act_filelist.json`），
再用 16 线程从 raw.githubusercontent.com 拉取（实测 41 文件/秒，14,271 个文件 6.1 分钟，零失败）。
提取端新增 `_extract_mission_acts`（跳过 TrainVisitor 以免重复），产出并入 `story` 类别，
`scene` 取任务 id —— 与 `Story/**` 同 id，所以两层重复会被 `_dedupe_records`/`_dedupe_chunks` 自动折叠。
效果：提取记录 84,504 → **225,750**（story 37,148 → 178,394），对话覆盖率 **7.5% → 50.3%**，
入库片段 27,175 → **50,447**（零重复，120.6 MB），剧情探针 7/7、深度 smoke 5/5 仍全绿。

**Ruby 注音规则（2026-09-12）**：`{RUBY_B#注音}正文{RUBY_E#}` 中正文才是显示内容
（如 `#死亡# + 塞纳托斯`），原实现把注音也拼进去（"死亡塞纳托斯"）。现在只保留正文，
但注音以「泰坦」结尾时保留成「正文（注音）」——因为「纷争之泰坦」「理性之泰坦」这类
泰坦称谓只存在于注音里，丢掉会让检索命中归零（实测「之泰坦」19 → 284 条）。

**模拟宇宙扩充（2026-09-12）**：下载 `Config/Level/Rogue**`（1,984 文件）+ `Config/Level/RogueDialogue**`（624）+ `ExcelOutput/Rogue*.json`（226），
共 2,834 个文件 / 1.3 分钟。提取端新增 `_extract_rogue`（对白，独立类别 `rogue`，提示词里标为【模拟宇宙】）
与 `_extract_rogue_configs`（祝福/命途/星神故事等配置文本，按文件聚合成一条可检索片段）。
坑：模拟宇宙用自己的任务类型 `PlayRogueSimpleTalk` / `PlayAndWaitRogueSimpleTalk` / `PlayAeonTalk`（7,507 处），
原来按 `$type` 精确匹配的解析器全漏了（只提到 2,035 条）；现改为**按字段判断**——只要节点带 `SimpleTalkList` 就解析，
`OptionList` 里带 `TalkSentenceID` 的才算台词（`PlayRogueOptionTalk` 的选项走 `OptionTextmapID`，属玩家选项，跳过）。
修正后 rogue 提取 12,925 条，入库 1,836 个片段（事件文本高度复用，去重比例高）。
实测问答：「模拟宇宙的奇物是什么」能答出机械生命修奇物、降维骰子等细节。

**出口清洗补充（2026-09-12）**：模型偶尔在中文回答里崩出西里尔字母词（实测出现过 "подробности"），
`clean_reply()` 现在会去掉这类残词。

**实体字面检索（2026-09-12）**：向量检索会把「实体名」漏掉——实测问「差分宇宙里的鲁珀特是什么？」，
库里有 64 个含「鲁珀特」的片段，但向量 top-k 全被「差分宇宙」同类片段占满，回答只能说没找到。
现在 `retrieval.entity_terms()` 会从问题里拆出实体候选（按 `的/里/和/与` 等切分、去掉疑问词尾、
过滤停用词，最短最具体的排前面），`chat._retrieve` 对每个候选做一次
`VectorDB.search_text()`（SQL LIKE，通配符已转义，距离用合成值 `LITERAL_MATCH_DISTANCE=0.25`，
按片段长度升序 = 越聚焦越靠前），再与向量结果一起 `merge_ranked`。
效果：同一句问题现在能答出「鲁珀特二世是智械/天才、与螺丝咕姆同为智械伙伴、黑塔收集学派战争数据要复现权杖系统」。
诊断字段新增 `literal_terms`。

**别称表与词表抽取（2026-09-12 第二批）**：「阿哈在列车上干过什么」查不到「把列车炸成两截」，
根因有三层：① 语料里这件事只写成「**乐子神**…把列车炸成两截」（阿哈/乐子神/「欢愉」三种写法互不命中）；
② 规则切分对「A在B上做过什么」失效，抽不出实体；③ 那条片段与问题相似度只有 0.48，
任何"按问题相似度排序"的通道都排不上它。改法：

- **别称表**：`scripts/build_aliases.py` 从 `ExcelOutput/RogueAeonDisplay.json` 生成 `app/aliases.py`
  （15 组星神↔命途，如 阿哈 = 欢愉星神 = 欢愉 = 乐子神 =「欢愉」），人工补语料别称；`expand_aliases()` 展开检索。
- **实体词表匹配**：`VectorDB.entity_vocabulary()` 用角色名（speaker）＋配置名（物品/怪物/任务「X」）建表
  （当前 7,862 条），`vocabulary_matches()` 做最长匹配；规则切分降级为兜底。实测
  「阿哈在列车上干过什么」能抽出 `['阿哈']`（原来是「阿哈在列车」废词），「螺丝咕姆怎么评价黑塔」不再返回空。
- **字面命中排序**：`search_text`/`search_text_terms` 在有查询向量时按语义相似度**在通道内**排序
  （候选池 200；按长度排序会把"长但相关"的片段截掉），但对外仍用 `LITERAL_MATCH_DISTANCE=0.25` 的精确匹配权重。
- **别名提示**：深度模式的 `search_knowledge_base` 观测里追加「阿哈 也叫 乐子神 /「欢愉」星神」，
  模型据此自己改写成「阿哈 把列车炸成两截 阿基维利」再查——实测深度模式已能正确回答。

**邻接扩展与字面保底（2026-09-12 第三批）**：

- `VectorDB.scene_neighbors()`：取同场景相邻 id 的片段（场景内 id 是连续分配的），
  命中片段后自动带上最多 `NEIGHBOR_BUDGET=3` 条邻居，治「那个家伙」这类共指被切块切断的问题；
  邻居同样受「单场景不超过一半」的配额约束。
- **字面保底席位**：每个实体词至少保证 1 条字面命中进上下文（`LITERAL_SEATS=2`）。
  没有这条时，同一个问题会**时好时坏**——因为相似度不足会触发模型反思检索（LLM 生成补充词），
  每次候选不同，边界上的片段就被随机挤进挤出。
- **词表命中不丢弃规则候选**：`entity_terms` 现在把词表命中和规则切分的候选**合并**。
  只分词表会漏掉不在表里的实体——实测问「差分宇宙里的鲁珀特是什么」时，
  词表只认出「差分宇宙」（4 字优先于「鲁珀特」），把规则抽出的「鲁珀特」整条挤掉了。

**教训（回退了两处过拟合）**：中途试过"实体×线索词联合检索 + 字面高权重(0.25) + 专题保底席位"，
结果 `rupert` 探针从 PASS 掉成 MISS（22/23 → 21/23）。已回退，改用上面这套（更保守但都说得通）。
现在**实体探针 23/23**（连跑两次稳定）、剧情探针 7/7、深度 smoke 5/5。

**入库去重（2026-09-12）**：提取出的配置类语料（怪物/技能/任务/物品）会把同一段文字按多个 ID 各存一份——
实测 41,198 个片段里 26% 是重复文本（monsters 74%、avatars 50%、missions 32%、items 18%；剧情与短信只有 1~2%）。
现在入库分两步去重：`_dedupe_records` 先丢掉同一场景内完全相同的行，`_dedupe_chunks` 再按 `(category, text)`
归一（保留首次出现的那条，其余场景写进 `meta.aliases`，最多 12 个，另有 `meta.alias_count` 记总数）。
结果：**41,198 → 27,175 片段（-34%）**，库文件 98.7 MB → **64 MB**（VACUUM 后）；
同一查询的 top-5 从「2~3 条不同文本」变成「5 条全不同」，重复文本不再霸占检索名额。

**切分策略修正（2026-09-13）**：旧策略是「同场景最多 4 行 / 220 字」合并，实测有两个问题：

1. **说话人归属被破坏**：片段里 52% 含 ≥2 个说话人，而 `speaker` 字段只记首行 → 角色路由
   （`search_speaker`）大量漏检：**丹恒漏 59%、三月七 56%、姬子 55%、黑塔 45%、帕姆 38%**。
2. **长文本按 220 字硬切**：2,549 条记录（1.0%）被从句中切开，碎片没有完整语义。

现在改为：
- **按说话人切块**：只有**同一说话人的连续行**才合并（仍 ≤4 行 / ≤220 字），换人就切块 →
  一个片段只属于一个说话人。
- **按句末标点切长文本**：优先在 `。！？；…` 处切，只有单句本身超长才硬切。

结果：片段 52,965 → **85,344**（1.6x，平均 67 字/块），库 126 → 193 MB，入库约 25 分钟；
**角色台词漏检从 38~59% 降到 0~9%**（帕姆 1%、姬子 4%、丹刑 1%、三月七 1%、星期日 0%、黑塔 9%）；
探针全部保持：实体 23/23、剧情 7/7、深度 smoke 5/5、单测 155。
（旧库备份在 `data/pom.db.pre-speakerchunk.bak`，gitignored，可回滚。）

**智库图鉴入库（2026-09-13）**：游戏内「智库」的数据源是 **`ExcelOutput/NounAtlas.json`**（Noun Atlas，名词图鉴），
**100 条词条 / 4.5 万字**：Type 1 名词·地名·人物·科技·现象（43 条）、Type 2 星神（19 条）、Type 3 派系（38 条），
字段为 `ID/Type/SortID/NounTitle/NounDesc/RelatedTerms`。

- 提取：新增 `_extract_atlas()`，每条**按段落拆成独立记录**（各占一个 scene，避免被合并），
  标题写成前缀 `智库「丹轮寺 - 均衡」：…` → **418 条记录 → 386 个片段**，类别 `atlas`（提示词里标为【智库】）。
- 排查过程：一开始按 `DataBank/Codex/Archive` 搜文件名一无所获，最后**把 ExcelOutput 全量拉下来（2,185 文件 / 253 MB）
  再本地 grep 词条文本**才定位到 `NounAtlas`。为此给 `scripts/fetch_extra_files.py` 加了 `--group excel`（全量 ExcelOutput）。
- 顺手修的坑：真实 TextMap 里的换行是**字面 `\n` 两个字符**（不是真换行），既让词条段落切不开，
  也让所有语料文本里混着字面 `\n`。`clean_text()` 现在先还原成换行再折叠空白。
- 效果：以前完全答不出的「丹轮寺是什么」现在能答出（漂游太空的庙刹、收敛遗骨超度逝者、持戒师摩腾的偈语…）；
  「「欢愉」的阿哈是什么样的星神」「无名客是什么」「裂界是什么」也都改用词条作答。

**第二批叙事文本入库（2026-09-13）**：把 ExcelOutput 全量（2,185 文件）扫了一遍，
统计每个配置引用的「长文本（≥25 字）」条目，与已入库配置对比 → **缺口 38,311 条 / 22%**。
按价值筛出「绿色批次」入库（**塔罗书剧情按用户要求排除**）：

| 配置 | 计入类别 | 内容 | 提取记录 |
|---|---|---|---|
| `PerformanceSkipOverride.json` | `story_summary`【剧情概要】 | **每段演出的概要**（相当于剧情摘要索引） | 3,688 |
| `MappingInfo.json` | `places`【地点】 | 地点/造物描述 | 1,894 |
| `LoadingDesc.json` | `places`【地点】 | 加载界面世界观/命途描述 | （并入上行） |
| `LocalbookConfig.json` | `books`【书籍】 | 书籍正文（与 BookSeriesConfig 是两套书库，976 本） | 11,289 |
| `ChronicleConclusion.json` | `chronicle`【编年史】 | 任务结语 | 434 |
| `MonsterAtlasExtraPhase(s).json` | `monsters`【怪物】 | 怪物图鉴额外阶段描述 | 8 |

提取器 `_extract_external_texts()`：字段名不统一（`Desc` / `BookContent` / `MonsterIntroduction` / `DescTextmapID`…），
统一「递归找 `{"Hash": n}` 按出现顺序取原文 → 短的当标题、长的按段落拆成独立记录 + 标签前缀」。
效果：记录 243,264 → **260,577**，片段 85,681 → **102,243**，库 193.6 → **231.3 MB**（入库约 35 分钟）；
实测「卡芙卡来黑塔空间站做了什么」「拟造花萼是什么」「花语手册里写了什么」都能用新来源作答。

**仍未入库的 22%**：主要是战斗数值（`StatusConfig`/`MazeBuff`/各种 `*Skill`，含 `#1[i]%` 占位符，会污染检索）
与教学/成就文本；限时活动类（`ActivityPanel`/`LimaoNews*`/`ClockParkCard*`/`GridFight*`，约 3,000 条）按需再定。

**第二批的验证与收尾（2026-09-13）**：重建库后逐项回归 —— 单测 **157 passed**、实体探针 **25/25**、
剧情探针 **7/7**、深度思考 smoke **5/5**；另加四条新来源的端到端实测（打印进入提示词片段的类别）：
「拟造花萼是什么」→ `places` 3 条（`MappingInfo` 地点词条）、「花语手册里写了什么」→ `books` 4 条（`LocalbookConfig`）、
编年史类提问 → `chronicle` 3 条、「卡芙卡来黑塔空间站做了什么」→ `story` 命中卡芙卡与黑塔的对话。

顺手修掉的三处小问题：

- 四个探针脚本（`run_entity_probes` / `run_plot_probes` / `run_deep_smoke` / `run_acceptance`）启动时把 stdout/stderr 设为 UTF-8：
  本机控制台是 GBK，而探针问题里有官方写法「阮•梅」的 `•`，会在打印汇总行时抛 `UnicodeEncodeError`（探针本身是过的，但结果文件写不出来）。
- 人设提示词把别称限额改成「**一段回答里合计最多两处，同一个别称不要反复出现**」：此前只说「一两个」，
  模型把「专武」重复用两次、加上「狼尊」共三处，超过人设上限（首次 smoke 因此 FAIL）；对应单测断言同步更新。
- 文档里过期的提交数、数据分布与运行状态。

**统一评测集（2026-09-14）**：原先行为级用例散在四个脚本里
（`run_entity_probes` 25 条实体探针、`run_plot_probes` 7 条剧情探针、`run_acceptance` 18 题验收、
`run_deep_smoke` 5 条深度 smoke），验收集还**没有任何自动断言**（只记录回答、靠人看）。
现在合并成单一事实来源 `eval/testset.json`（**65 题 / 10 轴 / 3 档**），由 `scripts/run_eval.py` 驱动：

| 档位 | 跑什么 | 用例 | 是否需要 Key | 首轮结果 |
|---|---|---:|---|---|
| `fast` | 只跑检索，不调模型 | 30 | 否 | **28/30**（+2 条已知缺口） |
| `llm` | 普通问答全链路 | 29 | 是 | **28/29**（+1 条已知缺口） |
| `deep` | 深度思考（SSE，需服务在跑） | 6 | 是 | **6/6** |

轴的划分参考了几个公开评测集的做法（依据见 README 的表）：
RGB（噪声鲁棒性 / 否定拒绝 / 信息整合 / 反事实鲁棒性）、CRAG（问题类型与分层）、
CharacterEval（角色扮演多维指标）、RAGChecker（检索端 vs 生成端分组）、GAIA（按工具需求分级）、
Ragas（测试集要素与防漂移）。本项目落了 10 个轴：
`grounding` / `noise` / `rejection` / `integration` / `counterfactual` /
`persona` / `style` / `safety` / `tooling` / `multilingual`。

断言全部是确定性字符串与计数检查（上下文命中、回答要点覆盖、无答案协议、拒绝话术、
联网工具调用、工具轮数、语言跟随、人设与风格），**不引入 LLM 判官**，保证可重复、零额外成本。
反事实轴用「纠正窗口」判断：允许把错误说法写进纠正句（"不是舰长，是领航员"），
禁止的是附和行为——纯子串禁用会误伤正确行为（这是首轮跑出来的教训）。

两条设计约束：① `tests/test_eval_set.py` 校验用例结构并断言统一集**始终是旧探针脚本的超集**，
合并过程不会悄悄丢覆盖（它当场抓到过一次题面转录错误）；② 已知缺口用 `known_gap` 字段记录，
报告里单列、不计失败、但会一直显示。当前三条缺口：

报告（`test-results/eval-<档位>-<时间戳>.md`）分四段：分轴结果 / 已知缺口 / 未通过用例 / 用例明细，
后三段逐题写出**问题原文与实际输出**（普通与深度档给完整回答＋工具轨迹，检索档给命中片段摘要），
只看 `.md` 就能判断答得对不对、为什么判失败。
`test-results/latest-eval.md` 是**目录**（一行一个档位，指向最近一次完整运行），
按档位的最新副本是 `latest-eval-<档位>.md|jsonl`——早先 `latest-eval.md` 会被"最后跑的那一档"覆盖，
跑完 fast 就看不到 llm 的回答了，已改掉。
`run_eval.py --from-jsonl <某次结果.jsonl>` 可用既有结果重渲染报告，不重跑、不再花 token。

- `noise_fact_aha_strict` / `noise_fact_danlun_strict`：问题里加噪声后实体仍在，但
  「炸成两截」那段、丹轮寺智库词条被其他同类片段挤掉（同题无噪声时能召回）；
- `acc_visited_worlds`：「列车去过哪些世界」在普通模式下只稳定召回匹诺康尼一个目的地，
  列不出雅利洛-VI／仙舟／翁法罗斯；**同一问题在深度思考档是通过的**，
  说明缺口在普通检索链路（列车广播通道），不在知识库。

数据源：`DimbreathBot/TurnBasedGameData`（中文 CHS）。

```powershell
# 1) 拉取最小数据集（约 80–100MB）
powershell -ExecutionPolicy Bypass -File scripts/fetch_data.ps1

# 2) 提取八类语料
.venv\Scripts\python.exe -c "from app.extract import extract_all; extract_all('data/StarRailData','data/dialogues')"

# 3) 向量化入库存
.venv\Scripts\python.exe -c "from app.import_data import import_from_dialogues; from app.embedder import Embedder; print(import_from_dialogues('data/dialogues','data/pom.db',Embedder('data/models')))"
```

要点：
- `fetch_data.ps1` 用 sparse checkout 只取需要的目录；若 github.com 直连失败，会自动用 `raw.githubusercontent.com` 补齐 ExcelOutput 文件；
- 提取的八类：story（含 Story/Discussion 剧情）、messages、train_visitor、books、avatars（角色档案/技能/**语音线 VoiceAtlas**/**角色档案 StoryAtlas**/**列车组成员名录**）、missions、items、monsters；
- 入库时长 ≈ 10 分钟（4 万条嵌入，CPU）；重复执行是幂等的（会清空重建）。

---

## 5. 运行与部署

### 5.1 配置（`.env`，不要提交）

| 变量 | 当前值 | 说明 |
|---|---|---|
| `LLM_API_KEY` | `sk-…`（DeepSeek） | 也兼容旧的 `ZHIPU_API_KEY` 作为回退 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容地址 |
| `LLM_MODEL` | `deepseek-v4-flash` | 可换 `glm-4.7-flash` 等 |
| `WEB_SEARCH_ENABLED` | `true` | 养成/配装/配队问题联网攻略 |
| `DEBUG` | 依启动方式 | `true` 时打印诊断并开放 `/api/debug/*` |
| `DEEP_THINK_MAX_STEPS` | `6` | 深度思考最多 LLM 轮数（其余 `DEEP_THINK_*` 见 README） |

### 5.2 本地启动

```powershell
# 推荐：一行启动脚本（自动清理旧实例 + 健康检查；-Debug / -Background / -Stop / -BindHost / -Port）
powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Debug -Background

# 等价的手动方式
# 普通模式
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# DEBUG 模式（建议本机排查时用；UTF-8 输出避免日志编码问题）
$env:DEBUG="true"; $env:PYTHONIOENCODING="utf-8"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

后台启动（隐藏窗口 + 日志落盘）：

```powershell
$env:DEBUG='true'; $env:PYTHONIOENCODING='utf-8'
Start-Process -FilePath 'D:\Pom-Pom\.venv\Scripts\python.exe' `
  -ArgumentList '-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000' `
  -WindowStyle Hidden `
  -RedirectStandardOutput 'D:\Pom-Pom\test-results\server-debug.out.log' `
  -RedirectStandardError  'D:\Pom-Pom\test-results\server-debug.err.log'
```

### 5.3 公网分享（Cloudflare Quick Tunnel）

```powershell
D:\Pom-Pom\tools\cloudflared.exe tunnel --url http://127.0.0.1:8000 --no-autoupdate
# 日志里会给出 https://xxxx.trycloudflare.com 形式的临时地址
```

注意：
- 临时地址随进程销毁而失效；每次重启都是**新地址**；
- `tools/cloudflared.exe` 未纳入版本控制（需重新下载，官方 54MB；用 `curl -C -` 断点续传更稳）；
- 若需局域网访问但打不开，需在管理员 PowerShell 放行端口：
  `netsh advfirewall firewall add rule name="Pom-Pom Chat 8000" dir=in action=allow protocol=TCP localport=8000`

### 5.4 停止服务

```powershell
# 停 uvicorn
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
  Where-Object { $_.CommandLine -like '*uvicorn*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
# 停隧道
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*cloudflared.exe*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

---

## 6. 核心实现要点（改动前务必理解）

### 6.1 人设（`app/persona.py` + `docs/pom-pom-persona.md`）

- 出自 275 条帕姆台词（去重 206 条）的语料分析：句尾口癖「帕」（21%）、**从不自称「本帕姆」**、称用户「开拓者乘客」、把成员称「乘客」；
- 规则要点：
  - 「帕」是句尾语气词，代替「哦/呢/啦」，**不单用、不连用（禁止「哦帕」）**；
  - 知识库正式名是「列车智库/智库」，不叫「资料库」；
  - **帕姆熟悉的事（列车、列车组、自己与成员的关系）直接回答**，不用「根据列车智库的记录」这类转述腔；自身/关系问题用第一人称，不引用第三人评价；
  - 剧情事实必须来自检索片段；玩家可选台词（Player/PlayerAuto）非正典；
  - 不提现实世界、游戏外或 AI 身份；超纲问题用帕姆口吻带开；
  - 养成/配装/配队类问题可综合「星间攻略情报（联网）」回答。
  - **语气收敛（2026-09-12）**：禁用网络流行语、网络梗与社区黑话（摆烂/破防/开挂/整活/赛博/喂装备/带飞），游戏术语用官方说法（「专属光锥」不说专武、「攻略图解」不说一图流、「列车补给凭证」不说大月卡），角色/装备不用社区外号（狼尊这类）；但「帕」粒子、短句、偶尔傲娇**保留**——那是官方人设不是网络用语。规则同时写在 `PERSONA_CORE`（普通模式）与 `POM_POM_VOICE`（深度模式提示词末尾，权重最高），smoke 里有「没有网络用语/社区黑话」断言。
  - **提示词压缩（2026-09-12）**：`PERSONA_CORE` 1567→1173 字（-25%）、深度尾部 `POM_POM_VOICE` 567→311 字（-45%）、深度系统提示词 2753→2043 字（-26%，约省 690 token/次）。做法：**同一条规则只写一次**（禁网语/官方术语等只在 `PERSONA_CORE`，尾部只留「最后强调」），合并重复条目、删冗长例句；规则一条没少（测试里 47 条断言短语全保留），并用 `test_persona_rules_are_not_duplicated` 防止再次复读。
  - **无答案句式（2026-09-12）**：压缩后模型会用「还没写进去」等自由说法，`no_answer` 判定漏检；改为在 `DEEP_THINK_RULES` 里要求回答**原样带上「智库里没有这段记录」**，并给 `_NO_ANSWER_PATTERNS` 补了「没写进去/没收录/没有这段」三类兜底模式。

### 6.2 检索（`app/retrieval.py` + `app/chat.py`）

- 首轮向量检索 top-k=6；相似度 <0.6 判定「一次查询不足」；
- 迭代：实体词重检索（`entity_query`）→ 必要时模型反思生成补充检索词（≤1 轮）；
- 角色名路由：问题含角色名时用 `db.search_speaker()` 在该角色台词**子集内**排序（不是先全局 top-k 再过滤，后者会返回空）；
- 身份问题路由：`is_self_intro_question()` → 只取帕姆自己的台词，剔除 `未知/？？？` 噪音与重复文本，并让「列车长/我是帕姆/负责/报站」类台词排前；
- 旅行列表问题：`is_travel_list_question()` → 追加「目的地广播」句式检索（解决「去过哪些世界」）；
- 过滤短信里 Player/PlayerAuto 的玩家调侃台词。
- **拥挤治理（2026-09-12，方案 A）**：向量检索会**过取** `top_k × 6` 条候选，再用
  `retrieval.select_diverse()` 挑选：MMR（`MMR_LAMBDA=0.7`，惩罚与已选片段相似的）+ **单场景配额**
  `scene_quota(k) = max(1, k // 2)`——即**同一场景不超过结果的一半**（k=6 → 最多 3 条）。
  实测同场景片段两两相似度中位只有 0.68~0.83，所以拥挤是"同话题不同内容"，用配额而不是近似去重。
  配额撞满时**宁可少给几条**也不放宽。
- **专题通道统一选区**：角色名路由/旅行广播/字面命中属于规则触发的高精度信号，
  现在和向量组**一起排序、同样受配额约束**，只是各自距离减一个 `SPECIALTY_DISTANCE_BONUS=0.03` 的优先偏置。
  （先前的"预留席位"方案有个漏洞：专题片段不受配额约束，`silverwolf999` 会出现 4/6 同场景；
  而完全不预留又会让「到访过哪些世界」的广播片段被挤掉 → 6/7 回归。统一选区同时解决两个问题。）
- **字面通道降级为"召回保险"**：`entity_terms()` 拆出的实体词先看上下文是否已覆盖，未覆盖才检索，
  且命中必须通过 `LITERAL_MIN_SIMILARITY=0.5` 的语义门控（否则「物品「鲁珀特」：鲁珀特」这类
  只含关键词的短条目会占名额）。诊断新增 `literal_terms` / `literal_injected` /
  `context_unique` / `context_max_scene`。
- **别称与推断（2026-09-12 二次调整）**：用户要求「允许少量别称 + 允许合理推断」，于是：
  - 术语规则改为**官方名优先、别称少量**：首次提到用官方名（专属光锥／列车补给凭证／银狼LV.999），
    之后可以偶尔带一两个社区别称（如「狼尊」），一段回答最多一两个；原先出口的**强制替换表已撤掉**；
    smoke 里改成 `NICKNAME_MARKERS` 计数检查（>2 才算失败），通用网络流行语（摆烂/破防/整活/赛博）仍禁用。
  - **允许有依据的推断**：可以顺着片段线索往下推（由「某位小姐出资」＋「艾丝妲是站长」推出资人），
    但必须说清是推测（「帕姆猜」「多半是」），且不得凭空编造剧情事实、头衔或关系。
    实测回答变成「…帕姆猜，多半就是艾丝妲小姐吧？」，官方归属（黑塔设立）仍然明确。
  - 随之调整 `scripts/plot_probes.json` 的 `herta_station_owner`：`avoid` 从「出现艾丝妲」改为
    「错误归属」（艾丝妲建造/艾丝妲建的/艾丝妲所建/艾丝妲修建），避免探针与新策略冲突。

### 6.3 联网攻略（`app/websearch.py`）

- 触发词：配装/配队/队友/队伍/遗器/光锥/攻略/强度/值得抽/星魂/模拟宇宙/混沌回忆/虚构叙事/末日幻影/主词条/副词条/手法/打法…；
- 检索词自动补游戏语境（无「星穹铁道/崩铁」时追加），避免「银狼」被搜成白银价格；
- **多源链（2026-09-11 改）**：`WEB_SEARCH_SOURCES` 默认 `bocha,duckduckgo,so360,sogou`，按序尝试、第一个有结果的源胜出，结果带 `source` 标记（前端思考轨迹显示 `（博查）`/`（DuckDuckGo）`）；单源失败不影响其它源；需要 key 的源（`_SOURCE_API_KEYS`，目前只有 bocha）未配 key 时直接跳过；
  - `search_bocha`：`POST api.bochaai.com/v1/web-search`（`Authorization: Bearer $BOCHA_API_KEY`，body `{query, freshness: "noLimit", summary: true, count}`）→ 取 `data.webPages.value[]`（`name/url/summary|snippet/siteName`）。实测 **0.4–0.5s 返回、命中质量最高**（米游社/17173 的「银狼LV.999 完整攻略」、游侠网的流萤配队正文），`/v1/ai-search` 端点该 key 无权限（403）；节流 0.5s、空结果冷却 15s；
  - `search_duckduckgo`：`html.duckduckgo.com/html/`，需解 `//duckduckgo.com/l/?uddg=` 包装链接；结果最对口（常命中米游社攻略），但**限流很凶**（连发数次就返回 202 空页），节流 12s；
  - `search_so360`：`www.so.com/s`，解析 `h3.res-title > a`，优先取 `data-mdurl`（真实地址，绕过 /link 跳转）+ `res-desc` 摘要；节流 3s；
  - `search_sogou`：原通道保留；`h3.vr-title` + `fz-mid space-txt`，链接补全为可跳转地址；实测会因风控整段时间返回 403/空，节流 4s；
- **保护机制**：每源最小调用间隔（`_MIN_INTERVAL_SECONDS`）、结果缓存（命中 300s / 空结果 45s）、**空结果冷却 90s**（`_COOLDOWN_UNTIL`，避免被限流后每个新 query 继续锤同一个源）；
- 已知实测（2026-09-11）：三个免费源都能返回过相关结果（DDG 命中米游社「银狼遗器主副词条」、360 命中 3DM/九游、搜狗命中词条攻略），但**密集请求后都会被限流**（DDG 202、360 只回 6KB 最小页、搜狗 403）；
- 结果以【星间攻略情报（联网检索）】并入提示词，提示「仅供参考、随版本变化」；
- `fetch_page_text(url)` / `html_to_text(html)`：抓取公开 http(s) 页面并转纯文本，自动跟随搜狗的 JS 跳转页（`window.location.replace(...)` / meta refresh）到真实攻略页；拒绝 `localhost/127.0.0.1` 等本机地址，失败返回空串。

**放弃米游社 API 的原因**（2026-09-11 实测）：`bbs-api.miyoushe.com/post/wapi/searchPosts` 虽然可直接调用（retcode=0），但中文关键词命中的多是社区闲聊帖（如「呜呜伯头套」），攻略帖的 `content`/`meta_content` 为空，取正文的 `getPostFull` 返回非 JSON（需要签名头），因此不适合作为攻略源。

### 6.4 模型与健壮性（`app/chat.py`）

- OpenAI 兼容客户端；`LLM_TIMEOUT_SECONDS=120`；失败自动重试 3 次（间隔 2 秒）后走帕姆口吻兜底；
- 兜底文案：`FALLBACK_REPLY`（智库卡）/`NO_KEY_REPLY`（未配置 Key）；
- `stream_reply(message, history, mode)` 是唯一执行路径：普通模式产出单个 `reply` 事件（逻辑与旧版一致）；`mode="deep"` 时先产出若干 `step` 事件再产出 `reply`；`reply()` 只是收集事件返回字符串，签名向后兼容；
- 诊断：每次回复记录 `question/model/elapsed_ms/fallback/searches/reflect_rounds/avatar_routed/web_titles/retrieved/attempts/last_error`；深度模式追加 `mode/steps/llm_rounds/deep_think_tool_calls/insufficient/no_answer`。

### 6.5 DEBUG 模式（`app/main.py`）

- `DEBUG=true` 时：控制台打印 `[debug]` JSON；开放 `GET /api/debug/info`、`GET /api/debug/logs`（内存最近 100 条，重启清空）；关闭时返回 404；
- **已修复的坑**：debug 打印遇到 `•` 等非 GBK 字符会抛 `UnicodeEncodeError`，导致回答已生成却返回 500（前端显示「列车广播故障」）。现输出前切 UTF-8 且打印异常不影响响应；
- 公网开 DEBUG 时，访客提问内容会进入 debug/logs，注意隐私；目前无访问口令。

### 6.6 记忆与隐私

- 会话内记忆：前端 JS 保存最近 20 轮并随请求发送；后端**不落盘**、请求无状态；
- **刷新保留（2026-09-12 新增）**：对话记录存在**浏览器 localStorage**（key `pom-pom-chat-v1`，最多 40 条消息 = 20 轮），刷新/重开页面自动恢复并重建发给后端的 history；隐私模式或存储不可用时静默降级为「刷新即清空」，不影响聊天。这是对 DESIGN.md「不做跨会话长期记忆」的**有限放宽**——数据只在本机浏览器，服务端仍然无状态、不存对话；
- **清空按钮**：页面右上角「清空对话」，二次确认后清掉 localStorage、DOM 与 history，并重新播报欢迎语；
- `data/pom.db` 只存知识库片段，不存对话。

### 6.7 深度思考模式（`app/deepthink.py`）

- **触发**：`POST /api/chat` 带 `{"mode": "deep"}`（其它值 422），或前端「深度思考」开关走 `POST /api/chat/stream`（SSE：`step` → `reply` → `done`）；
- **循环**：原生 function calling（`tool_choice="auto"`，真实模型不支持 `required`）→ 执行工具 → 回填 `role=tool` 观测 → 下一轮；模型不再请求工具即为最终回答；
- **工具**：`search_knowledge_base`（复用 `ChatEngine._retrieve` 的智能检索，含角色路由/迭代召回）、`search_web`（复用搜狗）、`fetch_page`（网页正文；`WEB_SEARCH_ENABLED=false` 时后两者不暴露）；
- **收敛保障**：`DEEP_THINK_MAX_STEPS`（默认 6 轮）、`DEEP_THINK_TIMEOUT_SECONDS`（默认 180 秒，单轮超时取 min(60s, 剩余)）、相同 `(工具,参数)` 去重、连续两轮无新证据即停、首轮失败直接兜底；
- **收尾**：不再带 `tools` 调一次；若「用过工具但一条证据都没有」，追加系统指令要求如实说明、不许编造；收尾调用失败时无证据走 `NO_ANSWER_REPLY`（帕姆口吻「智库翻遍了也没有」），有证据走 `FALLBACK_REPLY`；
- **无答案协议**：措辞只能指向帕姆自己的资料，禁止「游戏里根本没有」这类替游戏下结论的说法；`no_answer` 标记由回答文本判定（用于诊断与烟测）；
- **文本通道工具调用（DSML）**：模型偶发把工具调用写在文本里而不是 `tool_calls` 结构里。实测 DeepSeek 用 `<\uff5c\uff5cDSML\uff5c\uff5c invoke …>`（全角竖线 U+FF5C）这种方言，Qwen 系用 ASCII 的 `<tool_call>`。两种都会被 `has_tool_markup()` 识别：循环内视为「这不是回答」并纠正重来，收尾轮最多重试 2 次，仍泄漏则走确定性兜底；出口再加一道 `strip_tool_markup()` 清洗（普通模式同样适用），用户永远不会看到这类标记。诊断字段 `text_tool_calls` 记录泄漏次数；
- **提示词**：`persona.build_deep_think_prompt(samples, max_steps)` = `PERSONA_CORE` + 台词风格参考 + 【深度思考工作方式】，回答里不得出现「工具/函数/模型」等字眼。
- **语气分层（2026-09-11 修）**：深度模式一度「AI 味过重」——输出加粗小标题 + 项目符号 + 「仅供参考」免责声明腔。现结构为 `PERSONA_CORE` + 台词风格参考 + 【内部检索流程（开拓者看不到）】 + **【说话方式（最重要，回答前再确认一遍）】**（放在提示词最后，离生成最近）；`POM_POM_VOICE` 明确禁用 Markdown/加粗/项目符号/栏目标题、禁用免责声明腔，限定 3~6 句 / 150~250 字；
- **发声前提醒**：每轮工具观测之后（以及收尾调用前）都会在对话尾部保留一条 `_VOICE_REMINDER` 系统消息（全局唯一、始终在最后），把「现在这句是直接说给开拓者乘客听的」压到最近位置；
- **出口清洗**：`clean_reply()` = `strip_tool_markup()` + `strip_markdown_fluff()`（去掉 `**`、`~~`、反引号、`#` 标题、行首 `- / • / 1.`），深度模式与普通模式都过这道；单测与 smoke 都加了「没有报告腔格式」断言（smoke 的 `REPORT_MARKERS`）。

---

## 7. 测试与验收

```powershell
# 全量测试（每次运行都会自动存 test-results/）
.venv\Scripts\python.exe -m pytest -q

# 带完整日志
powershell -ExecutionPolicy Bypass -File scripts/run_tests.ps1

# 18 题验收（--quick 只跑前 3 题）
.venv\Scripts\python.exe scripts/run_acceptance.py

# 剧情事实探针（7 条：身份/姬子职务/关系/列车组/黑塔空间站/虚妄之母/到访世界）
.venv\Scripts\python.exe scripts/run_plot_probes.py

# 实体探针（20 条：只看检索结果有没有捞到该捞的实体，不调 LLM，秒级跑完）
.venv\Scripts\python.exe scripts/run_entity_probes.py

# 深度思考真实链路 smoke（4 题：剧情/占位名/不存在的星球/攻略；需先起服务）
.venv\Scripts\python.exe scripts/run_deep_smoke.py
```

结果位置：
- `test-results/latest-pytest.json|txt`、`pytest-<时间戳>.*`
- `test-results/latest-acceptance.jsonl|md`、`acceptance-<时间戳>.*`
- `test-results/latest-plot-probes.jsonl|md`、`plot-probes-<时间戳>.*`
- `test-results/latest-entity-probes.json`、`entity-probes-<时间戳>.json`（实体命中率/不同文本数/同场景最多）
- `test-results/latest-deep-smoke.jsonl|md`、`deep-smoke-<时间戳>.*`

---

## 8. 已知问题 / 下一步候选

1. **剧情覆盖**：Mission（主线/支线演出）+ 模拟宇宙 + **智库图鉴（NounAtlas，100 条星神/派系/地名词条）** 都已入库；
   2026-09-13 实测「丹轮寺是什么」「「欢愉」的阿哈是什么样的星神」「无名客是什么」均可用词条作答。
   **剩余缺口主要是限时活动**（`ExcelOutput/Activity*` 161 个 + `Config/Level/EventMission`/`GameplayMission` 等）
   与未引用的 NPC/道具文本（TextMap 46.6 万条里仍有大量未被任何提取器引用）。
1b. **检索**：已修（2026-09-12）——实体字面通道 + 拥挤治理（过取/MMR/单场景配额/专题预留席位），
见第 6.2 节。仍待办：实体词靠**规则切分**，遇到「白厄在翁法罗斯经历了什么」会切出废词整串（通道不触发、
但向量通道仍工作），且别称写不通（「狼尊」40 条 vs「银狼LV.999」61 条）——要彻底解决得上 FTS5 trigram +
BM25 + RRF，以及一张别名词表（原计划里的方案 B/第二步）。
2. **联网检索**：已接入**博查**（带 key，0.4–0.5s、质量最好）作为首选源，免 key 的 DuckDuckGo / 360 / 搜狗退为兜底（它们仍会在密集请求后被限流：DDG 202、360 最小页、搜狗 403）。未配 `BOCHA_API_KEY` 时自动只用免 key 源；额度/计费需自己盯（每次搜索 1 次调用）。
3. **DEBUG 公开暴露**：公网 + DEBUG 时任何人可看 `/api/debug/logs`（含提问内容），建议加调试口令或公网自动关闭 DEBUG。
4. **残留进程**：当前有一个 cloudflared 进程（PID 10064）指向已停止的服务，公网链接失效；重新分享前先清掉或重启。
5. **分支未整合**：工作都在 `feat/pom-pom-agent`，未合并 `main`、无远程仓库（集成方案：本地合并 / 推 PR / 保持现状）。
6. **密钥管理**：`.env` 内含真实 DeepSeek Key（已 gitignore）；换机器需重新配置。
6b. **博查 Key**：`BOCHA_API_KEY` 存在 `.env`（已 gitignore）；若泄露需到博查后台轮换。
7. **回答里的 Markdown 原样显示**：模型偶尔输出 `**加粗**`，前端用 `textContent` 渲染，用户会看到星号（普通模式也存在）。
8. **深度模式更慢更贵**：一次提问 1–3 分钟、数倍 token；默认 6 轮 / 180 秒，可按需调 `DEEP_THINK_*` 环境变量。
9. **`insufficient` 语义偏保守**：它只表示「一条片段都没查到」（用于触发无答案协议）；「查到但都无关」由 `no_answer` 与回答文本体现。

---

## 9. 环境与踩坑记录

| 现象 | 原因 / 处理 |
|---|---|
| `python` 命令不可用 | 本机是用 Anaconda 里的解释器（`%USERPROFILE%\anaconda3\envs\cv\python.exe`）创建 `.venv` 的，统一用 `.venv\Scripts\python.exe` |
| pytest 报 tmp/缓存权限错误 | 已在 `pytest.ini` 设 `--basetemp=.pytest_tmp -p no:cacheprovider` |
| git 报 dubious ownership | 已 `git config --global --add safe.directory D:/Pom-Pom` |
| 中文输出乱码/报错 | 运行时加 `PYTHONIOENCODING=utf-8`；打印已做 UTF-8 兼容 |
| github.com 直连不稳定 | 原始数据用 sparse clone + `raw.githubusercontent.com` 兜底；cloudflared 下载用 `curl -C -` 断点续传 |
| fastembed 模型下载慢 | 国内镜像：`$env:HF_ENDPOINT='https://hf-mirror.com'`，模型缓存在 `data/models` |
| sqlite 跨线程报错 | 已用 `check_same_thread=False` + 线程锁解决 |
| 回答里冒出工具调用标记 | 模型（DeepSeek）把工具调用写进文本通道：`<\uff5c\uff5cDSML\uff5c\uff5c invoke name=…>`（全角竖线），旧版直接当回答返回给用户。现已识别 + 纠正 + 出口清洗，并有单测与 smoke 用例（`lv999`）守住 |
| PowerShell 脚本中文解析报错 | Windows PowerShell 5.1 把无 BOM 的 .ps1 按 GBK 读，中文字符串会 `Unexpected token`；仓库内 .ps1 统一只写 ASCII |
| 搜狗搜索 403 / 免费搜索源限流 | 密集请求后 DDG 返 202 空页、360 返 6KB 最小页、搜狗返 403；已加每源节流、结果缓存、空结果 90s 冷却，并由多源链自动切换 |
| 米游社搜索 API 不可用 | `searchPosts` 命中社区闲聊帖、帖正文为空，`getPostFull` 需签名头返回非 JSON；不接入 |

---

## 10. 新会话快速上手（建议顺序）

1. 读 `docs/PROJECT-HANDOFF.md`（本文）→ `DESIGN.md` → `docs/pom-pom-persona.md`；
2. 跑一次 `.venv\Scripts\python.exe -m pytest -q`，确认 52 passed；
3. 看 `app/chat.py` 的 `reply/_retrieve` 与 `app/retrieval.py`，理解检索链路；
4. 若要改行为，先写测试（项目全程 TDD），再改 `persona.py` / `retrieval.py` / `chat.py`；
5. 用 `scripts/run_plot_probes.py` 或 `scripts/run_acceptance.py` 验证效果，结果自动存档；
6. 提交后按需重启服务（普通/DEBUG）与公网隧道。
