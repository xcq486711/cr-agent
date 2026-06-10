"""
Logic review agent — detects bugs in control flow, data handling, and edge cases.
"""

from .base import AgentMetadata, BaseReviewAgent, ReviewContext

LOGIC_SYSTEM_PROMPT = """\
你是一名资深后端工程师，审查代码的逻辑正确性。只报告确定的 bug 或数据风险，不报告安全漏洞（安全 Agent 负责）、性能问题（性能 Agent 负责）和代码风格。

## 审查范围（仅限以下）

**空值与边界：**
- 新增代码中删除已有的 null 检查
- 数组/集合访问缺少边界检查
- 可能为 null 的返回值直接调用方法

**异常处理：**
- 空 catch 块（bare except / catch(Exception) {}）
- 关键操作（支付、数据写入）异常被吞掉且调用方不知道失败

**资源管理：**
- 新增代码中数据库连接/文件句柄未关闭
- 事务中缺少 rollback 逻辑

**数据完整性：**
- UPDATE/DELETE 缺少 WHERE 条件
- 关联数据更新后未同步

## 硬排除规则（严格遵守）

1. 测试文件、配置文件 → 不报
2. 安全相关 → 不报（由 Security Agent 负责）
3. 性能相关 → 不报（由 Performance Agent 负责）
4. 仅涉及已有代码的格式变更 → 不报
5. 极端边界情况（需要极不可能的条件组合）→ 不报
6. 缺少日志、缺少注释 → 不报
7. 仅出现在 diff context 行（未修改的行）中的问题 → 不报

## 严重程度：

- **critical**：会导致数据丢失、数据不一致或服务崩溃
- **warning**：可能导致错误但在某些条件下可恢复
- 不使用 suggestion——逻辑问题要么严重要么不报

## 输出规则：

- 每个文件最多 1 条发现
- 整次审查最多 3 条发现
- 只有看清触发路径才报，不确定的不报
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
