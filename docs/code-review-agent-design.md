# CodeReview Agent — 生产级架构设计文档

**项目名称**: CR-Agent (Code Review Agent)
**版本**: v1.2
**日期**: 2025-06-09
**定位**: 独立 SaaS 服务，接收 GitHub PR / GitLab MR / 手动提交的 diff，输出结构化审查报告
**参考工程**: Claude Code (Anthropic CLI) — Tool 系统、Retry/Fallback、Cost Tracking、Context 管理

---

## Executive Summary (面试用 30 秒版)

**一句话**：多 Agent 并行代码审查系统，从 PR diff 中自动发现安全、逻辑、性能问题，输出定位到行号的结构化报告。

**核心技术亮点**：

1. **自研 LLM 编排层** — 不依赖 LangChain，500 行覆盖 retry (指数退避+jitter)、fallback (连续 529→切模型)、structured output (Pydantic 强约束)
2. **声明式 Agent 元数据驱动调度** — 并行安全性、超时、优先级由 Agent 自己声明，编排器自动分区（借鉴 Claude Code `partitionToolCalls`）
3. **三阶段审查管线** — 发现 → 交叉验证 → 置信度过滤，误报率压制在 15% 以下（借鉴 Claude Code `/security-review` 的三阶段+17 条硬排除）
4. **项目级审查学习** — 从用户反馈中自动提取 suppress/emphasize 规则，precision 随使用时间持续提升
5. **多语言感知** — Python/TS/Go/Rust 各有独立 LanguageProfile，审查重点和豁免规则随语言自适应

**关键指标（目标）**：
- Precision ≥ 85%（每 100 条 finding 中 ≥ 85 条有用）
- 中等 PR (300 行) 端到端 < 30s
- 单次审查成本 < ¥0.1

---

## 1. 系统全景

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            CR-Agent SaaS                                      │
│                                                                              │
│  入口层 (多入口汇聚为统一 ReviewRequest)                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │ GitHub App   │  │ CLI          │  │ Web UI       │                       │
│  │ (Webhook)    │  │ (手动触发)    │  │ (粘贴PR URL) │                       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                       │
│         └──────────────────┼─────────────────┘                               │
│                            ▼                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                     API Process (FastAPI)                                │ │
│  │  routes · auth · rate_limit · request_id                                │ │
│  │  职责: 接请求, 校验, 入队, 查询状态 — 不跑 LLM                           │ │
│  └─────────────────────────────┬───────────────────────────────────────────┘ │
│                                │ enqueue (Redis)                              │
│                                ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                   Worker Process (独立进程)                               │ │
│  │  orchestrator · agents · context_builder · llm · dedup                  │ │
│  │  职责: 从队列取任务, 跑 LLM 审查, 写结果回 DB                             │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  共享层 (API 和 Worker 都依赖)                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │ Storage      │  │ LLM Client   │  │ Observability│                       │
│  │ (PG + Redis) │  │ (httpx)      │  │ (Prom + Log) │                       │
│  └──────────────┘  └──────────────┘  └──────────────┘                       │
│                                                                              │
│  回写层                                                                       │
│  ┌──────────────────────────────────────────────────────────────────────────┐│
│  │ GitHub App API: 发 PR Review (inline comment) / 更新 Check Run           ││
│  └──────────────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────────────┘
```

**关键架构决策**：

| 决策 | 选择 | 理由 |
|---|---|---|
| API 与 Worker 分离 | 独立进程, 通过 Redis 队列通信 | Worker 跑 LLM 耗时 30-90s, 若同进程则健康检查超时被 K8s 杀 |
| GitHub 集成方式 | GitHub App (非 OAuth App) | 可发 inline review comment (PR Review API), 权限粒度更细 |
| 多入口设计 | Webhook + CLI + Web UI 三入口 | MVP 阶段用户大概率手动触发 (CLI/Web), 不只是 webhook |
| 共享代码边界 | llm/, storage/, schema/ 共享; routes/ 独占 API; agents/ 独占 Worker | 可独立部署和扩缩容 |

---

## 2. 核心数据流

```
触发源 (三种入口)
  ├─ GitHub Webhook: pull_request.opened / synchronize
  ├─ CLI: cr-agent review --pr <url> 或 --diff <file>
  └─ Web UI: 粘贴 PR URL
        │
        ▼ 统一转换为 ReviewRequest
┌─────────────────────┐
│ 1. Diff 获取 + 解析  │  Webhook: 从 event payload 拿 diff
│                     │  CLI/Web: 主动调 GitHub API 拉 diff
│                     │  → 结构化 FileDiff[]
│    + 文件过滤        │  过滤: lock文件、生成代码、超大文件
└─────────────────────┘
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│ 2. Context 构建      │  拉取完整文件(非仅diff)、相关类型定义、
│    (最关键一步)      │  项目 .cr-agent.yml 配置、历史 review 模式
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│ 3. 任务分发          │  按文件/模块分片 → 分配给 N 个 Agent Worker
│    (Fan-out)        │  每个 worker 带独立 context window
└─────────────────────┘
        │
        ├──▶ Security Agent ──┐
        ├──▶ Logic Agent ─────┤
        ├──▶ Perf Agent ──────┤  并行执行
        ├──▶ Style Agent ─────┤
        │                     │
        ▼                     ▼
┌─────────────────────┐
│ 4. 结果合并 + 去重   │  相同行号的 comment 去重/合并
│    + 置信度过滤      │  低于阈值的 finding 静默丢弃
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│ 5. 报告生成 + 投递   │  结构化JSON + Markdown渲染
│                     │  → API 返回 (GET /reviews/{id})
│                     │  → GitHub: PR Review (inline comments)
│                     │  → GitHub: Check Run (summary)
└─────────────────────┘
```

---

## 3. 目录结构

```
cr-agent/
├── pyproject.toml              # 项目元数据 + 依赖 (uv/poetry)
├── Dockerfile
├── docker-compose.yml          # 本地开发: postgres + redis + app
├── alembic/                    # DB 迁移
│   └── versions/
├── src/
│   └── cr_agent/
│       ├── __init__.py
│       ├── main.py             # FastAPI app 入口
│       ├── config.py           # Pydantic Settings, 环境变量管理
│       │
│       ├── api/                # ===== HTTP 层 =====
│       │   ├── __init__.py
│       │   ├── routes/
│       │   │   ├── webhook.py      # GitHub/GitLab webhook 接收
│       │   │   ├── review.py       # 手动提交审查 API
│       │   │   ├── report.py       # 查询审查报告
│       │   │   └── health.py       # 健康检查 + readiness
│       │   ├── middleware/
│       │   │   ├── auth.py         # API Key / JWT 验证
│       │   │   ├── rate_limit.py   # 令牌桶限流
│       │   │   └── request_id.py   # 请求追踪 ID
│       │   └── deps.py            # FastAPI 依赖注入
│       │
│       ├── core/               # ===== 业务核心 =====
│       │   ├── __init__.py
│       │   ├── orchestrator.py     # 审查编排器: 分片 → fan-out → 合并
│       │   ├── context_builder.py  # 构建 LLM 输入的完整上下文
│       │   ├── diff_parser.py      # Unified diff → FileDiff 结构
│       │   ├── file_filter.py      # 智能文件过滤 (lock, generated, binary)
│       │   ├── dedup.py            # 三级去重: 精确 → 语义 → 相邻合并
│       │   ├── language.py         # 多语言适配: profile + prompt 注入
│       │   ├── queue.py            # 任务队列: 优先级 + 幂等 + 死信
│       │   └── report_builder.py   # Finding[] → 最终报告
│       │
│       ├── agents/             # ===== Agent 定义 =====
│       │   ├── __init__.py
│       │   ├── base.py            # BaseReviewAgent 抽象
│       │   ├── security.py        # 安全审查: 注入、越权、密钥泄露
│       │   ├── logic.py           # 逻辑审查: 边界条件、空指针、竞态
│       │   ├── performance.py     # 性能审查: N+1、内存泄漏、阻塞调用
│       │   ├── style.py           # 风格审查: 命名、复杂度、可读性
│       │   ├── static_rules.py    # 静态规则 Agent (LLM 降级兜底, regex+AST)
│       │   └── prompts/           # Prompt 模板 (Jinja2)
│       │       ├── security.j2
│       │       ├── logic.j2
│       │       ├── performance.j2
│       │       └── style.j2
│       │
│       ├── llm/                # ===== LLM 编排层 =====
│       │   ├── __init__.py
│       │   ├── client.py          # 统一 LLM 调用: retry, timeout, fallback
│       │   ├── router.py          # 按任务类型选模型 + 参数
│       │   ├── schema.py          # Pydantic 输出约束 (structured output)
│       │   ├── tokenizer.py       # Token 计数 + context 截断策略
│       │   └── cost_tracker.py    # 单次审查的 token/cost 统计
│       │
│       ├── integrations/       # ===== 外部集成 =====
│       │   ├── __init__.py
│       │   ├── github.py          # GitHub App: 拉 diff, 发 inline review, Check Run
│       │   ├── gitlab.py          # GitLab API (同构)
│       │   └── git.py             # 本地 git 操作 (clone, diff)
│       │
│       ├── worker/             # ===== Worker 进程入口 =====
│       │   ├── __init__.py
│       │   ├── entry.py           # arq worker 启动入口
│       │   ├── tasks.py           # 注册的 async task 函数
│       │   └── cleanup.py         # 孤儿任务清理 Job
│       │
│       ├── storage/            # ===== 持久化 =====
│       │   ├── __init__.py
│       │   ├── models.py          # SQLAlchemy ORM models
│       │   ├── repositories.py    # Repository pattern CRUD
│       │   └── migrations/        # Alembic 迁移脚本
│       │
│       └── observability/      # ===== 可观测性 =====
│           ├── __init__.py
│           ├── logger.py          # Structured JSON logging
│           ├── metrics.py         # Prometheus metrics
│           └── tracing.py         # OpenTelemetry spans
│
├── tests/
│   ├── unit/
│   │   ├── test_diff_parser.py
│   │   ├── test_context_builder.py
│   │   ├── test_dedup.py
│   │   └── test_agents/
│   ├── integration/
│   │   ├── test_github_integration.py
│   │   └── test_full_review_flow.py
│   └── eval/                   # ===== 评估体系 =====
│       ├── dataset/            # 人工标注的 PR + 期望 findings
│       │   ├── pr_001.json
│       │   └── pr_002.json
│       ├── run_eval.py         # 批量跑评估
│       ├── metrics.py          # precision, recall, F1, latency
│       └── report.py           # 生成评估报告
│
├── deploy/
│   ├── k8s/                    # Kubernetes 部署清单
│   └── terraform/              # 基础设施 (可选)
│
└── docs/
    ├── api.md                  # API 文档
    ├── architecture.md         # 本文档
    └── evaluation.md           # 评估方法论
