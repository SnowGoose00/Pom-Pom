# 安全与隐私

这个仓库要公开发布，所以有两件事必须一直成立：**真实密钥不进版本库**、**本机隐私信息不进版本库**。

## 不会上传的东西

| 路径 | 内容 | 状态 |
|---|---|---|
| `.env` / `.env.*` | 真实 API Key | 已 gitignore；只有 `.env.example` 会进仓库，且其中 KEY/TOKEN/SECRET 全为空值 |
| `data/` | 原始游戏数据、提取结果、向量库（231 MB） | 已 gitignore |
| `tools/` | cloudflared 等第三方二进制 | 已 gitignore |
| `test-results/` | 运行日志、评测报告（含提问内容与诊断信息） | 已 gitignore |
| `*.exe` / `*.zip` / `*.whl` 等 | 二进制与压缩包 | 已 gitignore（历史上误入过一次，见下） |

## 提交前自检

```powershell
.venv\Scripts\python.exe scripts/check_secrets.py            # 只查将要提交的文件（秒级）
.venv\Scripts\python.exe scripts/check_secrets.py --history  # 连全部历史一起查（较慢）
```

命中即退出码 1，且只打印打码预览。检查范围：`sk-…` 形式的 API Key、Bearer token、
`KEY=真实值` 形式的赋值、带引号的密钥字面量、邮箱、手机号、Windows 用户目录下的本机路径、内网 IP。

`tests/test_no_secrets.py` 把工作区版本的检查接进了 pytest —— 密钥粘进代码会在提交前就失败。

## 2026-09-17 审计结果

对**工作区 82 个文件**和**全部历史提交的每一个 blob**都做了扫描：

- **没有发现任何 API Key**：`.env` 从未被提交，`sk-…` 模式零命中；`.env.example` 里所有敏感变量都是空值。
  文档里出现的 `https://xxx.trycloudflare.com` 均为占位示例，不是真实隧道地址。
- **没有邮箱、手机号、内网 IP、主机名**。
- ⚠️ `docs/PROJECT-HANDOFF.md` 曾包含一处本机路径（Windows 用户目录，带本机账号名），
  当前版本已替换为 `%USERPROFILE%`；但**历史提交里仍留有该字符串**（27 个历史版本）。
- ⚠️ 历史里有一个 **53.5 MB 的 `tools/cloudflared.exe`**（提交 `e7b9a09` 加入、`90201dd` 移除）。
  文件已不在工作区，但 blob 仍留在历史中，会让仓库体积多出约 18 MB。

后两条只存在于**历史**中——删掉当前文件并不会让它们消失。发布前按下面二选一处理。

## 发布方式（二选一）

**A. 只发布当前快照（推荐，非破坏性）**

新建一个孤儿分支放当前干净的代码树，原来的完整历史原样留在本地：

```powershell
git checkout --orphan public
git commit -m "Initial public release"
git push -u origin public:main
git checkout feat/pom-pom-agent   # 回到原来的开发分支
```

GitHub 上只显示一个初始提交，历史里那个二进制和用户名都不会被推送。

**B. 重写历史后发布完整提交记录**

保留 64 个提交的开发过程，同时把二进制与本机路径从历史中抹掉：

```powershell
git bundle create ..\pom-pom-backup.bundle --all          # 先备份，可完整还原
git filter-branch --force --tree-filter "git rm -f --ignore-unmatch tools/cloudflared.exe" -- --all
git for-each-ref --format='delete %(refname)' refs/original | git update-ref --stdin
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

注意：这会改写所有提交的 SHA。之后要再跑一次 `scripts/check_secrets.py --history` 确认干净。

## 提交署名

现有提交的作者是 `Codex <codex@local>`。如果希望 GitHub 上显示你自己的名字和邮箱，发布前用
`git config user.name` / `user.email` 改好，或在方式 A 里重新提交。

## 密钥泄漏了怎么办

1. 立刻去对应平台**吊销并重新生成**该 Key（改代码、删提交都来不及——Key 一旦公开就算泄漏）；
2. 检查该 Key 的用量与账单；
3. 清理仓库：按方式 B 重写历史，或按方式 A 重新发布；
4. 确认 `.env` 仍在 `.gitignore` 中，并跑一次 `--history` 全量扫描。
