# CR-Agent 实施指南

**目的**：指导跨对话的逐步实现，每次对话打开这份文档即可知道"做到哪了、下一步是什么"。

---

## 当前状态

- [x] 架构设计文档完成 (`docs/code-review-agent-design.md`)
- [x] Phase 1: 核心可用 (16 commits, 42 tests)
- [x] Phase 2: 生产化 (4 Agents + Tool Calling + FastAPI + GitHub App)
- [ ] Phase 3: 可观测 + 评估 + 学习
- [ ] Phase 4: 打磨

---

## Phase 1: 核心可用（目标：本地 CLI 跑通一次完整审查）

### 完成标志

```bash
cr-agent review --diff tests/fixtures/sample.patch
# 30 秒内输出 JSON 格式的审查报告
```

### 实施步骤（按顺序）

#### Step 1.1: 项目初始化
- [x] 创建新项目目录 `cr-agent/`（独立于当前 smart-cs-agent 学习项目）
- [x] `pyproject.toml`：uv 管理，依赖列表见下方
- [x] 目录结构：按设计文档 §3 创建所有 `__init__.py`
- [x] `src/cr_agent/config.py`：Pydantic Settings（API key、模型名、超时等）
- [x] `.env.example`：DeepSeek API key 占位
- [x] 验证：`uv run python -c "from cr_agent import config; print(config.settings)"`

**核心依赖**：
```
httpx>=0.27        # LLM 调用
pydantic>=2.7      # 配置 + 输出约束
pydantic-settings  # 环境变量管理
jinja2             # Prompt 模板
click              # CLI
structlog          # 日志
pytest             # 测试
pytest-asyncio     # 异步测试
```

#### Step 1.2: LLM Client
- [x] `src/cr_agent/llm/client.py`：LLMClient 类
  - `chat(messages) -> str`
  - `chat_structured(messages, schema) -> BaseModel`
  - retry 循环（指数退避 + jitter）
  - DeepSeek API 调用（httpx async）
- [x] `src/cr_agent/llm/schema.py`：ReviewFinding + ReviewOutput Pydantic models
- [x] `src/cr_agent/llm/cost_tracker.py`：基础版（内存计数，不存 DB）
- [x] 单元测试：mock LLM 响应，验证 retry 和 structured output 解析
- [x] 集成测试：真实调一次 DeepSeek，验证连通性

**关键实现细节**：
- DeepSeek 的 chat API 兼容 OpenAI 格式，base_url = `https://api.deepseek.com`
- structured output 策略：system prompt 里附 JSON Schema + 示例 → 解析输出 → 失败则带错误信息 retry
- 注意 DeepSeek 可能返回 ```json ... ``` 包裹的输出，需要 regex 提取

#### Step 1.3: Diff Parser
- [x] `src/cr_agent/core/diff_parser.py`：解析 unified diff 格式
  - 输入：.patch 文件内容（字符串）
  - 输出：`list[FileDiff]`，每个包含 path, hunks, added_lines, removed_lines
- [x] `src/cr_agent/core/file_filter.py`：过滤规则
  - 默认排除：`*.lock`, `*.sum`, `vendor/`, `node_modules/`, `*.min.js`
- [x] 单元测试：准备 3-5 个 sample .patch 文件作为 fixtures
- [x] 验证：`uv run pytest tests/unit/test_diff_parser.py`

**关键实现细节**：
- Python 标准库无现成 unified diff parser（`difflib` 是生成 diff 的，不是解析的）
- 自己写：按 `---/+++/@@` 行分割，提取 hunk header 的行号
- 或用 `unidiff` 第三方库（轻量，400 star）

#### Step 1.4: Security Agent（第一个 Agent）
- [x] `src/cr_agent/agents/base.py`：BaseReviewAgent 抽象基类
  - metadata: AgentMetadata
  - `review(context) -> ReviewOutput`
- [x] `src/cr_agent/agents/security.py`：SecurityAgent 实现
- [x] `src/cr_agent/agents/prompts/security.j2`：完整的 system prompt
- [x] 验证：手动传一段有 SQL 注入的 diff，看能否检出

**Prompt 要点（security.j2 核心结构）**：
```
SYSTEM: 你是高级安全工程师，审查以下代码变更...
审查维度: [SQL注入, XSS, 命令注入, 硬编码密钥, 路径穿越, 反序列化...]
硬排除规则: [仅测试文件不报, DOS不报, 框架已保护不报...]
输出格式: 严格 JSON，每条 finding 包含 file/line/severity/description/suggestion
置信度: 只报 confidence >= 0.7 的
```

