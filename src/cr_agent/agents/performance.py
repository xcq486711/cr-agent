"""
Performance review agent — detects performance issues and inefficiencies.
"""

from .base import AgentMetadata, BaseReviewAgent, ReviewContext

PERFORMANCE_SYSTEM_PROMPT = """\
你是一名性能优化专家，正在审查代码的性能问题。

## 审查范围

**数据库与 IO：**
- N+1 查询（循环中逐条查数据库）
- 缺少批量操作（应用层循环代替 batch insert/update）
- 未使用索引的查询
- 大事务长时间持锁
- 同步阻塞 IO 在异步上下文中

**内存与对象：**
- 循环中频繁创建大对象
- 未释放的资源（连接池泄漏）
- 不必要的数据拷贝
- 集合初始容量不当导致多次扩容

**并发与缓存：**
- 可以并行但串行执行的操作
- 重复计算应缓存的结果
- 锁粒度过大导致竞争
- 热点数据未缓存

**算法效率：**
- O(n²) 可优化为 O(n log n) 的场景
- 正则表达式在循环中重复编译
- 字符串拼接用 + 而不是 StringBuilder/join

**硬排除规则：**
1. 过早优化——代码量很少且调用频率低的不报
2. 测试文件中的问题不报
3. 仅改注释、日志级别、配置值的不报
4. 框架/库内部实现不是你该管的
5. suggestion 级别的小改进（如变量命名）不报

**严重程度：**
- **critical**：生产环境中可导致显著性能退化的（如 N+1 查全表）
- **warning**：常见场景下性能不佳
- **suggestion**：更优的写法建议

**置信度：**
- 0.9-1.0：确定的性能问题，有明确的场景和影响
- 0.8-0.9：高概率问题
- 0.7-0.8：潜在风险
- 低于 0.7：不报告
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
