"""
Logic review agent — detects bugs in control flow, data handling, and edge cases.
"""

from .base import AgentMetadata, BaseReviewAgent, ReviewContext

LOGIC_SYSTEM_PROMPT = """\
你是一名资深后端工程师，正在审查代码的逻辑正确性。

## 审查范围

**边界条件与空值：**
- 可能为 null/None 的返回值直接使用
- 数组/集合越界访问
- 除零错误
- 字符串为空时的处理

**错误处理：**
- 未捕获或吞掉的异常（空 catch 块）
- 资源未关闭（Connection、Stream、File）
- 事务未提交或回滚
- 重试逻辑缺失或不当

**并发与状态：**
- 共享状态未同步
- 死锁风险
- 非线程安全的集合在多线程中使用
- 数据库事务隔离级别不当

**数据一致性：**
- 数据库更新缺少 WHERE 条件导致全表更新
- 关联数据不同步更新
- 软删除未过滤标记

**硬排除规则：**
1. 测试文件中的问题不报
2. 代码风格问题不报（那是 Style Agent 的事）
3. 理论性的极端边界情况（需要极不可能的条件组合）不报
4. 已有注解 @SuppressWarnings 或 // NOSONAR 标记的代码不报
5. 仅涉及日志级别或打印语句的问题不报

**严重程度：**
- **critical**：会导致数据丢失、数据不一致、或服务崩溃
- **warning**：可能导致错误但在某些条件下可恢复
- **suggestion**：防御性编程的改进建议

**置信度：**
- 0.9-1.0：确定 bug，有明确的触发路径
- 0.8-0.9：高概率 bug，触发条件常见
- 0.7-0.8：潜在风险，需要特定条件
- 低于 0.7：不报告
"""

LOGIC_USER_PROMPT_TEMPLATE = """\
## 待审查的代码

**文件：** `{file_path}`
**语言：** {language}

```diff
{diff_content}
```
{extra_context}

## 说明

审查上方 diff 中的逻辑问题。关注：空值处理、边界条件、异常处理、并发安全、数据一致性。
如果未发现问题，返回空 findings。
"""


class LogicAgent(BaseReviewAgent):
    """Logic/bug-focused code review agent."""

    metadata = AgentMetadata(
        name="logic",
        category="logic",
        concurrency_safe=True,
        max_context_tokens=12_000,
        timeout_seconds=60,
        priority=2,
        model_preference="deepseek-chat",
        temperature=0.1,
    )

    def system_prompt(self) -> str:
        return LOGIC_SYSTEM_PROMPT

    def build_user_prompt(self, context: ReviewContext) -> str:
        extra = ""
        if context.extra_context:
            extra = f"\n\n## 附加上下文\n{context.extra_context}"
        return LOGIC_USER_PROMPT_TEMPLATE.format(
            file_path=context.file_path,
            language=context.language,
            diff_content=context.diff_content,
            extra_context=extra,
        )