```

---

## 4. 核心设计模式 (From Claude Code 源码逆向)

> 以下设计模式从 Claude Code (Anthropic CLI) 源码中提炼，经验证为生产级实践。

### 4.1 Agent 声明式元数据 (参考 `Tool.ts`)

Claude Code 中每个 Tool 不是简单函数，而是携带完整元数据的声明式对象。我们的每个 Review Agent 也应如此：

```python
@dataclass
class AgentMetadata:
    """Agent 声明式元数据 — 编排器据此做调度决策，而非硬编码"""
    name: str
    category: Literal["security", "logic", "performance", "style"]
    concurrency_safe: bool = True       # 是否可与其他 agent 并行
    is_read_only: bool = True           # 是否只读(未来 AutoFix 为 False)
    is_destructive: bool = False        # 是否不可逆操作
    max_context_tokens: int = 12_000    # 单次调用 context 预算
    timeout_seconds: int = 60           # 超时阈值
    priority: int = 1                   # 优先级 (1最高)
    model_preference: str = "deepseek-chat"  # 偏好模型
    fallback_model: str | None = None   # 降级模型
```

编排器根据元数据自动分区调度：
```python
def partition_agents(agents: list[BaseReviewAgent]) -> tuple[list, list]:
    """参考 Claude Code 的 partitionToolCalls: 并行安全的一起跑, 不安全的串行"""
    concurrent = [a for a in agents if a.metadata.concurrency_safe]
    serial = [a for a in agents if not a.metadata.concurrency_safe]
    return concurrent, serial
```

### 4.2 分级降级状态机 (参考 `withRetry.ts`)

Claude Code 不是无脑 retry，而是带状态的分级降级：

```python
class DegradationStateMachine:
    """
    降级链: 主力模型 → 轻量模型 → 静态规则兜底
    
    关键设计 (学自 Claude Code):
    - 区分前台/后台: 用户等着的请求才重试, 后台预热直接丢弃
    - 连续失败计数: consecutive_failures >= 3 → 触发降级
    - 冷却期: 降级后不立刻恢复, 等冷却期结束才尝试主力模型
    - 熔断器: 连续降级 N 次后停止尝试, 直到手动恢复
    """
    
    class State(Enum):
        PRIMARY = "primary"              # DeepSeek-chat (强推理)
        FALLBACK = "fallback"            # DeepSeek-lite (快+便宜)
        STATIC_RULES = "static_rules"    # 正则 + AST (零 API 调用)
        CIRCUIT_OPEN = "circuit_open"    # 熔断, 拒绝新请求

    def __init__(self):
        self.state = self.State.PRIMARY
        self.consecutive_failures = 0
        self.cooldown_until: float | None = None
        self.max_consecutive_failures = 3  # 同 Claude Code 的 MAX_529_RETRIES

    async def execute_with_degradation(self, func, *args):
        """执行带降级的 LLM 调用"""
        if self.state == self.State.CIRCUIT_OPEN:
            raise CircuitOpenError("Agent circuit breaker open")
        
        try:
            result = await func(*args)
            self._on_success()
            return result
        except (RateLimitError, TimeoutError, APIError) as e:
            return await self._on_failure(e, func, *args)

    def _on_success(self):
        self.consecutive_failures = 0
        # 冷却期结束后可恢复
        if self.cooldown_until and time.time() > self.cooldown_until:
            self.state = self.State.PRIMARY

    async def _on_failure(self, error, func, *args):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self._degrade()
        # 降级后重试一次
        return await func(*args, model=self._current_model())
```

### 4.3 Context 预算管理 (参考 `autoCompact.ts`)

Claude Code 的核心公式：`有效窗口 = 模型窗口 - 输出预留 - 安全 buffer`

```python
class ContextBudgetManager:
    """
    Token 预算管理器 — 不是简单截断, 而是按优先级填充
    
    参考 Claude Code:
    - AUTOCOMPACT_BUFFER_TOKENS = 13_000 (安全余量)
    - 连续失败 3 次 → 熔断 (MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3)
    - 超限时分片, 非丢弃
    """
    
    # 优先级队列 (数字越小优先级越高)
    PRIORITY_DIFF_HUNK = 1          # diff 变更块 (必须有)
    PRIORITY_FULL_FUNCTION = 2      # 被修改函数的完整体
    PRIORITY_TYPE_DEFINITIONS = 3   # import 的类型/接口
    PRIORITY_CALLERS = 4            # 调用方 (影响范围)
    PRIORITY_PROJECT_RULES = 5     # .cr-agent.yml 自定义规则
    PRIORITY_HISTORY = 6            # 历史 review 模式

    def __init__(self, model: str = "deepseek-chat"):
        self.model_context_window = 64_000  # DeepSeek context
        self.output_reserve = 4_000         # 输出预留
        self.safety_buffer = 2_000          # 安全余量
        self.effective_budget = (
            self.model_context_window - self.output_reserve - self.safety_buffer
        )

    def pack(self, items: list[ContextItem]) -> list[ContextItem]:
        """按优先级贪心填充, 超预算时低优先级项被丢弃"""
        items.sort(key=lambda x: x.priority)
        packed = []
        remaining = self.effective_budget
        for item in items:
            if item.token_count <= remaining:
                packed.append(item)
                remaining -= item.token_count
            elif item.priority <= self.PRIORITY_FULL_FUNCTION:
                # 高优先级项: 截断而非丢弃
                truncated = item.truncate_to(remaining)
                packed.append(truncated)
                break
            # 低优先级项: 静默丢弃
        return packed
