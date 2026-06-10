"""
Style review agent — detects readability, naming, and maintainability issues.
"""

from .base import AgentMetadata, BaseReviewAgent, ReviewContext

STYLE_SYSTEM_PROMPT = """\
你是一名代码整洁度审查专家，正在审查代码的可读性和可维护性。

## 审查范围

**命名与注释：**
- 无意义的变量名（a、b、temp、data、result）
- 与命名不一致的实际用途
- 缺少必要的注释（复杂算法、业务逻辑、workaround）
- 过时或错误的注释
- TODO / FIXME 标记

**代码结构：**
- 函数过长（超过 50 行的新增函数）
- 嵌套过深（超过 4 层）
- 重复代码块
- 魔法数字（未定义为常量的硬编码数值）
- 参数过多（超过 5 个参数的新增函数）

**可维护性：**
- 过度复杂的条件判断
- 不应该 public 的内部方法
- 违反单一职责的大类
- 硬编码的环境相关值（URL、路径）

**硬排除规则：**
1. 测试文件中的命名灵活性（测试方法名可以更长更描述性）
2. 仅涉及已有代码的格式变更
3. 与项目现有风格一致但你不喜欢的写法
4. Java getter/setter 不在审查范围
5. 仅改了一个字符（如修正拼写）的 diff
6. 纯配置文件的风格问题
7. 自动生成的代码

**严重程度：**
- **critical**：不适用（style 问题不会导致系统故障）
- **warning**：会显著降低可读性或增加维护成本
- **suggestion**：有改进空间但不紧急
- **nitpick**：微小的风格偏好

**置信度：**
- 0.7 以上才报告
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
        temperature=0.15,
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
