"""File filter — excludes files that shouldn't be reviewed."""

import re
from dataclasses import dataclass, field

from .diff_parser import FileDiff


@dataclass
class FilterConfig:
    """Configuration for file filtering."""

    exclude_patterns: list[str] = field(default_factory=lambda: [
        # Lock files
        "*.lock",
        "*.sum",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Pipfile.lock",
        "poetry.lock",
        "uv.lock",
        # Generated code
        "*.generated.*",
        "*.gen.*",
        "*.pb.go",
        "*_generated.go",
        # Vendored / third-party
        "vendor/*",
        "node_modules/*",
        "third_party/*",
        # Minified
        "*.min.js",
        "*.min.css",
        # Build outputs
        "dist/*",
        "build/*",
        ".next/*",
        # Data / assets
        "*.json",  # Configs are usually not worth reviewing via LLM
        "*.yaml",
        "*.yml",
        "*.toml",
        "*.xml",
        "*.svg",
        "*.png",
        "*.jpg",
    ])

    exclude_extensions: list[str] = field(default_factory=lambda: [
        ".lock", ".sum", ".map", ".min.js", ".min.css",
    ])

    max_file_lines: int = 1000  # Skip files with > 1000 changed lines


def filter_diffs(diffs: list[FileDiff], config: FilterConfig | None = None) -> list[FileDiff]:
    """Filter out files that shouldn't be reviewed. Returns the remaining diffs."""
    if config is None:
        config = FilterConfig()

    result = []
    for diff in diffs:
        if _should_exclude(diff, config):
            continue
        result.append(diff)
    return result


def _should_exclude(diff: FileDiff, config: FilterConfig) -> bool:
    """Check if a file should be excluded from review."""
    path = diff.path

    # Skip deleted files (nothing to review)
    if diff.is_deleted_file:
        return True

    # Skip renames with no content change
    if diff.is_rename and diff.total_changed == 0:
        return True

    # Check extension
    for ext in config.exclude_extensions:
        if path.endswith(ext):
            return True

    # Check glob patterns
    for pattern in config.exclude_patterns:
        if _glob_match(pattern, path):
            return True

    # Skip very large changes (likely generated or bulk refactor)
    if diff.total_changed > config.max_file_lines:
        return True

    return False


def _glob_match(pattern: str, path: str) -> bool:
    """Simple glob matching (supports * and **)."""
    # Convert glob to regex
    regex = pattern.replace(".", r"\.")
    regex = regex.replace("**", "__GLOBSTAR__")
    regex = regex.replace("*", r"[^/]*")
    regex = regex.replace("__GLOBSTAR__", r".*")
    regex = f"^{regex}$"

    return bool(re.match(regex, path))