```

### 4.4 多阶段审查 + 误报过滤 (参考 `/security-review` 命令)

Claude Code 的 security-review 命令使用三阶段设计，我们采用相同模式：

```python
class MultiStageReviewPipeline:
    """
    三阶段审查管线 (直接借鉴 Claude Code /security-review):
    
    Phase 1: 并行发现 (fan-out)
      → 每个 Agent 独立产出 findings (允许误报)
    
    Phase 2: 交叉验证 (parallel verification)
      → 每个 finding 由另一个 Agent 验证真实性 (置信度评分)
    
    Phase 3: 阈值过滤 + 去重
      → confidence < 0.7 的静默丢弃
      → 相同行号的 findings 合并
    """

    async def run(self, context: ReviewContext) -> list[ReviewFinding]:
        # Phase 1: 并行发现
        raw_findings = await self._phase_discover(context)
        
        # Phase 2: 并行验证 (每个 finding 独立验证)
        verified = await asyncio.gather(*[
            self._phase_verify(f, context) for f in raw_findings
        ])
        
        # Phase 3: 过滤 + 去重
        return self._phase_filter(verified)

    async def _phase_verify(self, finding: ReviewFinding, ctx: ReviewContext):
        """用轻量模型做二次验证, 输出置信度 0-1"""
        prompt = f"""
        以下是一个代码审查发现, 请验证其是否为真实问题:
        
        Finding: {finding.description}
        File: {finding.file}:{finding.line_start}
        Context: {ctx.get_surrounding_code(finding.file, finding.line_start, 20)}
        
        硬排除规则 (以下不算问题):
        1. 仅出现在测试文件中的问题
        2. 理论性的安全问题(无具体攻击路径)
        3. 代码风格偏好而非真正的 bug
        4. 已有框架保护的漏洞 (如 React 的 XSS 防护)
        
        输出: {{"is_valid": bool, "confidence": 0.0-1.0, "reason": "..."}}
        """
        return await self.llm.chat_structured(prompt, schema=VerificationResult)
```

### 4.5 Cost Tracking: 每次调用即时累加 (参考 `cost-tracker.ts`)

```python
class CostTracker:
    """
    实时成本追踪 — 参考 Claude Code 的 addToTotalSessionCost()
    
    关键设计:
    - 每次 API 调用后 *立即* 累加 (不是事后统计)
    - 按 model + agent_type 分维度
    - 超预算时主动熔断 (不是等月底账单)
    - 持久化到 DB (单次审查可追溯)
    """
    
    def __init__(self, budget_usd: float | None = None):
        self.budget_usd = budget_usd
        self._usage: dict[str, ModelUsage] = {}
        self._total_cost_usd: float = 0.0

    def record(self, model: str, agent_type: str, usage: TokenUsage):
        """每次 LLM 调用后立即调用"""
        cost = self._calculate_cost(model, usage)
        key = f"{model}:{agent_type}"
        
        if key not in self._usage:
            self._usage[key] = ModelUsage()
        self._usage[key].add(usage, cost)
        self._total_cost_usd += cost
        
        # 预算熔断
        if self.budget_usd and self._total_cost_usd >= self.budget_usd:
            raise BudgetExceededError(
                f"Review cost {self._total_cost_usd:.4f} exceeded budget {self.budget_usd}"
            )

    def summary(self) -> dict:
        """返回本次审查的成本摘要"""
        return {
            "total_cost_usd": self._total_cost_usd,
            "by_model_agent": {k: v.to_dict() for k, v in self._usage.items()},
            "total_input_tokens": sum(u.input_tokens for u in self._usage.values()),
            "total_output_tokens": sum(u.output_tokens for u in self._usage.values()),
        }
```

### 4.6 Streaming Tool Execution (参考 `StreamingToolExecutor.ts`)

Claude Code 的流式工具执行器在工具还在输出时就开始执行下一个，我们的 Agent 编排也应支持流式结果：

```python
class StreamingReviewExecutor:
    """
    流式审查执行器 — 不等所有 Agent 完成才返回
    
    参考 Claude Code 的 StreamingToolExecutor:
    - addTool() 时立即开始执行 (不等队列满)
    - 结果按接收顺序缓冲, 按添加顺序 yield (保序)
    - 某个 Agent 失败不阻塞其他 Agent
    - 支持 discard(): 流式降级时丢弃已失效的结果
    """
    
    async def execute(
        self, 
        agents: list[BaseReviewAgent], 
        context: ReviewContext,
        max_concurrent: int = 10
    ) -> AsyncGenerator[ReviewFinding, None]:
        semaphore = asyncio.Semaphore(max_concurrent)
        results_queue = asyncio.Queue()
        
        async def run_agent(agent):
            async with semaphore:
                try:
                    output = await asyncio.wait_for(
                        agent.review(context),
                        timeout=agent.metadata.timeout_seconds
                    )
                    for finding in output.findings:
                        await results_queue.put(finding)
                except asyncio.TimeoutError:
                    logger.warning(f"Agent {agent.metadata.name} timed out")
                except Exception as e:
                    logger.error(f"Agent {agent.metadata.name} failed: {e}")
                    # 不阻塞其他 agent

        # 并行启动所有 agent
        tasks = [asyncio.create_task(run_agent(a)) for a in agents]
        
        # 流式 yield 结果
        done_count = 0
        while done_count < len(tasks):
            try:
                finding = await asyncio.wait_for(results_queue.get(), timeout=1.0)
                yield finding
            except asyncio.TimeoutError:
                # 检查是否所有 task 都已完成
                done_count = sum(1 for t in tasks if t.done())
```

---

## 5. 核心模块详细设计 (结合 Claude Code 模式强化)

### 5.1 LLM 编排层 (`src/cr_agent/llm/`)

这是最核心的自研层，替代 LangChain：

```python
# llm/client.py — 统一 LLM 调用接口 (参考 Claude Code withRetry + client.ts)
class LLMClient:
    """
    职责:
    - 统一接口: chat() / chat_structured() / chat_with_tools()
    - 分级 retry (学自 Claude Code withRetry.ts):
      · 指数退避 + jitter: base_delay * 2^attempt + random(0, 0.25*delay)
      · 429: 读取 retry-after header, 有则遵守, 无则退避
      · 529 (过载): 连续 3 次 → FallbackTriggered → 切模型
      · 连接错误: 重建 client (参考 Claude Code 的 stale connection 检测)
    - 速率限制 (令牌桶, 按 API key 隔离)
    - 成本即时累加 (每次调用后立即 record 到 CostTracker)
    - Fallback 链: deepseek-chat → deepseek-reasoner → 静态规则
    """

    BASE_DELAY_MS = 500              # 同 Claude Code
    MAX_RETRIES = 10                 # 同 Claude Code DEFAULT_MAX_RETRIES
    MAX_CONSECUTIVE_529 = 3          # 同 Claude Code MAX_529_RETRIES
    
    async def chat(self, messages, **kwargs) -> str: ...

    async def chat_structured(self, messages, schema: type[BaseModel], **kwargs) -> BaseModel:
        """
        强制结构化输出:
        1. 附加 JSON Schema 到 system prompt
        2. 解析 LLM 输出为 Pydantic model
        3. 校验失败 → 带错误信息 retry (最多 3 次)
        4. 3 次后仍失败 → 抛出 StructuredOutputError
        """
        ...

    async def _retry_loop(self, operation, max_retries=None):
        """
        核心 retry 循环 — 直接参考 Claude Code withRetry 的 AsyncGenerator 模式:
        - 每次失败 yield 一个 RetryEvent (用于可观测性)
        - 区分可恢复错误 vs 不可恢复错误
        - 429/529 → retry, 400/401 → 不 retry
        """
        consecutive_529 = 0
        for attempt in range(1, (max_retries or self.MAX_RETRIES) + 1):
            try:
                return await operation()
            except RateLimitError as e:
                delay = self._get_retry_delay(attempt, e.retry_after)
                await asyncio.sleep(delay)
            except OverloadedError:
                consecutive_529 += 1
                if consecutive_529 >= self.MAX_CONSECUTIVE_529:
                    raise FallbackTriggeredError(self.current_model, self.fallback_model)
                delay = self._get_retry_delay(attempt)
                await asyncio.sleep(delay)
            except (AuthError, ValidationError):
                raise  # 不可恢复, 立即抛出

    def _get_retry_delay(self, attempt: int, retry_after: float | None = None) -> float:
        """指数退避 + jitter (同 Claude Code getRetryDelay)"""
        if retry_after:
            return retry_after
        base = min(self.BASE_DELAY_MS / 1000 * (2 ** (attempt - 1)), 32.0)
        jitter = random.random() * 0.25 * base
        return base + jitter
```

```python
# llm/router.py — 模型路由
class ModelRouter:
    """
    按任务类型 + 上下文长度选择最优模型配置:
    - security review → deepseek-chat (强推理, temperature=0.1)
    - style review → deepseek-chat (快, temperature=0.3)
    - 超长 diff (>50 files) → 分片 + 并行, 单片限 8K tokens
    """
    def select(self, task_type: TaskType, context_tokens: int) -> ModelConfig: ...
```

```python
# llm/schema.py — 输出约束
class ReviewFinding(BaseModel):
    """单条审查发现 — LLM 必须严格输出此格式"""
    file: str
    line_start: int
    line_end: int
    severity: Literal["critical", "warning", "suggestion", "nitpick"]
    category: Literal["security", "logic", "performance", "style"]
    title: str                    # 一句话标题
    description: str              # 详细说明
    suggestion: str | None        # 修复建议 (可选 code block)
    confidence: float             # 0.0 ~ 1.0, 低于阈值会被过滤

