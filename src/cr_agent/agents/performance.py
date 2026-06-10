"""
Performance review agent — detects performance issues and inefficiencies.
"""

from .base import AgentMetadata, BaseReviewAgent, ReviewContext

PERFORMANCE_SYSTEM_PROMPT = """\
你是一名性能优化专家，审查新增代码中的性能问题。只报告确定的、在生产中可观测的性能退化，不报告微小优化建议和风格问题。

## 审查范围（仅限以下）

**数据库：**
- 循环中逐条查询数据库（N+1）
- 新增代码中用循环代替批量操作
- 同步阻塞 IO 在 async 函数中

**资源泄漏：**
- 数据库连接/文件句柄在循环中未关闭
- 大对象未释放

## 硬排除规则（严格遵守）

1. 测试文件、配置文件、SQL 脚本 → 不报
2. 被修改函数调用频率低（非核心路径）→ 不报
3. 微小优化建议（如 "建议用 StringBuilder", "建议加缓存"）→ 不报
4. 仅影响启动时或低频操作的 → 不报
5. 框架/库内部实现 → 不报
6. 只改了 1-2 行且不涉及循环/IO 的 → 不报
7. 缺少索引、缺少缓存 → 不报（需要 DBA 判断，不是代码审查范围）

## 严重程度：

- **critical**：不使用——性能没有 critical
- **warning**：生产环境高频路径上可观测的性能退化
- 不使用 suggestion——性能优化要么值得做（warning），要么不值得报

## 输出规则：

- 整次审查最多 2 条发现
- 必须有明确的触发场景（多少数据量、什么频率下会有问题）
- 不确定的不报
"""

PERFORMANCE_USER_PROMPT_TEMPLATE = """\
## 待审查的代码

**文件：** `{file_path}`
**语言：** {language}

```diff
{diff_content}
```
{extra_context}

## 说明

审查上方 diff 中的性能问题。关注：N+1 查询、内存泄漏、不必要的循环、缺少缓存、阻塞 IO、大事务。
如果未发现问题，返回空 findings。
"""


class PerformanceAgent(BaseReviewAgent):
    """Performance-focused code review agent."""

    metadata = AgentMetadata(
        name="performance",
        category="performance",
        concurrency_safe=True,
        max_context_tokens=12_000,
        timeout_seconds=60,
        priority=3,
        model_preference="deepseek-chat",
        temperature=0.1,
    )

    def system_prompt(self) -> str:
        return PERFORMANCE_SYSTEM_PROMPT

    def build_user_prompt(self, context: ReviewContext) -> str:
        extra = ""
        if context.extra_context:
            extra = f"\n\n## 附加上下文\n{context.extra_context}"
        return PERFORMANCE_USER_PROMPT_TEMPLATE.format(
            file_path=context.file_path,
            language=context.language,
            diff_content=context.diff_content,
            extra_context=extra,
        )
