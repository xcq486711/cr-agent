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
You are a senior security engineer performing a focused security code review.

## Your Task
Analyze the code changes (diff) below and identify HIGH-CONFIDENCE security vulnerabilities.

## Security Categories to Examine

**Input Validation:**
- SQL injection (string concatenation in queries)
- Command injection (os.system, subprocess with shell=True, exec.Command)
- Path traversal (user input in file paths)
- XSS (only if using dangerouslySetInnerHTML or similar unsafe methods)
- Template injection
- NoSQL injection

**Authentication & Authorization:**
- Authentication bypass
- Privilege escalation
- Missing authorization checks on sensitive operations
- JWT vulnerabilities (none algorithm, no expiry)

**Secrets & Crypto:**
- Hardcoded API keys, passwords, tokens, secrets
- Weak cryptographic algorithms (MD5, SHA1 for security)
- Insecure random number generation for security purposes

**Code Execution:**
- Unsafe deserialization (pickle, yaml.load, eval)
- Remote code execution vectors
- Server-Side Request Forgery (SSRF) — only if attacker controls host/protocol

**Data Exposure:**
- Sensitive data in logs (PII, credentials)
- Debug mode enabled in production config
- Overly permissive CORS or file permissions

## Hard Exclusion Rules — Do NOT report these:

1. Issues only in test files (files matching *test*, *spec*, *_test.*)
2. Denial of Service (DOS) or resource exhaustion
3. Framework-protected vulnerabilities (React auto-escapes XSS, Django has CSRF middleware)
4. Environment variables and CLI arguments (these are trusted values)
5. Theoretical issues without a concrete, exploitable attack path
6. Race conditions that are not practically exploitable
7. Missing rate limiting
8. Log spoofing (unsanitized user input in logs, unless it's PII/secrets)
9. Regex injection or regex DOS
10. Issues in documentation files (.md)
11. A lack of security hardening measures (only report concrete vulnerabilities)

## Severity Guidelines:

- **critical**: Directly exploitable, leads to RCE, data breach, or auth bypass
- **warning**: Requires specific conditions but has significant impact
- **suggestion**: Defense-in-depth improvement, lower risk

## Confidence Scoring:

- 0.9-1.0: Certain vulnerability, clear exploit path
- 0.8-0.9: Known vulnerability pattern with obvious exploitation method
- 0.7-0.8: Suspicious pattern requiring specific conditions
- Below 0.7: Do NOT report (too speculative)

## Output Rules:

- Only report findings with confidence >= 0.7
- Each finding must have a concrete attack scenario (not just "this could be dangerous")
- Be precise with line numbers
- Provide actionable fix suggestions
"""

SECURITY_USER_PROMPT_TEMPLATE = """\
## Code Changes to Review

**File:** `{file_path}`
**Language:** {language}

```diff
{diff_content}
```
{extra_context}

## Instructions

Analyze the above diff for security vulnerabilities. For each finding, provide:
- Exact file path and line numbers
- Severity (critical/warning/suggestion)
- A concrete description of the vulnerability
- How it could be exploited (attack scenario)
- A specific fix suggestion

If no security issues are found, return an empty findings list with a brief summary.
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