class ReviewOutput(BaseModel):
    """单个 Agent 的完整输出"""
    findings: list[ReviewFinding]
    summary: str                  # 该维度的总体评价
    tokens_used: int
```

### 5.2 Agent Workers (`src/cr_agent/agents/`) — 声明式元数据驱动

```python
# agents/base.py (参考 Claude Code buildTool 模式: 元数据声明 + 统一接口)
class BaseReviewAgent(ABC):
    """所有审查 Agent 的基类 — 元数据驱动编排决策"""

    metadata: AgentMetadata  # 子类必须声明 (参考 4.1 节)

    def __init__(self, llm: LLMClient, config: AgentConfig):
        self.llm = llm
        self.config = config

    @abstractmethod
    def system_prompt(self) -> str: ...

    @abstractmethod
    def build_user_prompt(self, context: ReviewContext) -> str: ...

    async def review(self, context: ReviewContext) -> ReviewOutput:
        """执行审查 — 统一流程, 自动成本追踪 + 超时控制"""
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": self.build_user_prompt(context)},
        ]
        return await self.llm.chat_structured(
            messages, schema=ReviewOutput,
            model=self.metadata.model_preference,
            timeout=self.metadata.timeout_seconds,
        )
```

```python
# agents/security.py (参考 Claude Code /security-review 三阶段 + 17条排除规则)
class SecurityAgent(BaseReviewAgent):
    """
    三阶段安全审查:
    Phase 1: 发现 — 按 OWASP Top 10 分类扫描
    Phase 2: 验证 — 每条 finding 用轻量模型交叉验证
    Phase 3: 过滤 — confidence < 0.7 静默丢弃
    
    硬排除规则 (借鉴 Claude Code, 大幅减少误报):
    1. 仅测试文件 → 不报
    2. DOS/资源耗尽 → 不报
    3. 框架已保护的漏洞 (React XSS, Django CSRF) → 不报
    4. 环境变量/CLI 参数 → 视为可信值
    5. 理论性问题(无具体攻击路径) → 不报
    """
    metadata = AgentMetadata(
        name="security", category="security",
        concurrency_safe=True, max_context_tokens=16_000,
        timeout_seconds=90, priority=1,
        model_preference="deepseek-chat",
    )
```

### 5.3 编排器 (`src/cr_agent/core/orchestrator.py`) — 分区调度

```python
class ReviewOrchestrator:
    """
    核心编排逻辑 (参考 Claude Code toolOrchestration.ts 的分区模式):
    
    1. 接收 ReviewRequest
    2. 解析 diff → FileDiff[]
    3. 过滤不需要审查的文件
    4. 构建上下文 (拉完整文件, 类型定义等)
    5. 按文件分片, 确保每片 < token 预算 (ContextBudgetManager)
    6. 分区调度:
       - concurrent_safe agents → 并行 (semaphore 限流)
       - serial agents (如 AutoFix) → 串行
    7. 三阶段管线: 发现 → 验证 → 过滤
    8. 生成最终报告
    
    成本控制 (验证阶段):
    - Phase 1 (发现): 4 agents × N 片 = 4N 次调用
    - Phase 2 (验证): 仅对 severity >= warning 的 findings 验证
      · 预估: ~40% findings 需验证 → 实际 ~6-12 次额外调用
      · 用 deepseek-lite (便宜模型) 做验证, 非主力模型
    - Phase 3 (过滤): 纯本地计算, 无 API 调用
    """

    async def run(self, request: ReviewRequest) -> ReviewReport:
        # 1. Parse
        diffs = self.diff_parser.parse(request.diff_content)

        # 2. Filter
        diffs = self.file_filter.apply(diffs, request.config)

        # 3. Build context with token budget
        contexts = await self.context_builder.build(diffs, request.repo_info)

        # 4. Partition agents (参考 Claude Code partitionToolCalls)
        concurrent_agents, serial_agents = partition_agents(self.agents)

        # 5. Fan-out: 并行安全的 agents 一起跑
        tasks = []
        for ctx in contexts:
            for agent in concurrent_agents:
                tasks.append(agent.review(ctx))

        # 6. Parallel execution with concurrency limit
        results = await gather_with_limit(tasks, max_concurrent=10)

        # 7. Serial agents (如未来的 AutoFix)
        for agent in serial_agents:
            for ctx in contexts:
                results.append(await agent.review(ctx))

        # 8. 三阶段管线: 验证 + 过滤 (参考 security-review.ts)
        findings = self.dedup.merge(results)
        # 只验证 severity >= warning 的 findings (控制成本)
        to_verify = [f for f in findings if f.severity in ("critical", "warning")]
        skip_verify = [f for f in findings if f.severity not in ("critical", "warning")]
        verified = await self.verify_findings(to_verify, contexts)
        findings = verified + skip_verify
        findings = [f for f in findings if f.confidence >= request.config.threshold]

        # 9. Build report
        return self.report_builder.build(findings, request)