#### Step 1.5: Orchestrator（串行版）
- [x] `src/cr_agent/core/orchestrator.py`：ReviewOrchestrator
  - `run(diff_content: str) -> ReviewReport`
  - 串行调用：parse → filter → build_context → agent.review → report
  - 暂不并行，先跑通流程
- [x] `src/cr_agent/core/context_builder.py`：简化版
  - MVP：直接用 diff hunk + 上下 10 行作为 context（不拉完整文件）
- [x] 端到端测试：一个 .patch → 完整 JSON 报告

#### Step 1.6: CLI 入口
- [x] `src/cr_agent/cli.py`：Click CLI
  - `cr-agent review --diff <path>` — 从文件读 patch
  - `cr-agent review --pr <github_url>` — Phase 2 再做，先留占位
  - 输出：JSON 打印到 stdout / 写文件
- [x] `pyproject.toml` 注册 entry point：`[project.scripts] cr-agent = "cr_agent.cli:main"`
- [x] 验证：`uv run cr-agent review --diff tests/fixtures/sample.patch`

---

## Phase 2: 生产化（目标：Docker 一键跑，支持 GitHub Webhook）

### 前置条件
- Phase 1 全部完成
- 本地 CLI 能稳定产出合理的审查报告

### 实施步骤

#### Step 2.1: 补全 Agents
- [x] Logic Agent + prompt
- [x] Performance Agent + prompt
- [x] Style Agent + prompt
- [x] 并行执行改造（asyncio.gather + semaphore）

#### Step 2.2: FastAPI + 异步队列
- [x] `src/cr_agent/main.py`：FastAPI app
- [x] `src/cr_agent/api/routes/`：webhook, review, report, health
- [x] `src/cr_agent/worker/`：arq worker 入口
- [x] Redis 队列：幂等 + 优先级
- [x] Review 状态机：queued → running → completed/failed

#### Step 2.3: 数据库
- [x] PostgreSQL + SQLAlchemy async
- [x] Alembic 迁移
- [x] tenants / reviews / findings 三张表

#### Step 2.4: GitHub App 集成
- [x] 注册 GitHub App
- [x] Webhook 接收 + 签名验证
- [x] 拉 PR diff（GitHub API）
- [x] 回写 PR Review（inline comments）

#### Step 2.5: Docker
- [x] Dockerfile（multi-stage build）
- [x] docker-compose.yml：app + worker + postgres + redis
- [x] 验证：`docker compose up` → 发起审查 → 看到结果

---

## Phase 3: 可观测 + 评估 + 学习

#### Step 3.1: 可观测
- [ ] Prometheus metrics（review_duration, llm_tokens, findings_count）
- [ ] Structured logging (structlog → JSON)
- [ ] Grafana dashboard

#### Step 3.2: 评估体系
- [ ] 标注 20+ 个 PR 作为评估数据集
- [ ] `tests/eval/run_eval.py`：批量跑 + 算 precision/recall/F1
- [ ] CI 集成：每次改 prompt 自动跑 eval

#### Step 3.3: 反馈学习
- [ ] finding 投票 API（helpful/not_helpful/false_positive）
- [ ] Learned Rules 提取 Job
- [ ] Prompt 注入管线

---

## Phase 4: 打磨

- [ ] Web Dashboard（报告可视化）
- [ ] .cr-agent.yml 用户配置
- [ ] 降级策略完整实现
- [ ] 性能优化（缓存 GitHub 文件拉取结果）
- [ ] 安全加固（API Key hash 存储、输入校验）
- [ ] README + 文档 + Demo 录屏
- [ ] 开源发布

---

## 关键决策记录

| 决策 | 选择 | 理由 | 可回溯 |
|---|---|---|---|
| LLM Provider | DeepSeek | 便宜、国内快、代码能力不错 | 随时切，LLM Client 是 provider-agnostic |
| 包管理 | uv | 快，2025 标准 | 可换 poetry |
| diff 解析 | unidiff 库 or 自写 | 先用库，不够再自写 | 接口不变，内部替换 |
| structured output | prompt + regex 提取 | DeepSeek JSON mode 不够稳 | 稳定后可切原生 JSON mode |
| Phase 1 不做数据库 | 直接 stdout 输出 | 先验证 LLM 审查效果，再加工程化 | Phase 2 补 |
| Phase 1 context 简化 | diff hunk + 上下文 N 行 | 不拉完整文件避免 GitHub API 依赖 | Phase 2 加完整 context |

---

## 每次对话的开场指令

```
请打开 D:\cr-agent\docs\implementation-guide.md 查看当前进度，继续实现下一个未完成的 Step。
```

仓库地址: https://github.com/xcq486711/cr-agent
