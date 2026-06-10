"""
Security review agent — detects vulnerabilities in code changes.

Three-stage design (from Claude Code /security-review):
- Phase 1: Broad detection (allow false positives)
- Phase 2/3: Verification + filtering happens at orchestrator level

Hard exclusion rules (reduce noise):
1. Test-only files → skip
2. DOS / resource exhaustion → skip
3. Framework-protected vulnerabilities (React XSS, Django CSRF) → skip
4. Environment variables / CLI args → trusted values
5. Theoretical issues without concrete attack path → skip
"""

from .base import AgentMetadata, BaseReviewAgent, ReviewContext

SECURITY_SYSTEM_PROMPT = """\
你是一名资深安全工程师，进行专注的安全代码审查。只报告确定的安全问题，不要报告输入校验、业务逻辑或代码风格问题——这些由其他审查者负责。

## 审查范围（仅限以下）

**凭据泄露：**
- 硬编码的 API key、密码、token、密钥（最高优先级）

**注入攻击：**
- SQL 注入（字符串拼接构造查询，不含 ORM 安全方式）
- 命令注入（os.system、subprocess shell=True、Runtime.exec 拼接外部输入）
- 路径穿越（外部输入直接用于文件路径，未做任何校验）

**认证与授权：**
- 认证绕过（删除/注释掉认证中间件，且未添加替代鉴权）
- 新增端点无任何鉴权保护

**代码执行：**
- 不安全的反序列化（pickle.load、yaml.load 加载不可信数据）
- eval/exec 执行外部输入

## 硬排除规则（严格遵守）

1. 测试文件和配置文件中的问题 → 不报
2. 框架已保护的漏洞 → 不报（Spring Security/Django/React 内置防护）
3. 只有"某某属性""存在风险"而无法写出具体攻击步骤的 → 不报
4. 缺少速率限制、缺少日志、缺少加固措施 → 不报（这不是漏洞）
5. 重命名、格式调整、注释修改 → 不报
6. 不是新增的代码，只是修改已有代码 → 降低一个严重等级
7. XSS、CSRF、DOS 在服务端应用中 → 默认不报（除非明确看到不安全的渲染方式）
8. 配置文件中的 URL、路径等合理硬编码值 → 不报
9. 仅出现在 diff context 行（未修改的行）中的问题 → 不报

## 严重程度：

- **critical**：可直接远程利用，导致数据泄露、权限提升或代码执行
- **warning**：需要特定条件但影响显著
- **suggestion**：不使用——security 只有 critical 和 warning

## 置信度要求（关键）：

- 必须能为每条发现写出具体的攻击步骤，写不出的不报
- 必须确认问题出现在新增/修改行（+ 开头的行），不是原有的 context 行
- 0.9-1.0：确定漏洞，利用路径清晰
- 0.8-0.9：已知漏洞模式且符合当前上下文
- 0.7-0.8：高度可疑但需要额外条件
- 低于 0.7：不报告

## 输出规则：

- 每个文件最多报告 2 条发现
- 整次审查最多报告 5 条发现
- 优先报告 critical，警告类控制在 2 条以内
"""

SECURITY_USER_PROMPT_TEMPLATE = """\
## 待审查的代码变更

**文件：** `{file_path}`
**语言：** {language}

```diff
{diff_content}
```
{extra_context}

## 说明

分析上方的 diff，识别安全漏洞。每条发现请包含：
- 精确的文件路径和行号
- 严重程度（critical/warning/suggestion）
- 漏洞的具体描述
- 可被如何利用（攻击场景）
- 具体的修复建议

如果未发现安全问题，返回空的 findings 列表并附上简要总结。
"""


class SecurityAgent(BaseReviewAgent):
    """Security-focused code review agent."""

    metadata = AgentMetadata(
        name="security",
        category="security",
        concurrency_safe=True,
        max_context_tokens=16_000,
        timeout_seconds=90,
        priority=1,
        model_preference="deepseek-chat",
        temperature=0.1,
    )

    def system_prompt(self) -> str:
        return SECURITY_SYSTEM_PROMPT

    def build_user_prompt(self, context: ReviewContext) -> str:
        extra = ""
        if context.extra_context:
            extra = f"\n\n## Additional Context\n{context.extra_context}"

        return SECURITY_USER_PROMPT_TEMPLATE.format(
            file_path=context.file_path,
            language=context.language,
            diff_content=context.diff_content,
            extra_context=extra,
        )
