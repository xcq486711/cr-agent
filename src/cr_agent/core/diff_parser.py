"""
Unified diff parser — converts .patch / git diff output into structured FileDiff objects.

Handles standard unified diff format:
- --- a/path and +++ b/path headers
- @@ -old_start,old_count +new_start,new_count @@ context
- Added (+), removed (-), and context ( ) lines
"""

from dataclasses import dataclass, field


@dataclass
class DiffHunk:
    """A single hunk within a file diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str  # The full @@ line (may include function context)
    lines: list[str] = field(default_factory=list)  # Raw lines including +/-/space prefix

    @property
    def added_lines(self) -> list[tuple[int, str]]:
        """Return (line_number, content) for added lines."""
        result = []
        line_num = self.new_start
        for line in self.lines:
            if line.startswith("+"):
                result.append((line_num, line[1:]))
                line_num += 1
            elif line.startswith("-"):
                pass  # Removed line doesn't increment new line counter
            else:
                line_num += 1
        return result

    @property
    def removed_lines(self) -> list[tuple[int, str]]:
        """Return (line_number, content) for removed lines (using old file numbering)."""
        result = []
        line_num = self.old_start
        for line in self.lines:
            if line.startswith("-"):
                result.append((line_num, line[1:]))
                line_num += 1
            elif line.startswith("+"):
                pass  # Added line doesn't increment old line counter
            else:
                line_num += 1
        return result


@dataclass
class FileDiff:
    """Parsed diff for a single file."""

    old_path: str | None  # None for new files
    new_path: str | None  # None for deleted files
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new_file: bool = False
    is_deleted_file: bool = False
    is_rename: bool = False

    @property
    def path(self) -> str:
        """The effective file path (prefer new_path)."""
        return self.new_path or self.old_path or ""

    @property
    def total_added(self) -> int:
        return sum(len(h.added_lines) for h in self.hunks)

    @property
    def total_removed(self) -> int:
        return sum(len(h.removed_lines) for h in self.hunks)

    @property
    def total_changed(self) -> int:
        return self.total_added + self.total_removed


def parse_diff(diff_text: str) -> list[FileDiff]:
    """
    Parse unified diff text into a list of FileDiff objects.

    Accepts output from:
    - git diff
    - git format-patch
    - GitHub PR .patch files
    """
    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: DiffHunk | None = None
    lines = diff_text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]

        # New file diff starts with "diff --git" or "---"
        if line.startswith("diff --git"):
            # Save previous file
            if current_file is not None:
                files.append(current_file)

            current_file = _parse_diff_header(lines, i)
            current_hunk = None
            # Skip to next meaningful line
            i += 1
            continue

        # File header lines (--- and +++)
        if line.startswith("--- "):
            if current_file is not None:
                old_path = _strip_path_prefix(line[4:])
                if old_path != "/dev/null":
                    current_file.old_path = old_path
                else:
                    current_file.is_new_file = True
            i += 1
            continue

        if line.startswith("+++ "):
            if current_file is not None:
                new_path = _strip_path_prefix(line[4:])
                if new_path != "/dev/null":
                    current_file.new_path = new_path
                else:
                    current_file.is_deleted_file = True
            i += 1
            continue

        # Hunk header
        if line.startswith("@@"):
            current_hunk = _parse_hunk_header(line)
            if current_file is not None and current_hunk is not None:
                current_file.hunks.append(current_hunk)
            i += 1
            continue

        # Hunk content lines
        if current_hunk is not None and (
            line.startswith("+") or line.startswith("-") or line.startswith(" ")
        ):
            current_hunk.lines.append(line)
            i += 1
            continue

        # Skip other lines (index, mode, similarity, etc.)
        i += 1

    # Don't forget the last file
    if current_file is not None:
        files.append(current_file)

    return files


def _parse_diff_header(lines: list[str], start: int) -> FileDiff:
    """Parse the diff --git header and subsequent metadata lines."""
    file_diff = FileDiff(old_path=None, new_path=None)

    line = lines[start]
    # Extract paths from "diff --git a/path b/path"
    if line.startswith("diff --git"):
        parts = line[len("diff --git "):].split(" b/", 1)
        if len(parts) == 2:
            file_diff.old_path = parts[0].lstrip("a/")
            file_diff.new_path = parts[1]

    # Check subsequent lines for metadata
    j = start + 1
    while j < len(lines) and not lines[j].startswith("---") and not lines[j].startswith("diff "):
        meta_line = lines[j]
        if meta_line.startswith("new file mode"):
            file_diff.is_new_file = True
        elif meta_line.startswith("deleted file mode"):
            file_diff.is_deleted_file = True
        elif meta_line.startswith("rename from") or meta_line.startswith("rename to"):
            file_diff.is_rename = True
        j += 1

    return file_diff


def _parse_hunk_header(line: str) -> DiffHunk | None:
    """Parse @@ -old_start,old_count +new_start,new_count @@ context."""
    import re

    match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)", line)
    if not match:
        return None

    old_start = int(match.group(1))
    old_count = int(match.group(2)) if match.group(2) else 1
    new_start = int(match.group(3))
    new_count = int(match.group(4)) if match.group(4) else 1
    context = match.group(5).strip()

    return DiffHunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        header=line,
        lines=[],
    )


def _strip_path_prefix(path: str) -> str:
    """Strip a/ or b/ prefix from diff paths."""
    path = path.strip()
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path