```

### 5.4 去重与合并 (`src/cr_agent/core/dedup.py`)

```python
class FindingDeduplicator:
    """
    多 Agent 产出的 findings 去重合并策略:
    
    问题: 4 个 Agent 审查同一段代码, 可能产出重叠 findings:
    - Security Agent: "未验证的 SQL 拼接 → SQL 注入"
    - Logic Agent: "用户输入未校验直接使用"
    这两条本质是同一个问题, 需合并而非重复报告。
    
    合并策略 (三级):
    1. 精确去重: 相同 file + line_range 重叠 + 相同 category → 合并
    2. 语义去重: 相同 file + line_range 重叠 + 不同 category → 保留高优先级
       优先级: security > logic > performance > style
    3. 相邻合并: 同 file + line 差 ≤ 3 + 同 title 关键词 → 合并为一条
    """
    
    def merge(self, results: list[ReviewOutput]) -> list[ReviewFinding]:
        all_findings = []
        for r in results:
            all_findings.extend(r.findings)
        
        # Step 1: 精确去重
        unique = self._exact_dedup(all_findings)
        
        # Step 2: 语义去重 (line range 重叠 + 不同维度)
        merged = self._semantic_dedup(unique)
        
        # Step 3: 相邻合并
        final = self._adjacent_merge(merged)
        
        return final

    def _semantic_dedup(self, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        """同一行范围的多个 findings, 保留最高优先级的那个, 其他合入描述"""
        PRIORITY = {"security": 1, "logic": 2, "performance": 3, "style": 4}
        
        # 按 (file, line_range) 分组
        groups = defaultdict(list)
        for f in findings:
            key = (f.file, f.line_start, f.line_end)
            groups[key].append(f)
        
        result = []
        for key, group in groups.items():
            if len(group) == 1:
                result.append(group[0])
            else:
                # 保留优先级最高的, confidence 取最大值
                group.sort(key=lambda x: PRIORITY.get(x.category, 99))
                primary = group[0]
                primary.confidence = max(f.confidence for f in group)
                # 补充其他维度的信息到 description
                others = [f"[{f.category}] {f.title}" for f in group[1:]]
                primary.description += f"\n\nAlso flagged by: {'; '.join(others)}"
                result.append(primary)
        
        return result
```

### 5.5 多语言适配 (`src/cr_agent/core/language.py`)

```python
class LanguageAdapter:
    """
    多语言支持 — 不同语言的审查策略和 prompt 差异化
    
    设计原则:
    - Agent 的 system prompt 是通用的 (审查方法论)
    - 语言相关的知识通过 LanguageProfile 注入 user prompt
    - 新增语言只需添加一个 profile, 不改 agent 代码
    """
    
    PROFILES: dict[str, "LanguageProfile"] = {
        "python": LanguageProfile(
            name="Python",
            ast_parser="tree-sitter-python",
            security_focus=[
                "pickle 反序列化", "eval/exec 注入", "SQL 拼接 (非 ORM)",
                "os.system/subprocess 命令注入", "SSRF via requests/urllib",
            ],
            performance_focus=[
                "N+1 查询 (Django/SQLAlchemy)", "同步阻塞在 async 函数中",
                "列表推导内的重复计算",
            ],
            ignore_patterns=[
                "type: ignore 注释是合理的类型标注覆盖",
            ],
        ),
        "typescript": LanguageProfile(
            name="TypeScript",
            ast_parser="tree-sitter-typescript",
            security_focus=[
                "dangerouslySetInnerHTML (XSS)", "eval() 使用",
                "prototype pollution", "不安全的 any 类型断言",
            ],
            performance_focus=[
                "useEffect 缺少依赖导致无限循环",
                "大数组在 render 中重复创建",
                "未 memo 的昂贵计算",
            ],
            ignore_patterns=[
                "React/Angular 框架本身已防 XSS, 无需额外转义",
                "@ts-ignore 有时是必要的类型绕过",
            ],
        ),
        "go": LanguageProfile(
            name="Go",
            ast_parser="tree-sitter-go",
            security_focus=[
                "SQL 拼接 (非 prepared statement)", "命令注入 via exec.Command",
                "不安全的 TLS 配置 (InsecureSkipVerify)",
            ],
            performance_focus=[
                "goroutine 泄漏 (未关闭 channel)", "defer 在循环中",
                "大 struct 值传递 (应用指针)",
            ],
            ignore_patterns=[
                "Go 是内存安全语言, 不需要报缓冲区溢出",
                "err != nil 模式是惯用写法, 不算冗余",
            ],
        ),
        "rust": LanguageProfile(
            name="Rust",
            ast_parser="tree-sitter-rust",
            security_focus=[
                "unsafe 块的合理性", "未检查的 unwrap()",
                "不安全的 FFI 边界",
            ],
            performance_focus=[
                "不必要的 clone()", "Box<dyn Trait> vs 泛型",
                "过度使用 Arc<Mutex<>>",
            ],
            ignore_patterns=[
                "Rust 的所有权系统已保证内存安全, 不报内存类漏洞",
                "Rust 的类型系统已防空指针, 不报 null deref",
            ],
        ),
    }

    def detect_language(self, file_path: str) -> str:
        """根据文件扩展名检测语言"""
        ext_map = {
            ".py": "python", ".ts": "typescript", ".tsx": "typescript",
            ".js": "javascript", ".go": "go", ".rs": "rust",
            ".java": "java", ".kt": "kotlin", ".rb": "ruby",
        }
        ext = Path(file_path).suffix.lower()
        return ext_map.get(ext, "unknown")

    def get_prompt_injection(self, language: str, agent_category: str) -> str:
        """为特定语言 + agent 类型生成额外的 prompt 段落"""
        profile = self.PROFILES.get(language)
        if not profile:
            return ""
        
        sections = []
        if agent_category == "security":
            sections.append(f"## {profile.name} 安全审查重点\n" + 
                          "\n".join(f"- {f}" for f in profile.security_focus))
        elif agent_category == "performance":
            sections.append(f"## {profile.name} 性能审查重点\n" + 
                          "\n".join(f"- {f}" for f in profile.performance_focus))
        
        if profile.ignore_patterns:
            sections.append(f"## {profile.name} 已知豁免\n" + 
                          "\n".join(f"- {f}" for f in profile.ignore_patterns))
        
        return "\n\n".join(sections)
```
```

### 5.6 Context Builder (`src/cr_agent/core/context_builder.py`)

```python
class ContextBuilder:
    """
    构建 LLM 输入上下文 — 与 demo 级项目的核心差异:
    不是把 raw diff 扔给 LLM, 而是组装完整的审查上下文。
    
    内部使用 ContextBudgetManager (见 4.3 节) 管理 token 预算。
    
    构建策略:
    1. 完整文件内容 (不仅是 diff hunk, LLM 需要看上下文)
    2. 相关类型定义 (import 的 interface/type, 函数签名)
    3. 被修改函数的调用方 (影响范围)
    4. 项目配置 (.cr-agent.yml 中的自定义规则)
    5. 语言适配 (通过 LanguageAdapter 注入, 见 5.5 节)
    """

    async def build(self, diffs: list[FileDiff], repo: RepoInfo) -> list[ReviewContext]:
        for diff in diffs:
            full_file = await self.git.get_file(repo, diff.path, diff.head_sha)
            changed_functions = self.ast_extractor.extract_changed(full_file, diff.hunks)
            related_types = await self.find_related_types(changed_functions, repo)
            
            # 语言感知的额外 context
            lang = self.language_adapter.detect_language(diff.path)
            
            context = self.budget_manager.pack([
                ContextItem(priority=1, content=diff.hunks, token_count=...),
                ContextItem(priority=2, content=changed_functions, token_count=...),
                ContextItem(priority=3, content=related_types, token_count=...),
            ])
            yield ReviewContext(diff=diff, packed_context=context, language=lang)
```

> **StreamingReviewExecutor (4.6 节) 与 ReviewOrchestrator (5.3 节) 的关系**：
> Orchestrator 是入口——负责解析、分片、调度、合并的完整流程。
> StreamingExecutor 是 Orchestrator 内部的执行引擎——负责并行调度 agent 并流式输出中间结果。
> 即：`orchestrator.run()` 内部调用 `streaming_executor.execute(agents, ctx)` 做并行执行。

---

## 6. API 设计

### 6.1 核心端点

```yaml
# Webhook 接收
POST /api/v1/webhook/github
  - 验证 X-Hub-Signature-256
  - 处理 pull_request.opened / synchronize 事件
  - 异步入队, 立即返回 202

# 手动提交审查
POST /api/v1/reviews
  Request:
    repo_url: str
    pr_number: int | None
    diff_content: str | None  # 二选一: PR号 或 直接传 diff
    config_override: dict     # 覆盖默认配置
  Response: 202
    review_id: uuid
    status_url: /api/v1/reviews/{id}

# 查询审查状态/结果
GET /api/v1/reviews/{review_id}
  Response:
    status: "queued" | "running" | "completed" | "failed"
    progress: { total_agents: 4, completed: 2 }
    report: ReviewReport | null
    cost: { tokens_in: 15000, tokens_out: 3000, estimated_usd: 0.012 }

# 审查报告详情
GET /api/v1/reviews/{review_id}/findings
  Query: severity=critical&category=security
  Response: paginated list of findings

# 健康检查
GET /health          # liveness
GET /health/ready    # readiness (DB + LLM 连通性)
```

### 6.2 认证方式

```
Authorization: Bearer <api_key>

API Key 分级:
- free:  10 reviews/day, 单文件 < 500 行
- pro:   100 reviews/day, 无限制
- team:  自定义 quota + webhook 集成
```

---

## 7. 数据库设计

```sql
-- 用户/租户
CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(255) NOT NULL,
    api_key     VARCHAR(64) UNIQUE NOT NULL,  -- sha256 hash
    plan        VARCHAR(20) DEFAULT 'free',
    quota_daily INT DEFAULT 10,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 审查任务
CREATE TABLE reviews (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID REFERENCES tenants(id),
    repo_url    VARCHAR(512),
    pr_number   INT,
    head_sha    VARCHAR(40),                    -- 用于幂等 + 回写定位
    status      VARCHAR(20) DEFAULT 'queued',   -- queued/running/completed/failed/cancelled
    config      JSONB,                          -- 审查配置
    started_at  TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error       TEXT,
    retry_count INT DEFAULT 0,                  -- 孤儿重试计数
    -- 成本追踪
    tokens_in   INT DEFAULT 0,
    tokens_out  INT DEFAULT 0,
    cost_usd    DECIMAL(10, 6) DEFAULT 0,
    -- GitHub 回写追踪
    github_review_id  BIGINT,                   -- GitHub PR Review ID (用于更新/删除)
    github_check_run_id BIGINT,                 -- Check Run ID
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 审查发现
CREATE TABLE findings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   UUID REFERENCES reviews(id) ON DELETE CASCADE,
    file_path   VARCHAR(512),
    line_start  INT,
    line_end    INT,
    severity    VARCHAR(20),  -- critical/warning/suggestion/nitpick
    category    VARCHAR(20),  -- security/logic/performance/style
    title       VARCHAR(255),
    description TEXT,
    suggestion  TEXT,
    confidence  DECIMAL(3, 2),
    -- 用户反馈 (评估闭环)
    user_vote   VARCHAR(10),  -- helpful/not_helpful/false_positive
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_reviews_tenant ON reviews(tenant_id, created_at DESC);
CREATE INDEX idx_reviews_status ON reviews(status) WHERE status IN ('queued', 'running');
CREATE INDEX idx_findings_review ON findings(review_id);
CREATE INDEX idx_findings_severity ON findings(review_id, severity);
```

---

## 8. 可观测性设计

### 8.1 Metrics (Prometheus)

```python
# 关键指标
review_requests_total          # counter: 总请求数 (by status, plan)
review_duration_seconds        # histogram: 端到端审查耗时
review_findings_total          # counter: 发现数 (by severity, category)
llm_request_duration_seconds   # histogram: LLM 调用耗时 (by model, agent_type)
llm_tokens_total               # counter: token 消耗 (by direction, model)
llm_errors_total               # counter: LLM 错误 (by error_type)
context_tokens_used            # histogram: context 使用率 (占预算百分比)
```

### 8.2 Structured Logging

```json
{
  "timestamp": "2025-06-09T10:30:00Z",
  "level": "info",
  "event": "review.agent.completed",
  "review_id": "uuid",
  "agent_type": "security",
  "duration_ms": 3200,
  "findings_count": 3,
  "tokens_in": 4500,
  "tokens_out": 800,
  "request_id": "req-xxx"
}
```

### 8.3 告警规则

```yaml
# LLM 错误率 > 5% 持续 5 分钟
- alert: LLMErrorRateHigh
  expr: rate(llm_errors_total[5m]) / rate(llm_request_duration_seconds_count[5m]) > 0.05

# 审查队列积压 > 50
- alert: ReviewQueueBacklog
  expr: review_queue_depth > 50

# 单次审查耗时 > 5 分钟 (P95)
- alert: ReviewLatencyHigh
  expr: histogram_quantile(0.95, review_duration_seconds_bucket) > 300
```

---

## 9. 降级策略

| 故障场景 | 降级方案 |
|---|---|
| DeepSeek API 不可用 | Fallback → 备用 provider (统一 LLM Client 抽象, 非 OpenAI SDK 依赖) |
| 所有 LLM 不可用 | StaticRuleAgent 兜底 (见下方说明) |
| 单个 Agent 超时 | 跳过该维度, 报告中标注"安全审查未完成" |
| Context 过长 (>64K) | 触发分片策略 (见 §9.1) |
| 数据库不可用 | 写入本地文件队列, 恢复后回放 |
| GitHub API 限流 | 指数退避 + 缓存已获取的文件内容 |

**StaticRuleAgent 架构定位**：
不是独立的降级代码路径，而是一个特殊的 Agent——metadata 中 `model_preference = None`，`review()` 方法内部走 regex + AST 模式匹配（不调 LLM）。这样它与 LLM Agent 走同一个编排流程，产出同样的 `ReviewOutput`，下游 dedup/report 不需要特殊处理。

### 9.1 分片策略

```
┌──────────────────────────────────────────────────────────┐
│                    分片决策树                              │
│                                                          │
│  PR 总 diff tokens ≤ context 预算 (58K)?                 │
│      │                                                    │
│      ├─ YES → 不分片, 每个 Agent 看全部文件               │
│      │         (中小 PR 的常见情况, 4 次 LLM 调用)        │
│      │                                                    │
│      └─ NO → 按模块/目录分片                              │
│              每片 ≤ 预算                                   │
│              每个 Agent × 每片 = N×4 次调用                │
│              跨片关联: 用 summary 传递上下文               │
└──────────────────────────────────────────────────────────┘
```

**设计决策**：

| 维度 | 决策 | 理由 |
|---|---|---|
| 默认不分片 | PR ≤ 30 文件且 tokens ≤ 58K 时, 全量给每个 Agent | 避免跨片遗漏问题, 中等 PR 足够装下 |
| 分片粒度 | 按目录/模块 (不按单文件) | 同目录的文件往往有关联 (import 关系), 按文件分会割裂上下文 |
| 跨片关联 | 第一轮各片独立审查, 第二轮用"其他片的 findings 摘要"做交叉检查 | 兼顾成本和跨片问题发现 |
| 分片上限 | 单片 ≤ 5 个文件 or ≤ 50K tokens (先触达者为准) | 留余量给 system prompt + output |
| GitHub API 限流 | 指数退避 + 缓存已获取的文件内容 |

---

## 10. 任务队列与状态机

### 10.1 Review 生命周期状态机

```
                    ┌──────────────────────────────────────────────┐
                    │                                              │
                    ▼                                              │
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌───────────┐       │
│ queued  │───▶│ running │───▶│completed│    │  failed   │       │
└─────────┘    └────┬────┘    └─────────┘    └───────────┘       │
                    │                              ▲               │
                    ├──── timeout (5min) ──────────┤               │
                    ├──── LLM 全部失败 ────────────┤               │
                    │                              │               │
                    │         ┌───────────┐        │               │
                    └────────▶│ cancelled │        │               │
                              └───────────┘        │               │
                                                   │               │
               Worker 进程挂了 (孤儿任务) ──────────┘               │
               健康检查发现 running > 10min ────────────────────────┘
                              (重新入队)
```

**状态转移规则**：

| 当前状态 | 事件 | 目标状态 | 执行者 |
|---|---|---|---|
| queued | Worker 取到任务 | running | Worker (原子 CAS) |
| running | 所有 Agent 完成 | completed | Worker |
| running | 超时 (5min) | failed | arq timeout handler |
| running | LLM 全部不可用 + 静态规则也失败 | failed | Worker |
| running | 用户主动取消 | cancelled | API (设 abort flag) |
| running | Worker 进程崩溃 (孤儿) | queued (重新入队) | 定时清理 Job |
| queued | 超过 30min 无人消费 | failed | 定时清理 Job |

**孤儿任务清理**：
```python
# 每 60s 执行一次
async def cleanup_orphan_tasks():
    """
    Worker 进程可能中途被 OOM Kill 或 SIGKILL,
    留下 status=running 但无人处理的任务。
    策略: running 超过 10 分钟 → 重新入队 (最多重试 2 次)
    """
    orphans = await db.query(
        "SELECT id, retry_count FROM reviews "
        "WHERE status = 'running' AND started_at < NOW() - INTERVAL '10 min'"
    )
    for task in orphans:
        if task.retry_count >= 2:
            await db.update(task.id, status="failed", error="orphan_timeout")
        else:
            await db.update(task.id, status="queued", retry_count=task.retry_count + 1)
            await queue.enqueue(task.id)
```

### 10.2 任务队列

```python
# 基于 arq (async Redis queue) 的任务管理

class ReviewTaskQueue:
    """
    关键设计点 (demo 级项目不会有的):
    
    1. 优先级队列:
       - team plan → priority 1 (插队)
       - pro plan  → priority 5
       - free plan → priority 10
       
    2. 幂等性保证:
       - 每个 (repo_url, pr_number, head_sha) 组合有唯一 idempotency_key
       - webhook 重复投递时, 后到的请求直接返回已有 review_id
       
    3. 超时 + 死信:
       - 单任务超时: 5 分钟 (hard kill)
       - 失败重试: 最多 2 次, 指数退避
       - 3 次失败 → 进入 dead letter queue, 标记 review 为 failed
       
    4. 并发控制:
       - 全局最大并发: 20 (防止 LLM API 被打爆)
       - 单租户最大并发: 5 (防止大用户饿死小用户)
    """
    
    async def enqueue(self, request: ReviewRequest) -> str:
        # 幂等性检查
        idempotency_key = f"{request.repo_url}:{request.pr_number}:{request.head_sha}"
        existing = await self.redis.get(f"idem:{idempotency_key}")
        if existing:
            return existing  # 返回已有 review_id
        
        # 确定优先级
        priority = self._get_priority(request.tenant_plan)
        
        # 入队
        review_id = str(uuid4())
        await self.redis.set(f"idem:{idempotency_key}", review_id, ex=3600)
        await self.arq_pool.enqueue_job(
            "run_review", request, _job_id=review_id,
            _queue_name=f"priority_{priority}",
            _job_try=0, _timeout=300,  # 5分钟超时
        )
        return review_id
```

---

## 11. 评估体系

### 11.1 离线评估

```python
# tests/eval/dataset/ 结构
{
    "pr_id": "pytorch/pytorch#12345",
    "diff": "...",
    "human_findings": [
        {
            "file": "torch/nn/modules/linear.py",
            "line": 42,
            "severity": "warning",
            "category": "logic",
            "description": "Missing null check for weight parameter"
        }
    ],
    "metadata": {
        "language": "python",
        "diff_size": "medium",  # small(<100行) / medium / large(>1000行)
        "annotator": "senior_engineer"
    }
}
```

### 11.2 评估指标

```
Precision = 正确发现 / Agent总发现     (误报率的反面)
Recall    = 正确发现 / 人工总发现     (漏报率的反面)
F1        = 2 * P * R / (P + R)

按维度拆分:
- security_precision, security_recall
- logic_precision, logic_recall
- performance_precision, performance_recall

额外指标:
- 定位准确率: Agent 指出的行号 ± 3 行内有真实问题
- 严重等级准确率: Agent 标的 severity 与人工标注一致
- 平均审查耗时 / 平均 cost per review
```

### 11.3 在线评估 (用户反馈闭环)

```
用户对每条 finding 投票: helpful / not_helpful / false_positive
                              │
                              ▼
              定期统计 helpful_rate by (category, severity)
                              │
                              ▼
              helpful_rate < 60% 的 prompt → 自动标记需优化
```

### 11.4 项目级审查学习 (Learned Rules)

> 借鉴 Claude Code 的 Persistent Memory 机制：从用户行为中自动提取规则，跨审查持久生效。
> 但不照搬其三层记忆架构 — CR-Agent 是无状态一次性任务，不需要 session memory 和 compact。

#### 核心数据流

```
用户投票 (helpful / not_helpful / false_positive)
        │
        ▼
┌─────────────────────────────┐
│  Rule Extraction Job        │  定期 (每 50 条投票) 或手动触发
│  (后台, 不阻塞审查)         │
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│  Pattern Mining             │  从投票中提取规律:
│                             │  - 哪些 category 在这个项目总是被标 false_positive?
│                             │  - 哪些 file pattern 的 finding 总是被否定?
│                             │  - 哪些 agent 在这个项目表现差?
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│  Learned Rules Store        │  持久化到 DB (per-tenant per-repo)
│                             │  格式: 结构化 JSON, 有置信度和来源
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│  下次审查时                  │  learned rules 注入各 Agent 的 prompt
│  Prompt Injection           │  作为 "项目特定的硬排除/加强规则"
└─────────────────────────────┘
```

#### 数据模型

```python
class LearnedRule(BaseModel):
    """从用户反馈中学习到的项目级审查规则"""
    id: str
    tenant_id: str
    repo_url: str
    
    # 规则内容
    rule_type: Literal[
        "suppress",     # 抑制: 不要再报这类问题
        "emphasize",    # 加强: 这类问题很重要, 要更严格
        "context",      # 上下文: 这个项目的特殊背景
    ]
    category: Literal["security", "logic", "performance", "style"] | None
    
    # 规则描述 (自然语言, 直接注入 prompt)
    description: str
    # 例:
    # "这个项目使用 Django ORM, 所有数据库查询都经过 ORM, 不需要报 SQL 注入"
    # "这个项目的 console.log 是故意保留的调试日志, 不要报 style 问题"
    # "这个团队非常重视性能, N+1 查询问题请标为 critical 而非 warning"
    
    # 匹配条件 (可选, 用于精确匹配)
    file_pattern: str | None = None     # e.g. "src/legacy/**"
    keyword_pattern: str | None = None  # e.g. "console.log"
    
    # 元数据
    confidence: float         # 0.0 ~ 1.0, 基于投票统计
    source_votes: int         # 这条规则基于多少条投票推导
    created_at: datetime
    expires_at: datetime | None  # 可选过期时间 (避免永远生效的错误规则)
    
    # 审计
    is_active: bool = True
    deactivated_reason: str | None = None
```

```sql
-- DB Schema
CREATE TABLE learned_rules (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID REFERENCES tenants(id),
    repo_url    VARCHAR(512) NOT NULL,
    rule_type   VARCHAR(20) NOT NULL,  -- suppress/emphasize/context
    category    VARCHAR(20),
    description TEXT NOT NULL,
    file_pattern VARCHAR(255),
    keyword_pattern VARCHAR(255),
    confidence  DECIMAL(3, 2) NOT NULL,
    source_votes INT NOT NULL,
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ,
    
    CONSTRAINT valid_confidence CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX idx_learned_rules_repo ON learned_rules(tenant_id, repo_url) 
    WHERE is_active = true;
```

#### 规则提取算法

```python
class RuleExtractor:
    """从用户投票中挖掘审查规则"""
    
    MIN_VOTES_FOR_RULE = 5       # 至少 5 条同类投票才生成规则
    MIN_CONFIDENCE = 0.75        # 75% 一致性才算有效规则
    
    async def extract(self, tenant_id: str, repo_url: str) -> list[LearnedRule]:
        """定期执行, 从累积投票中提取模式"""
        votes = await self.db.get_votes(tenant_id, repo_url, since=self.last_run)
        
        rules = []
        
        # Pattern 1: 按 category 聚合 — "这个项目的安全问题总被标 false_positive"
        rules += self._mine_category_patterns(votes)
        
        # Pattern 2: 按 file pattern 聚合 — "vendor/ 下的问题总被标 not_helpful"  
        rules += self._mine_file_patterns(votes)
        
        # Pattern 3: 按关键词聚合 — "console.log 的 finding 总被否定"
        rules += self._mine_keyword_patterns(votes)
        
        # 去重: 新规则 vs 已有规则
        rules = self._deduplicate(rules)
        
        return rules

    def _mine_category_patterns(self, votes: list[Vote]) -> list[LearnedRule]:
        """挖掘 category 级别的规律"""
        # 按 (category, severity) 分组
        groups = defaultdict(list)
        for v in votes:
            groups[(v.finding_category, v.finding_severity)].append(v)
        
        rules = []
        for (cat, sev), group_votes in groups.items():
            if len(group_votes) < self.MIN_VOTES_FOR_RULE:
                continue
            
            false_positive_rate = sum(
                1 for v in group_votes if v.vote == "false_positive"
            ) / len(group_votes)
            
            if false_positive_rate >= self.MIN_CONFIDENCE:
                rules.append(LearnedRule(
                    rule_type="suppress",
                    category=cat,
                    description=f"This project's {cat} findings at {sev} level "
                               f"have {false_positive_rate:.0%} false positive rate. "
                               f"Be more conservative with {cat}/{sev} reports.",
                    confidence=false_positive_rate,
                    source_votes=len(group_votes),
                ))
            
            helpful_rate = sum(
                1 for v in group_votes if v.vote == "helpful"
            ) / len(group_votes)
            
            if helpful_rate >= self.MIN_CONFIDENCE:
                rules.append(LearnedRule(
                    rule_type="emphasize",
                    category=cat,
                    description=f"This project values {cat} findings at {sev} level "
                               f"({helpful_rate:.0%} helpful rate). "
                               f"Be thorough in {cat} analysis.",
                    confidence=helpful_rate,
                    source_votes=len(group_votes),
                ))
        
        return rules
```

#### Prompt 注入方式

```python
class LearnedRulesInjector:
    """将 learned rules 注入 Agent 的 prompt"""
    
    MAX_RULES_PER_AGENT = 10         # 每个 agent 最多注入 10 条规则
    MAX_RULES_TOKEN_BUDGET = 1500    # 规则总共不超过 1500 tokens
    
    async def build_rules_section(
        self, tenant_id: str, repo_url: str, agent_category: str
    ) -> str:
        """构建注入 prompt 的规则段落"""
        rules = await self.db.get_active_rules(tenant_id, repo_url)
        
        # 过滤: 只选与当前 agent 相关的规则
        relevant = [
            r for r in rules 
            if r.category is None or r.category == agent_category
        ]
        
        # 按 confidence 排序, 取 top N
        relevant.sort(key=lambda r: r.confidence, reverse=True)
        relevant = relevant[:self.MAX_RULES_PER_AGENT]
        
        if not relevant:
            return ""
        
        # 生成 prompt 段落
        lines = ["## Project-Specific Rules (Learned from feedback)\n"]
        for r in relevant:
            prefix = {"suppress": "⛔ SKIP", "emphasize": "⚠️ FOCUS", "context": "ℹ️ NOTE"}
            lines.append(f"- {prefix[r.rule_type]}: {r.description}")
            if r.file_pattern:
                lines.append(f"  (applies to: {r.file_pattern})")
        
        return "\n".join(lines)
```

```python
# 在 BaseReviewAgent.review() 中注入:
async def review(self, context: ReviewContext) -> ReviewOutput:
    # 获取项目级学习规则
    learned_rules = await self.rules_injector.build_rules_section(
        context.tenant_id, context.repo_url, self.metadata.category
    )
    
    messages = [
        {"role": "system", "content": self.system_prompt() + "\n\n" + learned_rules},
        {"role": "user", "content": self.build_user_prompt(context)},
    ]
    return await self.llm.chat_structured(messages, schema=ReviewOutput)
```

#### 安全保障

| 风险 | 防护 |
|---|---|
| 错误规则永远生效 | 规则有 `expires_at`, 默认 30 天后过期需重新验证 |
| 规则太多占 token | 每个 agent 最多 10 条规则, 总计 ≤ 1500 tokens |
| 用户恶意投票毒化规则 | `MIN_VOTES_FOR_RULE = 5` + `MIN_CONFIDENCE = 0.75` 双门槛 |
| suppress 规则导致漏报 | suppress 只降低严重等级而非完全跳过; critical 永远不被 suppress |
| 规则冲突 | emphasize > suppress; 精确匹配(file_pattern) > 泛匹配 |

#### 面试亮点

这个设计让你能说：

> "我的审查系统不是静态的。它从用户反馈中自动学习项目级别的审查偏好 — 如果一个项目的 React 组件从不关心 XSS（因为框架已经保护了），系统会自动学到这一点，下次不再报。这让 precision 随使用时间持续提升。类似推荐系统的冷启动 → 个性化的过程。"

---

## 12. 配置系统 (.cr-agent.yml)

用户在 repo 根目录放置配置文件：

```yaml
# .cr-agent.yml — 项目级审查配置
version: 1

# 审查维度开关
agents:
  security: true
  logic: true
  performance: true
  style: false       # 该项目不做风格审查

# 过滤规则
filters:
  exclude_paths:
    - "vendor/**"
    - "*.generated.go"
    - "**/*.test.ts"
  exclude_extensions: [".lock", ".sum"]
  max_file_size: 1000  # 超过 1000 行的文件跳过

# 输出控制
output:
  min_severity: warning   # 不报 nitpick 和 suggestion
  min_confidence: 0.7     # 置信度阈值
  max_findings: 20        # 单次最多报告条数

# 自定义规则 (高级)
custom_rules:
  - name: "no-console-log"
    pattern: "console\\.log"
    severity: warning
    message: "Remove console.log before merging"
```

---

## 13. 安全设计

| 层面 | 措施 |
|---|---|
| API 认证 | API Key (SHA256 存储) + JWT for Web UI |
| Webhook 验证 | GitHub: HMAC-SHA256 签名验证 |
| 代码隔离 | 不执行用户代码, 纯静态分析 + LLM 推理 |
| 数据隔离 | 租户间数据完全隔离 (tenant_id FK) |
| 敏感信息 | diff 中检测到的 secret 不写入 finding.suggestion |
| 速率限制 | 按 API Key 令牌桶 + 全局并发控制 |
| 输入校验 | diff 大小上限 5MB, 文件数上限 200 |

---

## 14. 部署架构

```
┌─────────────────────────────────────────────┐
│              Docker Compose (开发)            │
├─────────────────────────────────────────────┤
│  app (FastAPI + Uvicorn, 4 workers)         │
│  postgres:16                                │
│  redis:7 (任务队列 + 缓存)                   │
│  prometheus + grafana (监控)                 │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│              Production (K8s)                │
├─────────────────────────────────────────────┤
│  Deployment: cr-agent-api (HPA: 2~10 pods)  │
│  Deployment: cr-agent-worker (HPA: 2~20)    │
│  StatefulSet: PostgreSQL (或 RDS)            │
│  Deployment: Redis                          │
│  Ingress: nginx + TLS                       │
└─────────────────────────────────────────────┘
```

---

## 15. 实现路线图

### Phase 1: 核心可用 (Week 1-2)
- [ ] 项目骨架搭建 (FastAPI + 目录结构)
- [ ] LLM Client (DeepSeek 调用 + retry + structured output)
- [ ] Diff Parser (unified diff → FileDiff)
- [ ] Security Agent + Logic Agent (2 个 agent 先跑通)
- [ ] 编排器 (串行版本, 先不并行)
- [ ] 最简 API: POST /reviews + GET /reviews/{id}
- [ ] 本地 CLI 可跑: `cr-agent review --diff xxx.patch`

### Phase 2: 生产化 (Week 3-4)
- [ ] 并行执行 + 并发控制
- [ ] Performance Agent + Style Agent
- [ ] Context Builder (完整文件 + 类型推断)
- [ ] PostgreSQL 持久化
- [ ] GitHub Webhook 集成
- [ ] API Key 认证 + 速率限制
- [ ] Docker Compose 一键启动

### Phase 3: 可观测 + 评估 + 学习 (Week 5-6)
- [ ] Prometheus metrics + Grafana dashboard
- [ ] Structured logging
- [ ] 评估数据集构建 (标注 20+ PR)
- [ ] 评估 pipeline: precision/recall/F1
- [ ] 用户反馈机制 (finding 投票)
- [ ] Learned Rules: 规则提取 + DB 存储 + prompt 注入
- [ ] 降级策略实现

### Phase 4: 打磨 (Week 7-8)
- [ ] Web Dashboard (报告可视化)
- [ ] .cr-agent.yml 配置系统
- [ ] 性能优化 (缓存 + 批量推理)
- [ ] 安全加固
- [ ] 文档 + README + Demo 录屏
- [ ] 开源发布

---

## 16. 技术栈总览

| 层 | 选型 | 理由 |
|---|---|---|
| Web 框架 | FastAPI 0.115+ | Async 原生, 自动 OpenAPI 文档 |
| 任务队列 | Redis + arq | 轻量, 比 Celery 简单, async 原生 |
| 数据库 | PostgreSQL 16 | JSONB 存配置, 事务可靠 |
| ORM | SQLAlchemy 2.0 (async) | 类型安全, 迁移用 Alembic |
| LLM 调用 | httpx (provider-agnostic) | 统一接口适配 DeepSeek/OpenAI/本地, 不绑定单一 SDK |
| 输出约束 | Pydantic v2 | JSON Schema 生成 + 校验 |
| 模板引擎 | Jinja2 | Prompt 模板, 条件渲染 |
| 测试 | pytest + pytest-asyncio | 标准选择 |
| 容器化 | Docker + docker-compose | 本地 + CI + 生产统一 |
| 监控 | Prometheus + Grafana | 业界标准 |
| 日志 | structlog | 结构化 JSON, 可对接任何日志平台 |
| 包管理 | uv | 比 pip/poetry 快 10x, 2025 新标准 |

---

## 17. 成本估算 (DeepSeek)

```
单次审查 (中等 PR, ~300 行 diff, 4 agents 并行):

Phase 1 (发现):
- Input:  ~12K tokens × 4 agents = 48K tokens
- Output: ~2K tokens × 4 agents  = 8K tokens

Phase 2 (验证, 仅 warning+ findings, ~8 条需验证):
- Input:  ~3K tokens × 8 findings = 24K tokens (用轻量模型)
- Output: ~200 tokens × 8 = 1.6K tokens

总计:
- Input:  72K tokens
- Output: 9.6K tokens
- Cost:   72K × $0.14/M + 9.6K × $0.28/M ≈ $0.013 (~￥0.09)

日均 100 次审查: ~$1.3/天 ≈ ￥9/天

结论: 即使加了验证阶段, DeepSeek 仍极便宜, 不需要激进优化
```

---

## 18. Claude Code 源码参考索引

> 以下文件可在面试中作为"我参考了生产级系统的实际实现"的论据。

| 我们的设计 | Claude Code 源文件 | 借鉴的核心模式 |
|---|---|---|
| Agent 声明式元数据 | `src/Tool.ts` (buildTool, 元数据字段) | isConcurrencySafe / isReadOnly / isDestructive 驱动调度 |
| 分区并行执行 | `src/services/tools/toolOrchestration.ts` | partitionToolCalls: 安全→并行, 不安全→串行 |
| 流式执行器 | `src/services/tools/StreamingToolExecutor.ts` | addTool 即执行, 结果保序, 失败不阻塞 |
| Retry + Fallback | `src/services/api/withRetry.ts` | 指数退避+jitter, 连续529→切模型, 前台/后台区分 |
| Context 压缩 | `src/services/compact/autoCompact.ts` | 有效窗口公式, 连续失败熔断, buffer 预留 |
| Cost Tracking | `src/cost-tracker.ts` | 按模型分维度, 每次调用即时累加, 持久化 |
| Security Review Prompt | `src/commands/security-review.ts` | 三阶段(发现→验证→过滤), 17条硬排除, 置信度评分 |
| Agent 子任务 | `src/tools/AgentTool/runAgent.ts` | 子 agent 隔离 context, 独立 MCP, cleanup |
| 任务状态机 | `src/Task.ts` | TaskStatus 生命周期, isTerminal 判断, ID 前缀分类 |
| 项目级审查学习 | `src/services/extractMemories/` + `src/utils/claudemd.ts` | 从行为中提取持久规则, 下次自动注入; 多层优先级; 去重+过期 |

### 面试话术参考

当面试官问"你这个设计有什么依据"时：

> "我分析了 Claude Code (Anthropic 的 CLI 工具) 的生产源码。比如 retry 机制不是简单的 3 次重试，而是区分了 429 (限流, 等 retry-after) 和 529 (过载, 连续 3 次切模型)，前台请求重试但后台任务直接丢弃避免雪崩。Tool 编排根据声明的 isConcurrencySafe 自动分区——安全的并行跑，不安全的串行。我把这些模式直接移植到了 Python 的 agent 编排层。"
