# Pom-Pom Agent（帕姆 Agent）

以《崩坏：星穹铁道》列车长**帕姆（Pom-Pom）**为原型的本地 AI 对话 Agent：
本地游戏语料向量库（RAG）＋ OpenAI 兼容大模型 ＋ Web 聊天界面。
既能用帕姆的口吻聊剧情，也能切到「深度思考」模式反复查库、搜网、读网页，攒够依据再作答。

> 新会话、新同事接手请看 [docs/PROJECT-HANDOFF.md](docs/PROJECT-HANDOFF.md)：
> 项目现状、架构取舍、运行手册、已知问题与下一步都在那里。
>
> 要公开部署/发布，先读 [SECURITY.md](SECURITY.md)：密钥与隐私信息的处理方式、提交前自检命令、
> 以及一次完整的审计结果。

## 现状一览

| 项目 | 现状 |
|---|---|
| 知识库 | 271,061 条记录 → **109,446 个向量片段**（SQLite + sqlite-vec，248 MB） |
| 对话覆盖率 | 游戏内 `TalkSentenceConfig` 共 240,488 条对话，本地能引用到 **125,518 条（52.2%）** |
| 自动化测试 | **168 passed**（`pytest`，结果自动存档到 `test-results/`） |
| 统一评测集 | `eval/testset.json` 65 题 / 10 个轴：快档 **28/30**、普通档 **28/29**、深度档 **6/6**（另有 3 条已知缺口记录在案） |
| 检索 | 向量检索 ＋ 实体字面检索 ＋ 角色路由 ＋ 多样性重排（MMR / 单场景配额） |
| 联网 | 多源链：博查（带 key 优先）→ DuckDuckGo → 360 → 搜狗，带节流／失败冷却／缓存 |
| 模型 | 任意 OpenAI 兼容接口，当前默认 DeepSeek `deepseek-v4-flash` |

## 功能

- **完整帕姆人设**：列车长、傲娇可爱，自称「帕姆」，称用户「开拓者乘客」，风格锚定官方剧情台词
- **剧情问答**：本地 `bge-small-zh-v1.5` 向量检索 SQLite（sqlite-vec）中的 13 类游戏语料
- **实体接地**：问题里的人物／物品名走字面检索兜底，避免「差分宇宙」这类同题材片段把「鲁珀特」挤掉
- **角色路由**：问到具体角色时按说话人定向检索该角色的台词
- **跨篇章列表类问题**（如「列车去过哪些世界」）自动抓取列车目的地广播语料合并后作答
- **养成／配装／配队**类问题自动联网检索攻略，以「星间攻略情报」并入回答
- **深度思考模式**（输入框旁的开关）：ReAct 方式反复查智库与星间情报，必要时打开攻略网页读正文，
  前端实时显示「帕姆的思考过程」；查不到时如实说明，绝不编造
- **无答案协议**：不说「游戏里根本没有」这类替游戏下结论的话；也会如实说明「智库里还没有这段记录」
- **超纲问题**帕姆口吻带开；**违规内容**列车长口吻拒绝
- 模型瞬时调用失败自动重试 3 次，减少限流导致的兜底
- 对话保留最近 20 轮，刷新页面自动恢复（localStorage）；右上角「清空对话」二次确认后清空，不影响列车智库

