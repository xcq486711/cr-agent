"""
Tool system — registerable tools that LLM agents can invoke during review.

Architecture:
  - Tool: declarative tool definition (name, description, parameters, handler)
  - ToolRegistry: manages registered tools, generates API function schemas
  - ToolResult: returned by tool execution

The LLM decides when to call a tool, we execute it, and feed results back.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    """Result of a tool execution."""
    success: bool
    content: str
    error: str | None = None
    truncated: bool = False
    max_length: int = 8000  # Truncate content beyond this


@dataclass
class Tool:
    """A tool that an LLM agent can invoke."""

    name: str
    description: str
    parameters: dict  # JSON Schema for parameters
    handler: Callable  # async (params: dict) -> ToolResult
    read_only: bool = True

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI-compatible function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Manages registered tools and generates LLM-format tool schemas."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def to_openai_schemas(self) -> list[dict]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def tool_descriptions(self) -> str:
        """Human-readable tool listing for system prompts."""
        lines = []
        for t in self._tools.values():
            lines.append(f"- {t.name}: {t.description}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Built-in tools for code review
# ---------------------------------------------------------------------------

async def _read_file_handler(params: dict, workspace: str | None = None) -> ToolResult:
    """Read a file from the workspace."""
    import os
    path = params.get("path", "")
    if workspace and not os.path.isabs(path):
        path = os.path.join(workspace, path)

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return ToolResult(success=False, content="", error=f"File not found: {path}")
    except PermissionError:
        return ToolResult(success=False, content="", error=f"Permission denied: {path}")

    # Optional line range: "10-30" or "10"
    line_range = params.get("lines", "")
    if line_range:
        try:
            parts = line_range.split("-")
            start = int(parts[0]) - 1
            end = int(parts[1]) if len(parts) > 1 else start + 1
            lines = content.splitlines()
            content = "\n".join(lines[start:end])
        except (ValueError, IndexError):
            pass

    if len(content) > ToolResult.max_length:
        content = content[:ToolResult.max_length] + "\n... (truncated)"
        return ToolResult(success=True, content=content, truncated=True)

    return ToolResult(success=True, content=content)


async def _grep_handler(params: dict, workspace: str | None = None) -> ToolResult:
    """Search for a pattern in files under the workspace."""
    import os
    import re

    pattern = params.get("pattern", "")
    path_filter = params.get("path_filter", "*")  # e.g., "*.java" or "src/main/**"
    max_results = params.get("max_results", 20)
    max_files = params.get("max_files", 10)

    if not workspace:
        return ToolResult(success=False, content="", error="No workspace configured")

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return ToolResult(success=False, content="", error=f"Invalid regex: {e}")

    # Simple glob filter
    import fnmatch
    results = []
    seen_files = 0

    for root, dirs, files in os.walk(workspace):
        # Skip hidden and common non-source dirs
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                   ("node_modules", "vendor", "__pycache__", "build", "dist", "target", ".git")]

        for fname in files:
            if seen_files >= max_files * 3 or len(results) >= max_results:
                break
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, workspace)

            if not fnmatch.fnmatch(rel_path, path_filter) and not fnmatch.fnmatch(fname, path_filter):
                continue

            seen_files += 1
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for line_no, line in enumerate(f, 1):
                        match = regex.search(line)
                        if match:
                            results.append(f"{rel_path}:{line_no}: {line.strip()[:200]}")
                            if len(results) >= max_results:
                                break
            except (OSError, UnicodeDecodeError):
                continue

    if not results:
        return ToolResult(success=True, content=f"No matches for '{pattern}' in {workspace}")
    return ToolResult(success=True, content="\n".join(results[:max_results]))


async def _list_dir_handler(params: dict, workspace: str | None = None) -> ToolResult:
    """List files in a directory."""
    import os
    path = params.get("path", ".")
    if workspace and not os.path.isabs(path):
        path = os.path.join(workspace, path)

    if not os.path.isdir(path):
        return ToolResult(success=False, content="", error=f"Not a directory: {path}")

    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return ToolResult(success=False, content="", error=f"Permission denied: {path}")

    lines = []
    for e in entries:
        full = os.path.join(path, e)
        tag = "/" if os.path.isdir(full) else ""
        lines.append(f"{e}{tag}")

    return ToolResult(success=True, content="\n".join(lines)[:ToolResult.max_length])


# --- Factory functions to create tools with workspace binding ---

def create_read_file_tool(workspace: str | None = None) -> Tool:
    async def handler(params):
        return await _read_file_handler(params, workspace)
    return Tool(
        name="read_file",
        description="Read a file from the codebase. Use to see the full implementation of a function or class referenced in the diff.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute file path"},
                "lines": {"type": "string", "description": "Optional line range, e.g. '10-30' or '50'"},
            },
            "required": ["path"],
        },
        handler=handler,
    )


def create_grep_tool(workspace: str | None = None) -> Tool:
    async def handler(params):
        return await _grep_handler(params, workspace)
    return Tool(
        name="grep",
        description="Search for a text/regex pattern in the codebase. Use to find references, callers, or definitions.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for. E.g. 'getBedId' or 'class AlertService'"},
                "path_filter": {"type": "string", "description": "Glob filter for files. E.g. '*.java' or 'src/main/**'"},
                "max_results": {"type": "integer", "description": "Max results to return (default 20)"},
            },
            "required": ["pattern"],
        },
        handler=handler,
    )


def create_list_dir_tool(workspace: str | None = None) -> Tool:
    async def handler(params):
        return await _list_dir_handler(params, workspace)
    return Tool(
        name="list_dir",
        description="List files in a directory. Use to understand project structure.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to workspace root"},
            },
        },
        handler=handler,
    )


def create_default_tools(workspace: str | None = None) -> ToolRegistry:
    """Create a ToolRegistry pre-populated with standard code review tools."""
    registry = ToolRegistry()
    registry.register(create_read_file_tool(workspace))
    registry.register(create_grep_tool(workspace))
    registry.register(create_list_dir_tool(workspace))
    return registry
