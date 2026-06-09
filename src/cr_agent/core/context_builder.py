"""
Context builder — assembles review context from parsed diffs.

Phase 1 (MVP): simplified — uses diff content directly without pulling full files.
Phase 2+: add GitHub API full-file fetching + AST extraction.
"""

from cr_agent.agents.base import ReviewContext


def build_contexts(
    diffs: list,  # list[FileDiff]
    language_hints: dict[str, str] | None = None,
    extra_context: str = "",
) -> list[ReviewContext]:
    """
    Build ReviewContext objects from parsed FileDiffs.

    Phase 1 strategy:
    - Feed the full hunk content for each file
    - No external file fetching (no GitHub API dependency yet)
    - Token budget is handled per-file (each file is a separate context)
    """
    contexts = []
    if language_hints is None:
        language_hints = {}

    for diff in diffs:
        # Format the diff content for LLM
        diff_text = _format_diff_for_llm(diff)
        if not diff_text.strip():
            continue

        language = language_hints.get(
            diff.path, _guess_language(diff.path)
        )

        ctx = ReviewContext(
            diff_content=diff_text,
            file_path=diff.path,
            language=language,
            extra_context=extra_context,
        )
        contexts.append(ctx)

    return contexts


def _format_diff_for_llm(diff) -> str:
    """Format a single FileDiff into human-readable text for LLM analysis."""
    lines = [f"### {diff.path}"]

    if diff.is_new_file:
        lines.append("(new file)")
    elif diff.is_deleted_file:
        lines.append("(deleted file)")

    for hunk in diff.hunks:
        lines.append(hunk.header)
        lines.extend(hunk.lines)

    return "\n".join(lines)


def _guess_language(path: str) -> str:
    """Detect programming language from file extension."""
    ext_map = {
        ".py": "python",
        ".pyi": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".swift": "swift",
        ".rb": "ruby",
        ".php": "php",
        ".c": "c",
        ".h": "c",
        ".cpp": "c++",
        ".cc": "c++",
        ".hpp": "c++",
        ".cs": "c#",
        ".sql": "sql",
        ".sh": "shell",
        ".bash": "shell",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".json": "json",
        ".md": "markdown",
    }
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ext_map.get(f".{ext}", "unknown")