知识库结构与 [HSR-Database-Web](https://github.com/Mar7thLover/HSR-Database-Web) 的
avatar/dialogue/mission/item/monster 模块保持一致，数据源同为 `DimbreathBot/TurnBasedGameData`。

## 知识库

### 语料类别

「记录」是提取出的条目数，「片段」是入库后的向量块数：文本完全相同的记录会在入库时去重，
长文本则会按段落与句末标点切成多块，所以两列不一定相等。

| 类别 | 提示词标签 | 内容 | 记录 | 片段 |
|---|---|---|---:|---:|
| `story` | 主线／支线剧情 | 任务演出对白（`Story/Mission`、`Story/Discussion`、`Config/Level/Mission/**/Act*.json`） | 182,544 | 58,598 |
| `messages` | 短信 | 游戏内短信（`MessageContactsConfig`/`MessageItemConfig` 等） | 13,255 | 7,222 |
| `rogue` | 模拟宇宙 | 模拟宇宙对白（`Config/Level/Rogue**`）＋ 祝福／命途配置（`ExcelOutput/Rogue*.json`，逐条提取） | 23,409 | 9,610 |
| `books` | 书籍 | 书籍文本（`BookSeriesConfig` ＋ `LocalbookConfig` 两套书库） | 12,100 | 12,097 |
| `avatars` | 角色信息 | 角色档案、技能、语音线、角色故事（`AvatarConfig`/`AvatarSkillConfig`/`VoiceAtlas`/`StoryAtlas`） | 11,923 | 2,857 |
| `missions` | 任务 | 主线／支线任务条目（`MainMission`/`SubMission`） | 10,218 | 7,017 |
| `items` | 物品 | 物品与用途（`ItemConfig*`/`ItemPurpose`） | 5,017 | 4,134 |
| `story_summary` | 剧情概要 | 每段演出的概要（`PerformanceSkipOverride`） | 3,688 | 3,682 |
| `train_visitor` | 列车访客对话 | 列车访客对白（`Config/Level/Mission/TrainVisitor`） | 3,504 | 1,076 |
| `monsters` | 怪物 | 怪物档案＋图鉴额外阶段描述（`MonsterConfig`/`MonsterTemplateConfig`/`MonsterAtlasExtraPhase(s)`） | 2,657 | 1,185 |
| `places` | 地点 | 地点／造物与加载界面世界观描述（`MappingInfo`/`LoadingDesc`） | 1,894 | 1,142 |
| `chronicle` | 编年史 | 任务结语（`ChronicleConclusion`） | 434 | 440 |
| `atlas` | 智库 | 游戏内智库词条（`NounAtlas`：名词／星神／派系） | 418 | 386 |

**有意不入库**：战斗数值（`StatusConfig`/`MazeBuff`/各种 `*Skill`，含 `#1[i]%` 占位符，会污染检索）、
教学与成就文本、塔罗书玩法剧情对白。

### 文本切分

按**说话人**切块（换人就切），长文本再按句末标点（`。！？；…`）切。
早期「同场景 4 行／220 字」合并会让一个片段里混进多个说话人、而 `speaker` 只记首行，
角色路由漏检率高达 38%~59%；改后降到 0~9%。

### 数据来源

`DimbreathBot/TurnBasedGameData`（中文 CHS），由 `scripts/fetch_data.ps1` 以 sparse checkout 拉最小集，
缺失文件回落到 raw 单文件下载（本机走 git 通道会被 reset，所以脚本里做了兜底）。

## 检索与回答流程

### 普通模式（`POST /api/chat`）

1. **身份路由**：「帕姆是谁」这类问题直接走帕姆自己的台词，不掺剧情场景；
2. **向量检索**：先按 `TOP_K × 6` 过取候选；上下文不足时本地改写追问（最多 2 次），仍不足则由模型反思生成检索词（最多 2 次），总检索次数上限 5；
3. **专题通道**：命中角色名走说话人定向检索；列表类问题抓列车广播；问题里的实体词走字面检索（实体词表 8,873 条最长匹配 ＋ 别称展开，如 阿哈／乐子神／「欢愉」）；
4. **语义门控**：字面命中还必须与问题语义相关（相似度 ≥0.5，走别称时 ≥0.45）才允许注入，避免同名的「物品「鲁珀特」」占名额；
5. **合并重排**：MMR（λ=0.7）＋「单场景不超过结果一半」的配额；字面精确命中额外保底席位（每个实体 1 条），防止被随机候选挤掉；
6. **邻接扩展**：给最相关的几条补上同场景相邻片段（上限 3 条），缓解台词被切块后「那个家伙」这类指代断裂。

短信里玩家可选的吐槽台词（`Player`/`PlayerAuto`）不算正典，检索阶段直接剔除。

### 深度思考模式（`POST /api/chat/stream`，SSE）

输入框右侧的「深度思考」开关打开后，本次提问走 ReAct 循环：

1. 模型自己决定下一步查什么，可反复调用三个工具：`search_knowledge_base`（列车智库，剧情依据）、
   `search_web`（星间攻略情报）、`fetch_page`（打开攻略网页读正文，会自动跟随搜狗跳转链接）；
2. 相同查询不会重复执行；连续两轮查不到新证据、用满 `DEEP_THINK_MAX_STEPS` 轮或超过总时限时收尾，
   不给模型无限查下去的机会；
3. 前端通过 SSE 实时显示「帕姆的思考过程」（🔍 查智库 / 🌐 联星间情报 / 📖 翻正文），回答到达后自动收起；
   普通模式仍走 `POST /api/chat`，行为与以前完全一致。

查不到时会如实说「智库里没有这段记录」，并用不确定语气（「帕姆猜」「多半是」）区分推断与事实，
不会拿游戏外常识补剧情，也不会替游戏下结论。

> 深度思考比普通模式慢 3~10 倍、消耗更多 token（一次提问通常 1~3 分钟）。
> 想验证真实链路可运行 `scripts/run_deep_smoke.py`（需先起服务），结果写入 `test-results/`。

### 人设与语气

- 自称「帕姆」或「列车长」；「帕」是句尾语气词（如「放心了帕！」），每两三句点一个即可；
- 说话正式礼貌，不用「摆烂」「破防」「整活」「赛博」这类网络流行语当口癖；
- 游戏事物**首次提到用官方名**，之后可以偶尔带一两个玩家社区的别称或简写（如「狼尊」「专武」），
  一段回答里合计最多两处，同一个别称不要反复出现；
- 两种模式的回答都按帕姆口吻短句输出，不出现 Markdown 加粗／项目符号／栏目标题，
  也不说「仅供参考」这类免责声明（服务端 `clean_reply()` 会清掉这些报告腔标记）。

## 快速开始

1. 创建环境并安装依赖：

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. 下载剧情最小数据集（需要网络，约 80–100MB）：

```bash
powershell -ExecutionPolicy Bypass -File scripts/fetch_data.ps1
```

3. 配置 API Key：复制 `.env.example` 为 `.env`，填入模型服务商 Key：

```dotenv
LLM_API_KEY=你的key
```

4. 提取并导入数据（首次会下载约 100MB 的本地 embedding 模型到 `data/models`，
   可设 `HF_ENDPOINT=https://hf-mirror.com` 走国内镜像；全量入库约 30–40 分钟）：

```bash
.venv\Scripts\python.exe -c "from app.extract import extract_all; extract_all('data/StarRailData', 'data/dialogues')"
.venv\Scripts\python.exe -c "from app.import_data import import_from_dialogues; from app.embedder import Embedder; import_from_dialogues('data/dialogues','data/pom.db',Embedder('data/models'))"
```

5. 启动：

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

更省事的一行启动脚本（自动清掉旧实例、自动做健康检查、可选后台／DEBUG／公网）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1              # 前台，Ctrl+C 停止
powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Debug       # 调试模式
powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Background  # 后台（日志落 test-results/）
powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Public      # 本机服务 + 公网隧道（打印公网地址）
powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Stop        # 停止
powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -BindHost 0.0.0.0 -Port 8080
```

浏览器打开 <http://127.0.0.1:8000/>。

`-Public` 会同时拉起 Cloudflare Quick Tunnel 并打印公网地址（形如 `https://xxx.trycloudflare.com`），
`-Stop` 会把服务和隧道一起停掉。注意两点：① 这是无账号版隧道，**地址每次重启都会变**；
② 有时本机 DNS 还没解析新域名，脚本会提示 "local DNS lagging"——外网访客仍可正常打开
（可用 `curl.exe --resolve` 指定 IP 自行验证）。**DEBUG 模式不要开公网**：`/api/debug/logs`
会回显提问内容。

> 注意：部分免费档模型（如智谱 GLM-4.7-Flash）响应延迟明显，单次对话可能需要数十秒，
> 页面会显示「帕姆正在翻列车智库……」，请耐心等待；超时（120 秒）会走帕姆口吻兜底。

**公网部署请务必带上访问口令**，否则拿到链接的任何人都能消耗你的模型额度：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Public -Token 你自己设的口令
```

启动后把 `https://xxx.trycloudflare.com/#token=你的口令` 发给访客——前端会记住口令，
之后直接访问也会自动带上。终端里不带 `#token` 的链接打不开聊天（会提示需要通行证）。
每来源 IP 默认限速 20 次/分钟，`POM_RATE_LIMIT_PER_MINUTE=0` 可关闭。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_API_KEY` | （空） | LLM API Key（DeepSeek／智谱等） |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容接口地址 |
| `LLM_MODEL` | `deepseek-v4-flash` | 模型名（可换智谱 `glm-4.7-flash` 等） |
| `DB_PATH` | `data/pom.db` | 向量库路径 |
| `DATA_DIR` | `data/StarRailData` | 原始数据目录 |
| `DIALOGUES_DIR` | `data/dialogues` | 提取结果目录 |
| `MODEL_CACHE_DIR` | `data/models` | embedding 模型缓存 |
| `TOP_K` | `6` | 检索返回片段数 |
| `HISTORY_TURNS` | `20` | 对话历史轮数 |
| `WEB_SEARCH_ENABLED` | `true` | 养成攻略类问题是否联网搜索 |
| `BOCHA_API_KEY` | （空） | 博查搜索 API Key；配置后搜索链优先用它（`https://open.bochaai.com`） |
| `WEB_SEARCH_SOURCES` | `bocha,duckduckgo,so360,sogou` | 搜索源顺序（按序尝试，第一个有结果的就用） |
| `DEBUG` | `false` | 调试模式：控制台打印每次请求诊断，并开放 `/api/debug/*` |
| `POM_ACCESS_TOKEN` | （空） | 设置后聊天接口必须带令牌（`Authorization: Bearer …` 或 `X-Access-Token`）；留空 = 不校验，本地使用不变 |
| `POM_RATE_LIMIT_PER_MINUTE` | `20` | 每个来源 IP 每分钟的请求上限，`0` = 不限制 |
| `POM_MAX_HISTORY_MESSAGES` | `40` | 一次请求最多接受多少条历史消息 |
| `POM_MAX_HISTORY_CHARS` | `20000` | 历史消息总字数上限 |
| `POM_MAX_MESSAGE_CHARS` | `2000` | 单条提问字数上限 |
| `DEEP_THINK_MAX_STEPS` | `6` | 深度思考最多 LLM 轮数 |
| `DEEP_THINK_TIMEOUT_SECONDS` | `180` | 深度思考总时限（秒） |
| `DEEP_THINK_LLM_TIMEOUT_SECONDS` | `60` | 深度思考单轮 LLM 超时（秒） |
| `DEEP_THINK_MAX_TOOL_CHARS` | `1200` | 单次工具结果回填模型的最大字符数 |
| `DEEP_THINK_MAX_FETCHES` | `2` | 每次深度思考最多打开的网页数 |
| `DEEP_THINK_FETCH_ENABLED` | `true` | 是否允许深度思考打开网页读正文 |

### 调试模式（DEBUG=true）

设置 `.env` 的 `DEBUG=true` 后重启服务（只绑本机，避免诊断日志外泄）：

```powershell
$env:DEBUG="true"; .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 控制台每次对话打印一行 JSON：问题、模型、耗时、是否兜底、检索次数、
  命中片段（类别／说话人／摘要）、反思轮数、角色路由、联网攻略标题等；
- `GET /api/debug/info` —— 运行时配置与知识库状态；
- `GET /api/debug/logs` —— 最近 100 次请求的诊断记录（仅调试模式返回，否则 404）。

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/chat` | 普通问答，返回 `reply` / `mode` / `steps` |
| `POST` | `/api/chat/stream` | SSE 流式：`step`（思考步骤）→ `reply` → `done` |
| `GET` | `/api/health` | 健康检查：`status`（`ok` / `degraded`）、片段数、embedder 是否已加载、模型是否已配置 |
| `GET` | `/api/debug/info` | 运行时配置与知识库状态（仅 DEBUG） |
| `GET` | `/api/debug/logs` | 最近 100 次请求诊断（仅 DEBUG） |

## 测试与验收

```bash
.venv\Scripts\python.exe -m pytest -v
```

每次运行 pytest 都会把摘要自动保存到 `test-results/`：`pytest-<时间戳>.json/.txt` 与 `latest-pytest.json/.txt`。
带完整控制台日志的运行方式：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_tests.ps1
```

### 统一评测集（`eval/testset.json`）

行为级评测集中在**一个文件**里，按「轴 + 档位」组织，用 `scripts/run_eval.py` 驱动。
结构参考了几个公开评测集的做法：

| 参考 | 借鉴点 |
|---|---|
| [RGB](https://github.com/chen700564/RGB)（AAAI 2024） | 噪声鲁棒性 / 否定拒绝 / 信息整合 / 反事实鲁棒性四种基础能力，中英分开 |
| [CRAG](https://github.com/facebookresearch/CRAG)（Meta） | 问题类型分类（多跳、聚合、假前提…）与按热度、时效分层 |
| [CharacterEval](https://github.com/morecry/CharacterEval) | 角色扮演评测的多维指标：人设一致性、语言风格 |
| [RAGChecker](https://github.com/amazon-science/RAGChecker)（Amazon） | 指标分成「检索端」与「生成端」两组，便于定位问题 |
| [GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA) | 按所需工具与自主程度给任务分级 |
| [Ragas](https://docs.ragas.io/en/stable/concepts/test_data_generation/) | 测试集要素与「覆盖真实场景、持续更新」的原则 |

当前 65 条用例覆盖 10 个轴（`grounding` / `noise` / `rejection` / `integration` /
`counterfactual` / `persona` / `style` / `safety` / `tooling` / `multilingual`），分三档：

| 档位 | 跑什么 | 用例 | 是否需要 Key | 最近结果 |
|---|---|---:|---|---|
| `fast` | 只跑检索，不调模型（秒级/题） | 30 | 否 | **28/30**（+2 条已知缺口） |
| `llm` | 普通问答全链路 | 29 | 是 | **28/29**（+1 条已知缺口） |
| `deep` | 深度思考（SSE，需服务在跑） | 6 | 是 | **6/6** |

断言全部是确定性的字符串/计数检查，不引入 LLM 判官，保证可重复：
上下文命中、回答要点覆盖、无答案协议、拒绝话术、联网工具是否调用、工具轮数、
语言跟随、人设与风格（无网络流行语、别称限额、无报告腔、无工具标记泄漏）。

```bash
.venv\Scripts\python.exe scripts/run_eval.py --tier fast          # 秒级回归，改检索必跑
.venv\Scripts\python.exe scripts/run_eval.py --tier llm           # 普通模式全链路
.venv\Scripts\python.exe scripts/run_eval.py --tier deep          # 深度思考（先起服务）
.venv\Scripts\python.exe scripts/run_eval.py --tier llm --axis rejection
.venv\Scripts\python.exe scripts/run_eval.py --tier llm --id cf_ --limit 2
.venv\Scripts\python.exe scripts/run_eval.py --from-jsonl test-results/eval-llm-<时间戳>.jsonl
```

结果写入 `test-results/eval-<档位>-<时间戳>.jsonl|.md`。要直接看结果就打开
[`test-results/latest-eval.md`](test-results/latest-eval.md)——它是**目录**，一行一个档位，
指向该档位最近一次完整运行的报告；每个档位还有自己的稳定副本 `latest-eval-<档位>.md|jsonl`。
「帕姆实际怎么回答的」在 `llm` 与 `deep` 两份里（`fast` 档只跑检索，没有回答）。
`--from-jsonl` 能用既有结果重新渲染报告，不必重跑（也不会再花 token）。

报告是本轮运行的完整记录，分四段：**分轴结果**（每轴几过几）、**已知缺口**、**未通过用例**、
**用例明细**。后三段都逐题写出问题原文和实际输出——普通/深度档给出完整回答和工具轨迹，
检索档（fast）给出命中的片段摘要与条数——所以只看 `.md` 就能判断"这题答得对不对、为什么判失败"。
已知缺口用 `known_gap` 字段记录在案：报告里单列、不计入失败，但会一直显示。
`tests/test_eval_set.py` 会校验用例结构，并保证统一集始终是旧探针脚本的超集——
合并过程不会悄悄丢覆盖。

### 旧探针脚本（兼容保留）

下面四个脚本仍然可用，用例与统一集一致（漂移会被上面那条测试拦下），新工作建议直接用 `run_eval.py`：

| 脚本 | 覆盖内容 | 最近结果 |
|---|---|---|
| `scripts/run_entity_probes.py` | 25 条实体／事实探针：检索片段里到底有没有该实体（秒级、不调 LLM） | 25/25 |
| `scripts/run_plot_probes.py` | 7 条剧情事实断言（如「姬子是领航员且不能出现舰长／摇滚明星」） | 7/7 |
| `scripts/run_deep_smoke.py` | 5 条深度思考真实链路 smoke（剧情／未知／不存在／攻略／LV.999） | 5/5 |
| `scripts/run_acceptance.py` | 18 题验收集（`--quick` 只跑前 3 题） | 见 `test-results/latest-acceptance.*` |

```bash
.venv\Scripts\python.exe scripts/run_entity_probes.py
.venv\Scripts\python.exe scripts/run_plot_probes.py
.venv\Scripts\python.exe scripts/run_deep_smoke.py
.venv\Scripts\python.exe scripts/run_acceptance.py          # 完整 18 题
.venv\Scripts\python.exe scripts/run_acceptance.py --quick  # 只跑前 3 题
```

验收测试集（见 [DESIGN.md](DESIGN.md) 第 5 节，18 题五类）需要在配置 API Key 后逐题手动验证并记录表现。

## 数据更新

游戏版本更新后，重新抓取最新数据并重跑导入（脚本幂等）：

```bash
Remove-Item -Recurse -Force data/StarRailData
powershell -ExecutionPolicy Bypass -File scripts/fetch_data.ps1
# 提取 + 重新入库（导入约 30-40 分钟，中途中断不会破坏旧库）
.venv\Scripts\python.exe scripts/rebuild_knowledge_base.py
```

只想重跑其中一步时：`--skip-extract` 跳过提取（语料没变、只改切块或入库逻辑时用）。
重建期间建议先 `scripts/start_server.ps1 -Stop`，避免服务正读着旧库。

补充 sparse checkout 之外的文件（Mission／Rogue／全量 ExcelOutput）：

```bash
.venv\Scripts\python.exe scripts/fetch_extra_files.py --group excel
```

## 目录结构

```
app/
  config.py       配置加载（Settings、模型、Key、DEBUG）
  embedder.py     本地 embedding（fastembed + bge-small-zh-v1.5，512 维）
  database.py     SQLite + sqlite-vec（search / search_speaker / 邻接片段 / 实体词表）
  extract.py      从 StarRailData 提取 13 类语料到 JSONL
  import_data.py  切块＋向量化＋幂等写入
  retrieval.py    迭代检索、实体抽取、别称展开、文内广播
  persona.py      帕姆人设提示词与上下文拼装
  websearch.py    联网搜索链（博查／DuckDuckGo／360／搜狗）
  deepthink.py    深度思考 ReAct 循环＋无答案协议＋出口清洗
  chat.py         RAG 对话入口（检索→人设提示词→LLM→清理；stream_reply 事件流）
  main.py         FastAPI（/api/chat、/api/chat/stream、/api/health、/api/debug/*）
static/           index.html / style.css / app.js（前端）
scripts/          取数、起服务、跑测试与各类探针
docs/             交接文档、人设文档、设计与计划
tests/            pytest（168 条）
data/             原始数据、提取结果与向量库（均不入库）
```

## 许可

代码以 [MIT](LICENSE) 许可发布，© 2026 SnowGoose00。

几点说明：《崩坏：星穹铁道》及其游戏文本、角色、素材的版权归米哈游所有，本仓库是爱好者项目，
与米哈游无任何关联；仓库不附带游戏数据（`data/` 不入库，由 `scripts/fetch_data.ps1` 从第三方数据仓库
`DimbreathBot/TurnBasedGameData` 拉取）；`tests/fixtures` 里仅有少量游戏文本片段，用于测试。
