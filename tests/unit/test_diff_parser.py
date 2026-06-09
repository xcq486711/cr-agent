"""Unit tests for diff parser and file filter."""

from pathlib import Path

import pytest

from cr_agent.core.diff_parser import FileDiff, parse_diff
from cr_agent.core.file_filter import FilterConfig, filter_diffs

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class TestDiffParser:
    """Test unified diff parsing."""

    def get_sample_patch(self) -> str:
        return (FIXTURES_DIR / "sample.patch").read_text()

    def test_parses_multiple_files(self):
        diffs = parse_diff(self.get_sample_patch())
        assert len(diffs) == 3

    def test_parses_modified_file(self):
        diffs = parse_diff(self.get_sample_patch())
        login_diff = diffs[0]
        assert login_diff.path == "src/auth/login.py"
        assert login_diff.is_new_file is False
        assert login_diff.is_deleted_file is False
        assert len(login_diff.hunks) == 1

    def test_parses_new_file(self):
        diffs = parse_diff(self.get_sample_patch())
        config_diff = diffs[1]
        assert config_diff.path == "src/utils/config.py"
        assert config_diff.is_new_file is True

    def test_counts_added_removed_lines(self):
        diffs = parse_diff(self.get_sample_patch())
        login_diff = diffs[0]
        assert login_diff.total_added > 0
        assert login_diff.total_removed > 0

    def test_hunk_line_numbers(self):
        diffs = parse_diff(self.get_sample_patch())
        hunk = diffs[0].hunks[0]
        assert hunk.new_start == 15
        added = hunk.added_lines
        assert len(added) > 0
        # All line numbers should be >= new_start
        for line_num, content in added:
            assert line_num >= hunk.new_start

    def test_empty_diff(self):
        diffs = parse_diff("")
        assert diffs == []

    def test_single_hunk_no_context(self):
        patch = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 line1
+added_line
 line2
 line3
"""
        diffs = parse_diff(patch)
        assert len(diffs) == 1
        assert diffs[0].total_added == 1
        assert diffs[0].total_removed == 0


class TestFileFilter:
    """Test file filtering logic."""

    def get_sample_patch(self) -> str:
        return (FIXTURES_DIR / "sample.patch").read_text()

    def test_filters_lock_files(self):
        diffs = parse_diff(self.get_sample_patch())
        filtered = filter_diffs(diffs)
        paths = [d.path for d in filtered]
        assert "package-lock.json" not in paths

    def test_keeps_source_files(self):
        diffs = parse_diff(self.get_sample_patch())
        filtered = filter_diffs(diffs)
        paths = [d.path for d in filtered]
        assert "src/auth/login.py" in paths
        assert "src/utils/config.py" in paths

    def test_custom_exclude_pattern(self):
        diffs = parse_diff(self.get_sample_patch())
        config = FilterConfig(exclude_patterns=["src/utils/*"])
        filtered = filter_diffs(diffs, config)
        paths = [d.path for d in filtered]
        assert "src/utils/config.py" not in paths
        assert "src/auth/login.py" in paths

    def test_max_file_lines(self):
        # Create a diff with many changes
        big_patch = "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n@@ -1,5 +1,1005 @@\n"
        big_patch += "\n".join([f"+line{i}" for i in range(1001)])
        diffs = parse_diff(big_patch)
        config = FilterConfig(max_file_lines=1000, exclude_patterns=[], exclude_extensions=[])
        filtered = filter_diffs(diffs, config)
        assert len(filtered) == 0

    def test_skips_deleted_files(self):
        patch = """diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-line1
-line2
-line3
"""
        diffs = parse_diff(patch)
        filtered = filter_diffs(diffs)
        assert len(filtered) == 0
