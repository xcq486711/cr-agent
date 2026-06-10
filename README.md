# CR-Agent

多 Agent 并行代码审查系统 — 从 PR diff 中自动发现安全、逻辑、性能问题，输出定位到行号的结构化报告。

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-42_passed-brightgreen)

## 示例输出

输入一个包含 SQL 注入和硬编码密钥的 patch，输出：

| Severity | Category | File | Line | Issue |
|---|---|---|---|---|
| critical | security | `src/auth/login.py` | 22-23 | SQL 注入 — f-string 拼接用户输入构造查询 |
| critical | security | `src/utils/config.py` | 5 | 硬编码 SECRET_KEY |
| warning | performance | `src/auth/login.py` | 16 | 每次调用新建数据库连接，建议用连接池 |
| suggestion | style | `src/utils/config.py` | 1 | 缺少模块级 docstring |

> 4 agents · 9 files · 8 seconds · $0.0007 cost

## 核心特性

| 特性 | 说明 |
|---|---|
| **自研 LLM 编排层** | retry、fallback、structured output、tool calling，不依赖 LangChain |
| **声明式 Agent 调度** | Agent 自己声明并发安全性/超时/优先级，编排器自动分区 |
| **Tool Calling** | Agent 可自主读文件、搜代码、探索调用链 |
| **三阶段管线** | 发现 → 交叉验证 → 置信度过滤 |
| **GitHub App 原生集成** | Webhook 自动触发 → 审查 → PR inline comment |

设计参考了 Claude Code (Anthropic CLI) 的生产源码：[设计文档](docs/code-review-agent-design.md)

## 架构

```
入口层:     Webhook / CLI / Web UI → 统一 ReviewRequest
API 层:     FastAPI → 认证 → 限流 → 入队 (Redis)
Worker 层:  编排器 → parse diff → filter → context → 4 Agents 并行 → dedup → report
Agent 层:   Security / Logic / Performance / Style (每维度独立 LLM prompt)
LLM 层:     httpx → retry (指数退避) → fallback → structured output
回写层:     GitHub PR Review (inline comments) + Check Run
```

## 快速开始

### 安装

```bash
git clone git@github.com:xcq486711/cr-agent.git && cd cr-agent
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 配置

```bash
cp .env.example .env
# 编辑 .env: 填入 DEEPSEEK_API_KEY
```

### 使用

```bash
# 生成 diff
cd 你的项目
git diff HEAD~3 > /tmp/input.patch          # Linux/Mac
git diff HEAD~3 | Out-File -Encoding utf8 D:\cr-agent\input.patch  # Windows

# 运行审查
cd cr-agent
cr-agent review -d input.patch -f markdown -o report.md
```

## 使用方式

### CLI（零依赖，即开即用）

```bash
cr-agent review -d changes.patch                    # JSON 输出
cr-agent review -d changes.patch -f markdown        # Markdown
cr-agent review -d changes.patch -t 0.9 -q          # 调高阈值
```

### CLI + Tool Calling（Agent 自主探索代码库）

```bash
cr-agent review -d changes.patch -w /path/to/project -f markdown -o report.md
```

Agent 可用工具：`read_file`（读函数定义）、`grep`（搜索引用）、`list_dir`（浏览目录）

### API 服务（Docker 一键启动）

```bash
docker compose up -d
curl -X POST http://localhost:8000/api/v1/reviews \
  -H "Content-Type: application/json" \
  -d '{"diff_content": "diff --git ..."}'
curl http://localhost:8000/api/v1/reviews/<review_id>
```

### GitHub App（自动审查每个 PR）

1. [注册 GitHub App](https://github.com/settings/apps/new)
2. `.env` 填入 `GITHUB_APP_ID` / `GITHUB_APP_PRIVATE_KEY` / `GITHUB_WEBHOOK_SECRET`
3. Webhook URL → `https://你的域名/api/v1/webhook/github`

## 技术点

| 层 | 选型 | 亮点 |
|---|---|---|
| Web | FastAPI + Uvicorn | async 原生 |
| LLM | 自研编排层 | retry 指数退避+jitter, 529→fallback, Pydantic 强约束 |
| Agent | 声明式元数据 | concurrency_safe 自动分区调度 |
| 队列 | Redis + arq | 优先级 + 幂等 + 死信 |
| 存储 | PostgreSQL | JSONB + 异步 SQLAlchemy |
| 监控 | structlog | 结构化 JSON 日志 |

## 项目结构

```
src/cr_agent/
├── cli.py              # CLI 入口
├── main.py             # FastAPI 服务入口
├── agents/             # Security / Logic / Performance / Style
├── core/               # 编排器、Diff 解析、上下文构建、去重
├── llm/                # LLM Client、Tool Registry、Cost Tracker
├── api/                # REST 路由 + Webhook + 认证
├── storage/            # SQLAlchemy ORM 模型
├── worker/             # arq 异步任务
└── integrations/       # GitHub App 认证 + API 客户端
```

## 文档

- [架构设计 (1,700 行完整系统设计)](docs/code-review-agent-design.md)
- [实施指南](docs/implementation-guide.md)

## License

MIT
