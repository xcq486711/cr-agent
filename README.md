# CR-Agent

多 Agent 并行代码审查系统 — 从 PR diff 中自动发现安全、逻辑、性能问题，输出定位到行号的结构化报告。

## 核心特性

- **4 维度并行审查**：Security / Logic / Performance / Style 四个 Agent 并行执行
- **自研 LLM 编排层**：retry + fallback + structured output + tool calling，不依赖 LangChain
- **Tool Calling**：Agent 可自主读文件、搜索代码、探索调用链（`--workspace` 模式）
- **GitHub App 集成**：Webhook 自动触发 → 审查 → 发 PR inline comment
- **三阶段管线**：发现 → 交叉验证 → 置信度过滤，控制误报率

## 快速开始

### 1. 安装

```bash
git clone <repo-url> && cd cr-agent
python -m venv .venv
source .venv/Scripts/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek API Key
```

### 3. 使用

```bash
# 审查 git diff
cd 你的项目
git diff HEAD~3 | Out-File -Encoding utf8 D:\cr-agent\input.patch
cd D:\cr-agent
cr-agent review -d input.patch -f markdown -o report.md

# 查看报告
start report.md
```

## 使用方式

### CLI 模式（无需基础设施）

```bash
# 基础审查
cr-agent review -d changes.patch

# Markdown 输出 + 保存文件
cr-agent review -d changes.patch -f markdown -o report.md

# 调高置信度阈值
cr-agent review -d changes.patch -t 0.9
```

### Tool 模式（Agent 自主探索代码库）

```bash
cr-agent review -d changes.patch -w . -f markdown -o report.md
```

`-w` 指定项目根目录后，Agent 可以：
- `read_file` — 读取完整文件（看 diff 上下文外的函数定义）
- `grep` — 搜索代码库（找调用方/被调用方/引用）
- `list_dir` — 浏览目录结构

### API 模式（需要 Docker）

```bash
docker compose up -d

# 提交审查
curl -X POST http://localhost:8000/api/v1/reviews \
  -H "Content-Type: application/json" \
  -d '{"diff_content": "diff --git ..."}'

# 查询结果
curl http://localhost:8000/api/v1/reviews/<review_id>
```

### GitHub App（自动审查 PR）

1. 注册 GitHub App → 获取 App ID + 私钥
2. 配置 `.env` 中的 `GITHUB_APP_ID`、`GITHUB_APP_PRIVATE_KEY`、`GITHUB_WEBHOOK_SECRET`
3. 设置 webhook URL 指向 `https://你的域名/api/v1/webhook/github`
4. 之后每次 PR 提交，自动触发审查并回写 inline comment

## 项目结构

```
src/cr_agent/
├── cli.py          # CLI 入口
├── main.py         # FastAPI 入口
├── config.py       # 配置管理
├── agents/         # 审查 Agent (Security/Logic/Performance/Style)
├── core/           # 编排器 + Diff 解析 + 上下文构建
├── llm/            # LLM 编排层 (retry/fallback/structured output/tools)
├── api/            # HTTP 路由 + 中间件
├── storage/        # 数据库模型
├── worker/         # 异步任务 Worker
└── integrations/   # GitHub API + 认证
```

## 技术栈

Python 3.12 · FastAPI · DeepSeek · PostgreSQL · Redis · Docker · httpx · SQLAlchemy · structlog

## 文档

- [架构设计](docs/code-review-agent-design.md) — 完整系统设计
- [实施指南](docs/implementation-guide.md) — 开发路线图

## License

MIT
