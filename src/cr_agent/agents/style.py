"""
Style review agent — detects readability, naming, and maintainability issues.
"""

from .base import AgentMetadata, BaseReviewAgent, ReviewContext

STYLE_SYSTEM_PROMPT = """\
你是一名代码整洁度审查专家，审查新增代码的可维护性。只报告显著的、会实际影响团队协作的问题，不报告微小偏好和 nitpick。

## 审查范围（仅限以下）

**可读性：**
- 新增函数超过 50 行且逻辑混杂
- 魔法数字（硬编码数值未定义常量），且该数值出现 3 次以上

**重复代码：**
- 新增代码中有明显的复制粘贴（5 行以上重复）

## 硬排除规则（严格遵守）

1. 测试文件、配置文件、SQL 脚本 → 不报
2. 命名偏好（"变量名不够好"、"建议改名"）→ 不报
3. 缺少注释/文档 → 不报
4. 函数参数数量 → 不报（由团队规范决定）
5. 嵌套深度、复杂条件 → 不报（安全/逻辑 Agent 会看）
6. getter/setter/Builder 模式 → 不报
7. 5 行以内的小改动 → 不报
8. 仅修改已有代码格式 → 不报
9. TODO/FIXME 注释 → 不报

## 严重程度：

- 仅使用 **warning** 和 **suggestion**
- warning：明显影响团队协作的问题
- suggestion：有改进空间，仅在新增大段代码时报告

## 输出规则：

- 整次审查最多 2 条发现
- 只有新增超过 20 行的文件才考虑报告 style 问题
- 不确定的不报
"""

STYLE_USER_PROMPT_TEMPLATE = """\
## 待审查的代码

**文件：** `{file_path}`
**语言：** {language}

```diff
{diff_content}
```
{extra_context}

## 说明

审查上方 diff 中的代码风格和可维护性问题。关注：命名、注释、函数长度、嵌套深度、重复代码、魔法数字。
注意 style 问题没有 critical 级别，如果代码功能正常只是风格不佳，标记为 suggestion 或 nitpick。
如果未发现问题，返回空 findings。
"""


class StyleAgent(BaseReviewAgent):
    """Style-focused code review agent."""

    metadata = AgentMetadata(
        name="style",
        category="style",
        concurrency_safe=True,
        max_context_tokens=10_000,
        timeout_seconds=60,
        priority=4,
        model_preference="deepseek-chat",
        temperature=0.05,
    )

    def system_prompt(self) -> str:
        return STYLE_SYSTEM_PROMPT

    def build_user_prompt(self, context: ReviewContext) -> str:
        extra = ""
        if context.extra_context:
            extra = f"\n\n## 附加上下文\n{context.extra_context}"
        return STYLE_USER_PROMPT_TEMPLATE.format(
            file_path=context.file_path,
            language=context.language,
            diff_content=context.diff_content,
            extra_context=extra,
        )
