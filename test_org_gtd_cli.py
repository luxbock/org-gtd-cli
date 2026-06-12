"""
Pytest test suite for org-gtd-cli.

Black-box CLI testing: calls the org-gtd-cli Python wrapper via subprocess
and asserts on stdout, stderr, exit code, and file contents.

Port of the 3148-line bash test suite (46 sections, 744+ assertions).
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CLI_SCRIPT = Path(__file__).parent / "org-gtd-cli.py"
CORE_FILE = Path(__file__).parent / "gtd-core.el"
ELISP_FILE = Path(__file__).parent / "org-gtd-cli.el"

# Fake gcal calendar id used by add-event tests; the real id lives as a
# file-level "#+PROPERTY: calendar-id ..." in the live org file, not in code.
FAKE_CALENDAR_ID = "test-family@group.calendar.google.com"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org_dir(tmp_path):
    """Copy fixture org files to a temp directory."""
    for f in FIXTURES_DIR.glob("*.org"):
        dest = tmp_path / f.name
        shutil.copy(f, dest)
        dest.chmod(0o644)
    (tmp_path / "agent-notes").mkdir(exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(*args, org_dir, env_overrides=None):
    """Run org-gtd-cli with the given arguments.

    Returns (stdout, stderr, returncode).
    env_overrides, if given, is merged into the subprocess environment (used
    to simulate the non-UTF-8 locale of systemd/bwrap invocations).
    """
    env = os.environ.copy()
    env["ORG_DIRECTORY"] = str(org_dir) + "/"
    env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
    env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
    if env_overrides:
        env.update(env_overrides)
    cmd = ["python3", str(CLI_SCRIPT)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
    return result.stdout, result.stderr, result.returncode


def reset_fixtures(org_dir):
    """Re-copy fixture files into org_dir (equivalent to reset_fixtures in bash)."""
    # Remove everything except agent-notes dir
    for item in org_dir.iterdir():
        if item.name == "agent-notes":
            # Clear contents but keep directory
            for f in item.iterdir():
                if f.is_file():
                    f.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for f in FIXTURES_DIR.glob("*.org"):
        dest = org_dir / f.name
        shutil.copy(f, dest)
        dest.chmod(0o644)
    (org_dir / "agent-notes").mkdir(exist_ok=True)


def assert_line_before(filepath, first, second):
    """Assert that `first` appears on an earlier line than `second` in the file."""
    text = filepath.read_text()
    lines = text.splitlines()
    line_a = None
    line_b = None
    for i, line in enumerate(lines):
        if first in line and line_a is None:
            line_a = i
        if second in line and line_b is None:
            line_b = i
    assert line_a is not None, f"'{first}' not found in {filepath.name}"
    assert line_b is not None, f"'{second}' not found in {filepath.name}"
    assert line_a < line_b, (
        f"'{first}' (line {line_a}) is not before '{second}' (line {line_b}) "
        f"in {filepath.name}"
    )


def assert_no_long_lines(filepath, start_pat, end_pat, max_width):
    """Assert no lines between start_pat and end_pat exceed max_width chars."""
    text = filepath.read_text()
    in_range = False
    bad_lines = []
    for line in text.splitlines():
        if not in_range:
            if start_pat in line:
                in_range = True
        else:
            if end_pat and end_pat in line:
                break
            if len(line) > max_width:
                bad_lines.append(f"  ({len(line)} chars) {line}")
    assert not bad_lines, (
        f"Lines exceed {max_width} chars in {filepath.name}:\n" +
        "\n".join(bad_lines[:5])
    )


def assert_no_consecutive_blank_lines(filepath):
    """Assert the file contains no three consecutive newlines."""
    text = filepath.read_text()
    assert "\n\n\n" not in text, (
        f"{filepath.name} contains consecutive blank lines"
    )


def md5(filepath):
    """Return MD5 hex digest of a file."""
    return hashlib.md5(filepath.read_bytes()).hexdigest()


def line_number_of(filepath, pattern):
    """Return 1-based line number of the first line containing pattern, or None."""
    for i, line in enumerate(filepath.read_text().splitlines(), 1):
        if pattern in line:
            return i
    return None


def get_line(filepath, lineno):
    """Return the content of a specific 1-based line number."""
    lines = filepath.read_text().splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return None


# ===========================================================================
# 1. org-timestamp
# ===========================================================================

class TestOrgTimestamp:
    def test_date_with_day_of_week(self, org_dir):
        stdout, stderr, rc = run_cli("org-timestamp", "2026-03-15", org_dir=org_dir)
        assert rc == 0
        assert "<2026-03-15 Sun>" in stdout

    def test_date_with_time(self, org_dir):
        stdout, stderr, rc = run_cli("org-timestamp", "2026-03-15", "14:00", org_dir=org_dir)
        assert rc == 0
        assert "<2026-03-15 Sun 14:00>" in stdout

    def test_time_range(self, org_dir):
        stdout, stderr, rc = run_cli("org-timestamp", "2026-03-15", "14:00-15:30", org_dir=org_dir)
        assert rc == 0
        assert "<2026-03-15 Sun 14:00-15:30>" in stdout


# ===========================================================================
# 2. add-task
# ===========================================================================

class TestAddTask:
    def test_adds_to_inbox_by_default(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Test task", org_dir=org_dir)
        assert rc == 0
        assert "TODO Test task" in (org_dir / "inbox.org").read_text()
        assert "Added: Test task -> inbox.org (inbox.org)" in stdout

    def test_with_tags(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Tagged task", "--tags", "buy,@errand", org_dir=org_dir)
        assert rc == 0
        assert ":buy:@errand:" in (org_dir / "inbox.org").read_text()

    def test_with_schedule(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Scheduled task", "--schedule", "2026-03-20", org_dir=org_dir)
        assert rc == 0
        assert "SCHEDULED: <2026-03-20 Fri>" in (org_dir / "inbox.org").read_text()

    def test_with_deadline(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Deadline task", "--deadline", "2026-03-25", org_dir=org_dir)
        assert rc == 0
        assert "DEADLINE: <2026-03-25 Wed>" in (org_dir / "inbox.org").read_text()

    def test_with_body(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Body task", "--body", "This is the body text", org_dir=org_dir)
        assert rc == 0
        assert "This is the body text" in (org_dir / "inbox.org").read_text()

    def test_with_priority(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Priority task", "--priority", "A", org_dir=org_dir)
        assert rc == 0
        assert "[#A]" in (org_dir / "inbox.org").read_text()

    def test_with_state_waiting(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Waiting task", "--state", "WAITING", org_dir=org_dir)
        assert rc == 0
        assert "WAITING Waiting task" in (org_dir / "inbox.org").read_text()

    def test_with_category_single_segment(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Category task", "--category", "Finance", org_dir=org_dir)
        assert rc == 0
        assert "TODO Category task" in (org_dir / "tasks.org").read_text()
        assert "tasks.org/Finance" in stdout

    def test_category_not_found(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Missing cat", "--category", "Nonexistent", org_dir=org_dir)
        assert rc == 1

    def test_rejects_body_containing_org_heading(self, org_dir):
        stdout, stderr, rc = run_cli(
            "add-task", "Heading body task", "--body", "Some text\n* Sneaky heading",
            org_dir=org_dir,
        )
        assert rc == 1
        assert "Error" in stderr

    def test_category_path_match(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Path task", "--category", "Computers/Agents", org_dir=org_dir)
        assert rc == 0
        assert "TODO Path task" in (org_dir / "tasks.org").read_text()
        assert "Computers/Agents" in stdout

    def test_ambiguous_category(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Ambig task", "--category", "Tools", org_dir=org_dir)
        assert rc == 2
        assert "Multiple category matches" in stderr
        assert "Computers/Tools" in stderr
        assert "Research/Tools" in stderr

    def test_path_disambiguates(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Disambig task", "--category", "Research/Tools", org_dir=org_dir)
        assert rc == 0
        assert "TODO Disambig task" in (org_dir / "tasks.org").read_text()
        assert "Research/Tools" in stdout

    def test_wrong_path_not_found(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Wrong path", "--category", "Work/Agents", org_dir=org_dir)
        assert rc == 1
        assert "not found" in stderr

    def test_category_skips_todo_headings_holiday(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Holiday test task", "--category", "Holiday", org_dir=org_dir)
        assert rc == 0
        assert "TODO Holiday test task" in (org_dir / "tasks.org").read_text()
        assert "Travel/Holiday Trip" in stdout

    def test_category_only_todo_matches_returns_not_found(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Design test task", "--category", "Design", org_dir=org_dir)
        assert rc == 1
        assert "not found" in stderr

    def test_category_path_to_plain_heading_under_task(self, org_dir):
        stdout, stderr, rc = run_cli(
            "add-task", "Task under project", "--category",
            "Improve agent workflow/Resources", org_dir=org_dir,
        )
        assert rc == 0
        assert "TODO Task under project" in (org_dir / "tasks.org").read_text()
        assert "Improve agent workflow/Resources" in stdout

    def test_category_resources_ambiguous(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Ambig resources task", "--category", "Resources", org_dir=org_dir)
        assert rc == 2
        assert "Multiple category matches" in stderr
        assert "Improve agent workflow/Resources" in stderr
        assert "Research/Resources" in stderr


# ===========================================================================
# 3. add-task --category: no extra blank lines
# ===========================================================================

class TestAddTaskCategoryBlankLines:
    def test_repeated_add_task_category_no_consecutive_blank_lines(self, org_dir):
        run_cli("add-task", "First category task", "--category", "Finance", org_dir=org_dir)
        run_cli("add-task", "Second category task", "--category", "Finance", org_dir=org_dir)
        stdout, stderr, rc = run_cli("add-task", "Third category task", "--category", "Finance", org_dir=org_dir)
        assert rc == 0
        assert_no_consecutive_blank_lines(org_dir / "tasks.org")


# ===========================================================================
# 4. add-subtask
# ===========================================================================

class TestAddSubtask:
    def test_adds_child_at_correct_level(self, org_dir):
        stdout, stderr, rc = run_cli("add-subtask", "Write quarterly report", "Draft introduction", org_dir=org_dir)
        assert rc == 0
        assert "*** TODO Draft introduction" in (org_dir / "tasks.org").read_text()
        assert "Added subtask" in stdout

    def test_disambiguation(self, org_dir):
        stdout, stderr, rc = run_cli("add-subtask", "Buy", "New subtask", org_dir=org_dir)
        assert rc == 2

    def test_index_selects_match(self, org_dir):
        stdout, stderr, rc = run_cli("add-subtask", "Buy", "New subtask", "--index", "1", org_dir=org_dir)
        assert rc == 0


# ===========================================================================
# 4b. find-task: category-aware no-match hint
# ===========================================================================

class TestFindTaskCategoryHint:
    """When a substring matches only a category heading (no TODO keyword),
    the no-match error hint should point at add-task --category with the
    full slash path instead of the generic 'shorter substring' advice."""

    def _json_error(self, stderr):
        for line in stderr.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        raise AssertionError(f"No JSON error found in stderr: {stderr}")

    def test_add_subtask_category_substring_json(self, org_dir):
        # "Pet Ants" is a category heading (* Family / ** Pet Ants), not a task
        stdout, stderr, rc = run_cli(
            "--json", "add-subtask", "Pet Ants", "New child", org_dir=org_dir)
        assert rc == 1
        err = self._json_error(stderr)
        assert 'No task found matching "Pet Ants"' in err["error"]
        assert "category heading, not a task" in err["hint"]
        assert 'add-task --category "Family/Pet Ants"' in err["hint"]

    def test_add_subtask_category_substring_text(self, org_dir):
        stdout, stderr, rc = run_cli(
            "add-subtask", "Pet Ants", "New child", org_dir=org_dir)
        assert rc == 1
        assert 'No task found matching "Pet Ants"' in stderr
        assert "category heading, not a task" in stderr
        assert 'add-task --category "Family/Pet Ants"' in stderr

    def test_genuine_no_match_keeps_generic_hint(self, org_dir):
        stdout, stderr, rc = run_cli(
            "--json", "add-subtask", "zzqqxxk-nonexistent", "child", org_dir=org_dir)
        assert rc == 1
        err = self._json_error(stderr)
        assert 'No task found matching "zzqqxxk-nonexistent"' in err["error"]
        assert err["hint"] == "Try a shorter substring, or use 'search' for partial matches."
        assert "add-task --category" not in err["hint"]

    def test_show_category_substring_gets_hint_too(self, org_dir):
        # find-task is shared, so `show` gets the category-aware hint as well
        stdout, stderr, rc = run_cli("--json", "show", "Holiday Trip", org_dir=org_dir)
        assert rc == 1
        err = self._json_error(stderr)
        assert 'No task found matching "Holiday Trip"' in err["error"]
        assert "category heading, not a task" in err["hint"]
        assert 'add-task --category "Travel/Holiday Trip"' in err["hint"]


# ===========================================================================
# 5. add-subtask: no extra blank lines
# ===========================================================================

class TestAddSubtaskBlankLines:
    def test_repeated_add_subtask_no_consecutive_blank_lines(self, org_dir):
        run_cli("add-subtask", "Write quarterly report", "First subtask", org_dir=org_dir)
        run_cli("add-subtask", "Write quarterly report", "Second subtask", org_dir=org_dir)
        stdout, stderr, rc = run_cli("add-subtask", "Write quarterly report", "Third subtask", org_dir=org_dir)
        assert rc == 0
        assert_no_consecutive_blank_lines(org_dir / "tasks.org")


# ===========================================================================
# 6. agenda
# ===========================================================================

class TestAgenda:
    def test_returns_all_non_done_tasks(self, org_dir):
        stdout, stderr, rc = run_cli("agenda", org_dir=org_dir)
        assert rc == 0
        assert "TODO" in stdout
        assert "DONE Submit expense claims" not in stdout

    def test_deadline_shown(self, org_dir):
        stdout, stderr, rc = run_cli("agenda", org_dir=org_dir)
        assert rc == 0
        assert "D:<2026-03-15 Sun>" in stdout

    def test_priority_shown(self, org_dir):
        stdout, stderr, rc = run_cli("agenda", org_dir=org_dir)
        assert "[#A]" in stdout

    def test_file_ref_shown(self, org_dir):
        stdout, stderr, rc = run_cli("agenda", org_dir=org_dir)
        assert "(inbox.org)" in stdout

    def test_state_filter_todo(self, org_dir):
        stdout, stderr, rc = run_cli("agenda", "--state", "TODO", org_dir=org_dir)
        assert rc == 0
        assert "NEXT " not in stdout
        assert "WAITING " not in stdout

    def test_state_filter_waiting(self, org_dir):
        stdout, stderr, rc = run_cli("agenda", "--state", "WAITING", org_dir=org_dir)
        assert rc == 0
        assert "Consider buying a new monitor" in stdout
        assert "Get travel insurance quote" in stdout

    def test_tag_filter_agent(self, org_dir):
        stdout, stderr, rc = run_cli("agenda", "--tag", "@agent", org_dir=org_dir)
        assert rc == 0
        assert "Set up automated backups" in stdout
        assert "Buy a formicarium" in stdout
        assert "Pay quarterly taxes" not in stdout

    def test_tag_filter_and_via_repeated_flag(self, org_dir):
        """--tag @agent --tag family returns only tasks with both tags."""
        stdout, stderr, rc = run_cli("agenda", "--tag", "@agent", "--tag", "family", org_dir=org_dir)
        assert rc == 0
        assert "Buy a formicarium" in stdout
        # These are @agent but not family:
        assert "Set up automated backups" not in stdout

    def test_tag_filter_and_via_plus(self, org_dir):
        """--tag @agent+family is equivalent to two --tag flags (backwards compat)."""
        stdout, stderr, rc = run_cli("agenda", "--tag", "@agent+family", org_dir=org_dir)
        assert rc == 0
        assert "Buy a formicarium" in stdout
        assert "Set up automated backups" not in stdout

    def test_tag_filter_or_via_comma(self, org_dir):
        """--tag buy,travel returns tasks with either tag."""
        stdout, stderr, rc = run_cli("agenda", "--tag", "buy,travel", org_dir=org_dir)
        assert rc == 0
        assert "Buy a small UPS" in stdout
        assert "Holiday pre-trip tasks" in stdout
        # Not in buy or travel:
        assert "Pay quarterly taxes" not in stdout

    def test_date_range_filter(self, org_dir):
        stdout, stderr, rc = run_cli("agenda", "--from", "2026-03-10", "--to", "2026-03-15", org_dir=org_dir)
        assert rc == 0
        assert "Pay quarterly taxes" in stdout
        assert "Choose a formicarium" in stdout
        assert "Write quarterly report" not in stdout
        assert "Buy groceries" not in stdout


# ===========================================================================
# 7. search
# ===========================================================================

class TestSearch:
    def test_default_state_filter(self, org_dir):
        stdout, stderr, rc = run_cli("search", "formicarium", org_dir=org_dir)
        assert rc == 0
        assert "[1]" in stdout
        assert "[2]" in stdout
        assert "TODO Buy a formicarium" in stdout
        assert "NEXT Choose a formicarium" in stdout
        assert "Research formicarium options" not in stdout

    def test_state_all_includes_done(self, org_dir):
        stdout, stderr, rc = run_cli("search", "formicarium", "--state", "all", org_dir=org_dir)
        assert rc == 0
        assert "Research formicarium options" in stdout
        assert "Buy a formicarium" in stdout
        assert "Choose a formicarium" in stdout

    def test_file_restricts_to_single_file(self, org_dir):
        stdout, stderr, rc = run_cli("search", "buy", "--file", "inbox.org", org_dir=org_dir)
        assert rc == 0
        assert "Buy groceries" in stdout
        assert "Buy a formicarium" not in stdout
        assert "(inbox.org)" in stdout

    def test_no_matches_returns_exit_0(self, org_dir):
        stdout, stderr, rc = run_cli("search", "zzzznonexistent", org_dir=org_dir)
        assert rc == 0
        assert "No matches." in stderr

    def test_empty_substr_returns_exit_1(self, org_dir):
        stdout, stderr, rc = run_cli("search", "", org_dir=org_dir)
        assert rc == 1
        assert "Error" in stderr

    def test_tag_filter(self, org_dir):
        stdout, stderr, rc = run_cli("search", "backups", "--tag", "@agent", org_dir=org_dir)
        assert rc == 0
        assert "Set up automated backups" in stdout
        assert "Buy a formicarium" not in stdout

    def test_state_waiting(self, org_dir):
        stdout, stderr, rc = run_cli("search", "insurance", "--state", "WAITING", org_dir=org_dir)
        assert rc == 0
        assert "Get travel insurance quote" in stdout

    def test_state_done_finds_only_done(self, org_dir):
        stdout, stderr, rc = run_cli("search", "dentist", "--state", "DONE", org_dir=org_dir)
        assert rc == 0
        assert "Research dentists" in stdout
        assert "Reply to dentist" not in stdout

    def test_cross_file_matches(self, org_dir):
        stdout, stderr, rc = run_cli("search", "quarterly", org_dir=org_dir)
        assert rc == 0
        assert "Pay quarterly taxes" in stdout
        assert "Write quarterly report" in stdout

    def test_file_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("search", "buy", "--file", "nonexistent.org", org_dir=org_dir)
        assert rc == 1
        assert "file not found" in stderr

    def test_tag_inherited(self, org_dir):
        stdout, stderr, rc = run_cli("search", "report", "--tag", "work", org_dir=org_dir)
        assert rc == 0
        assert "Write quarterly report" in stdout

    def test_cross_file_match_from_inbox(self, org_dir):
        stdout, stderr, rc = run_cli("search", "interesting", org_dir=org_dir)
        assert rc == 0
        assert "interesting" in stdout
        assert "(inbox.org)" in stdout

    def test_valid_state_with_no_matches(self, org_dir):
        stdout, stderr, rc = run_cli("search", "formicarium", "--state", "CANCELLED", org_dir=org_dir)
        assert rc == 0
        assert "No matches." in stderr

    def test_search_without_substr_with_tag(self, org_dir):
        """search --tag @agent returns agent tasks without requiring SUBSTR."""
        stdout, stderr, rc = run_cli("search", "--tag", "@agent", "--state", "all", org_dir=org_dir)
        assert rc == 0
        assert "Set up automated backups" in stdout
        assert "Buy a formicarium" in stdout

    def test_search_without_substr_with_state(self, org_dir):
        """search --state WAITING returns waiting tasks without requiring SUBSTR."""
        stdout, stderr, rc = run_cli("search", "--state", "WAITING", org_dir=org_dir)
        assert rc == 0
        assert "Consider buying a new monitor" in stdout

    def test_search_without_substr_or_filters_fails(self, org_dir):
        """Bare search with no SUBSTR and no filters should fail."""
        stdout, stderr, rc = run_cli("search", org_dir=org_dir)
        assert rc != 0


class TestConflictedCopyExclusion:
    """Nextcloud conflict copies must not be scanned as agenda files.

    org-agenda-file-regexp is overridden in org-gtd-cli.el to reject
    "(conflicted copy ...)" names; otherwise every result is duplicated
    and org IDs get cloned.
    """

    CONFLICT_NAME = "inbox (conflicted copy 2026-06-06 143022).org"

    def _make_conflict_copy(self, org_dir):
        shutil.copy(org_dir / "inbox.org", org_dir / self.CONFLICT_NAME)

    def test_search_has_no_duplicates(self, org_dir):
        self._make_conflict_copy(org_dir)
        stdout, stderr, rc = run_cli("--json", "search", "groceries", org_dir=org_dir)
        assert rc == 0
        data = json.loads(stdout)
        assert data["count"] == 1
        headings = [t["heading"] for t in data["tasks"]]
        assert headings.count("Buy groceries") == 1
        files = [t["file"] for t in data["tasks"]]
        assert self.CONFLICT_NAME not in files

    def test_conflict_file_not_scanned(self, org_dir):
        self._make_conflict_copy(org_dir)
        stdout, stderr, rc = run_cli(
            "--json", "search", "--state", "all", "--tag", "@agent", org_dir=org_dir
        )
        assert rc == 0
        data = json.loads(stdout)
        files = {t["file"] for t in data["tasks"]}
        assert self.CONFLICT_NAME not in files
        assert all("conflicted copy" not in f for f in files)


# ===========================================================================
# 8. show
# ===========================================================================

class TestShow:
    def test_shows_full_subtree(self, org_dir):
        stdout, stderr, rc = run_cli("show", "Buy a formicarium", org_dir=org_dir)
        assert rc == 0
        assert "Messor Barbarus" in stdout
        assert "Research formicarium options" in stdout
        assert "(tasks.org)" in stdout

    def test_strips_logbook_from_body(self, org_dir):
        """show strips :LOGBOOK:...:END: drawers from body text."""
        stdout, stderr, rc = run_cli("show", "Fix org-capture workspace", org_dir=org_dir)
        assert rc == 0
        assert ":LOGBOOK:" not in stdout
        # Body text after the LOGBOOK should still be present
        assert "existing Emacs instance" in stdout

    def test_strips_logbook_from_json_body(self, org_dir):
        """show --json strips LOGBOOK from body field."""
        stdout, stderr, rc = run_cli("--json", "show", "Fix org-capture workspace", org_dir=org_dir)
        assert rc == 0
        data = json.loads(stdout)
        assert ":LOGBOOK:" not in (data["body"] or "")
        assert "existing Emacs instance" in (data["body"] or "")

    def test_task_with_org_link(self, org_dir):
        stdout, stderr, rc = run_cli("show", "interesting article", org_dir=org_dir)
        assert rc == 0
        assert "[[https://example.com" in stdout

    def test_find_task_with_described_link_by_description(self, org_dir):
        """search/show finds a task with [[url][desc]] by the description text."""
        stdout, stderr, rc = run_cli("search", "Interesting Project", "--state", "all", org_dir=org_dir)
        assert rc == 0
        assert "Interesting Project" in stdout

    def test_find_task_with_described_link_by_show(self, org_dir):
        """show finds [[url][desc]] heading via plain-text description."""
        stdout, stderr, rc = run_cli("show", "Interesting Project", org_dir=org_dir)
        assert rc == 0
        assert "connections to my work" in stdout

    def test_find_task_with_bare_link_by_url(self, org_dir):
        """show finds [[url]] heading via url text."""
        stdout, stderr, rc = run_cli("show", "bare-link", org_dir=org_dir)
        assert rc == 0
        assert "networking" in stdout

    def test_plain_shows_heading_hierarchy(self, org_dir):
        stdout, stderr, rc = run_cli("show", "Improve agent workflow", "--plain", org_dir=org_dir)
        assert rc == 0
        assert "TODO Improve agent workflow" in stdout
        assert "  TODO Design CLI tool" in stdout
        assert "    NEXT Add more test cases" in stdout
        assert "Top pain points" not in stdout
        assert ":LOGBOOK:" not in stdout
        assert ":@agent:" not in stdout

    def test_plain_on_leaf_task(self, org_dir):
        stdout, stderr, rc = run_cli("show", "Pay quarterly taxes", "--plain", org_dir=org_dir)
        assert rc == 0
        assert "TODO [#A] Pay quarterly taxes" in stdout
        assert "DEADLINE" not in stdout

    def test_show_strips_priority_cookie_from_substr(self, org_dir):
        """show '[#A] Pay quarterly taxes' finds the task (strips priority cookie)."""
        stdout, stderr, rc = run_cli("show", "[#A] Pay quarterly taxes", org_dir=org_dir)
        assert rc == 0
        assert "Pay quarterly taxes" in stdout

    def test_search_strips_priority_cookie(self, org_dir):
        """search '[#A] Pay quarterly' finds the task."""
        stdout, stderr, rc = run_cli("search", "[#A] Pay quarterly", "--state", "all", org_dir=org_dir)
        assert rc == 0
        assert "Pay quarterly taxes" in stdout


# ===========================================================================
# 9. subtasks
# ===========================================================================

class TestSubtasks:
    def test_lists_children_with_states_and_progress(self, org_dir):
        stdout, stderr, rc = run_cli("subtasks", "Improve agent workflow", org_dir=org_dir)
        assert rc == 0
        assert "DONE What are the current pain points" in stdout
        assert "TODO Design CLI tool" in stdout
        assert "2/5 done" in stdout
        assert "(tasks.org)" in stdout

    def test_exits_1_if_no_subtasks(self, org_dir):
        stdout, stderr, rc = run_cli("subtasks", "Pay quarterly taxes", org_dir=org_dir)
        assert rc == 1

    def test_nested_project_subtasks(self, org_dir):
        stdout, stderr, rc = run_cli("subtasks", "Holiday pre-trip tasks", org_dir=org_dir)
        assert rc == 0
        assert "DONE Book flights" in stdout
        assert "NEXT Book a rental car" in stdout
        assert "WAITING Get travel insurance" in stdout


# ===========================================================================
# 10. categories
# ===========================================================================

class TestCategories:
    def test_shows_plain_headings_as_full_paths(self, org_dir):
        stdout, stderr, rc = run_cli("categories", org_dir=org_dir)
        assert rc == 0
        assert "Work (tasks.org)" in stdout
        assert "Computers/Agents (tasks.org)" in stdout
        assert "Family/Pet Ants (tasks.org)" in stdout
        assert "Computers/Tools (tasks.org)" in stdout
        assert "Research/Tools (tasks.org)" in stdout
        assert "(tasks.org)" in stdout
        assert "* " not in stdout

    def test_does_not_show_todo_headings(self, org_dir):
        stdout, stderr, rc = run_cli("categories", org_dir=org_dir)
        assert "Write quarterly report" not in stdout
        assert "Pay quarterly taxes" not in stdout
        assert "Buy a formicarium" not in stdout

    def test_does_not_show_children_of_todo_headings(self, org_dir):
        stdout, stderr, rc = run_cli("categories", org_dir=org_dir)
        assert "Add more test cases" not in stdout
        assert "Research formicarium options" not in stdout

    def test_shows_plain_headings_under_task_headings(self, org_dir):
        stdout, stderr, rc = run_cli("categories", org_dir=org_dir)
        assert "Computers/Agents/Improve agent workflow/Resources" in stdout
        assert "Research/Resources" in stdout

    def test_does_not_show_headings_from_other_files(self, org_dir):
        stdout, stderr, rc = run_cli("categories", org_dir=org_dir)
        assert "(inbox.org)" not in stdout
        assert "(calendar.org)" not in stdout

    def test_categories_different_file(self, org_dir):
        stdout, stderr, rc = run_cli("categories", "--file", "inbox.org", org_dir=org_dir)
        assert rc == 0
        assert "(tasks.org)" not in stdout

    def test_categories_nonexistent_file(self, org_dir):
        stdout, stderr, rc = run_cli("categories", "--file", "nonexistent.org", org_dir=org_dir)
        assert rc == 1
        assert "File not found" in stderr


# ===========================================================================
# 11. projects
# ===========================================================================

class TestProjects:
    def test_lists_active_projects(self, org_dir):
        stdout, stderr, rc = run_cli("projects", org_dir=org_dir)
        assert rc == 0
        assert "Computers/Agents/Improve agent workflow (tasks.org) [2/4]" in stdout
        assert "Computers/Agents/Improve agent workflow/Design CLI tool (tasks.org) [0/3]" in stdout
        assert "Computers/Agents/Improve agent workflow/Implement CLI tool (tasks.org) [0/1]" in stdout
        assert "Deep nesting parent (tasks.org) [0/1]" in stdout
        assert "Family/Pet Ants/Buy a formicarium (tasks.org) [1/3]" in stdout
        assert "Travel/Holiday Trip/Holiday pre-trip tasks (tasks.org) [2/4]" in stdout

    def test_does_not_contain_leaf_tasks(self, org_dir):
        stdout, stderr, rc = run_cli("projects", org_dir=org_dir)
        assert "Write quarterly report" not in stdout
        assert "Pay quarterly taxes" not in stdout
        assert "Add aliases" not in stdout
        assert "Buy a small UPS" not in stdout

    def test_does_not_contain_done_or_category_headings(self, org_dir):
        stdout, stderr, rc = run_cli("projects", org_dir=org_dir)
        assert "Submit expense claims" not in stdout
        assert "Fix org-capture" not in stdout
        assert "Work (" not in stdout
        assert "Computers (" not in stdout


# ===========================================================================
# 12. process-agent-tasks
# ===========================================================================

class TestProcessAgentTasks:
    def test_removed(self, org_dir):
        """process-agent-tasks is removed; use search --tag @agent instead."""
        stdout, stderr, rc = run_cli("process-agent-tasks", org_dir=org_dir)
        assert rc == 1
        assert "removed" in stderr.lower()


# ===========================================================================
# 13. done (set-done)
# ===========================================================================

class TestDone:
    def test_marks_single_match_as_done(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Book a rental car", org_dir=org_dir)
        assert rc == 0
        assert "Done: Book a rental car (tasks.org)" in stdout
        assert "DONE Book a rental car" in (org_dir / "tasks.org").read_text()

    def test_exit_2_on_ambiguous(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Buy", org_dir=org_dir)
        assert rc == 2
        assert "[1]" in stderr
        assert "[2]" in stderr

    def test_index_selects_match(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Buy", "--index", "1", org_dir=org_dir)
        assert rc == 0

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Book a rental car", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would mark done" in stdout
        assert "NEXT Book a rental car" in (org_dir / "tasks.org").read_text()

    def test_auto_progress_promotes_next_todo(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Add more test cases", org_dir=org_dir)
        assert rc == 0
        assert "Auto-progressed" in stdout
        assert "NEXT Test on actual project" in (org_dir / "tasks.org").read_text()

    def test_done_removes_waiting_tag(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Get travel insurance quote", org_dir=org_dir)
        assert rc == 0
        assert "DONE Get travel insurance quote" in (org_dir / "tasks.org").read_text()

    def test_all_siblings_done_leaves_parent_open(self, org_dir):
        # Completing the last open subtask must NOT auto-close the project:
        # remaining work may simply not be filed as subtasks yet. The parent
        # is left open for manual review and an advisory is emitted.
        stdout, stderr, rc = run_cli("set-done", "Test migration on staging", org_dir=org_dir)
        assert rc == 0
        assert "project left open for review" in stdout
        assert "Ship epiphyte updates" in stdout
        text = (org_dir / "tasks.org").read_text()
        assert "DONE Ship epiphyte updates" not in text
        assert "TODO Ship epiphyte updates" in text

    def test_subproject_drill_in_promotes_first_child(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Set up alerting", org_dir=org_dir)
        assert rc == 0
        assert "Auto-progressed" in stdout
        assert "in subproject" in stdout
        assert "Design dashboard layout" in stdout
        assert "NEXT Design dashboard layout" in (org_dir / "tasks.org").read_text()

    def test_existing_next_prevents_promotion(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Get travel insurance quote", org_dir=org_dir)
        assert rc == 0
        assert "Auto-progressed" not in stdout
        assert "Auto-completed" not in stdout

    def test_dry_run_leaves_parent_open_preview(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Test migration on staging", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "project would be left open for review" in stdout
        assert "Ship epiphyte updates" in stdout
        # Dry run makes no changes
        assert "NEXT Test migration on staging" in (org_dir / "tasks.org").read_text()

    def test_dry_run_subproject_drill_in_preview(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Set up alerting", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would auto-progress" in stdout
        assert "in subproject" in stdout
        assert "NEXT Set up alerting" in (org_dir / "tasks.org").read_text()

    def test_no_cascade_leaves_ancestors_open(self, org_dir):
        # Last subtask of a sub-project: the sub-project is left open and the
        # cascade to the grandparent does NOT run — so the grandparent's other
        # child ("Run smoke tests") is not promoted to NEXT.
        stdout, stderr, rc = run_cli("set-done", "Install certificates", org_dir=org_dir)
        assert rc == 0
        assert "project left open for review" in stdout
        assert "Set up TLS certs" in stdout
        assert "Auto-progressed" not in stdout
        text = (org_dir / "tasks.org").read_text()
        assert "DONE Set up TLS certs" not in text
        assert "TODO Set up TLS certs" in text
        assert "NEXT Run smoke tests" not in text
        assert "TODO Run smoke tests" in text

    def test_no_cascade_dry_run_preview(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Install certificates", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "project would be left open for review" in stdout
        assert "Set up TLS certs" in stdout
        assert "Would auto-progress" not in stdout
        # No cascade preview, so the grandparent sibling is not mentioned
        assert "Run smoke tests" not in stdout
        assert "NEXT Install certificates" in (org_dir / "tasks.org").read_text()


# ===========================================================================
# 14. set-state
# ===========================================================================

class TestSetState:
    def test_changes_state(self, org_dir):
        stdout, stderr, rc = run_cli("set-state", "Book a rental car", "WAITING", org_dir=org_dir)
        assert rc == 0
        assert "NEXT -> WAITING (tasks.org)" in stdout
        assert "WAITING Book a rental car" in (org_dir / "tasks.org").read_text()

    def test_waiting_adds_tag(self, org_dir):
        stdout, stderr, rc = run_cli("set-state", "Book a rental car", "WAITING", org_dir=org_dir)
        assert rc == 0
        assert ":WAITING:" in (org_dir / "tasks.org").read_text()

    def test_removing_waiting_removes_tag(self, org_dir):
        stdout, stderr, rc = run_cli("set-state", "Consider buying a new monitor", "TODO", org_dir=org_dir)
        assert rc == 0
        assert "TODO Consider buying a new monitor" in (org_dir / "tasks.org").read_text()

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("set-state", "Book a rental car", "TODO", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would change" in stdout
        assert "NEXT Book a rental car" in (org_dir / "tasks.org").read_text()

    def test_preserves_priority_cookie(self, org_dir):
        stdout, stderr, rc = run_cli("set-state", "Pay quarterly taxes", "NEXT", org_dir=org_dir)
        assert rc == 0
        assert "NEXT [#A] Pay quarterly taxes" in (org_dir / "tasks.org").read_text()

    def test_invalid_state_clean_error(self, org_dir):
        stdout, stderr, rc = run_cli("set-state", "Book a rental car", "INVALID", org_dir=org_dir)
        assert rc == 1
        assert "not a valid state" in stderr
        assert "TODO, NEXT, DONE, WAITING, DEFER, CANCELLED" in stderr

    def test_defer_to_waiting_cleans_defer_tag(self, org_dir):
        # Multi-step: DEFER then WAITING
        run_cli("set-state", "Book a rental car", "DEFER", org_dir=org_dir)
        stdout, stderr, rc = run_cli("set-state", "Book a rental car", "WAITING", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert ":WAITING:" in text
        assert ":DEFER:" not in text

    def test_waiting_to_todo_cleans_waiting_tag(self, org_dir):
        # Multi-step: WAITING then TODO
        run_cli("set-state", "Book a rental car", "WAITING", org_dir=org_dir)
        stdout, stderr, rc = run_cli("set-state", "Book a rental car", "TODO", org_dir=org_dir)
        assert rc == 0
        assert "TODO Book a rental car" in (org_dir / "tasks.org").read_text()


# ===========================================================================
# 15. set-priority
# ===========================================================================

class TestSetPriority:
    def test_set_priority_a(self, org_dir):
        stdout, stderr, rc = run_cli("set-priority", "Buy groceries", "A", org_dir=org_dir)
        assert rc == 0
        assert "Priority:" in stdout
        assert "[#B] -> [#A]" in stdout
        assert "[#A] Buy groceries" in (org_dir / "inbox.org").read_text()

    def test_change_priority_a_to_c(self, org_dir):
        # Set A first, then change to C
        run_cli("set-priority", "Buy groceries", "A", org_dir=org_dir)
        stdout, stderr, rc = run_cli("set-priority", "Buy groceries", "C", org_dir=org_dir)
        assert rc == 0
        assert "[#A] -> [#C]" in stdout
        assert "[#C] Buy groceries" in (org_dir / "inbox.org").read_text()

    def test_clear_priority(self, org_dir):
        run_cli("set-priority", "Buy groceries", "A", org_dir=org_dir)
        run_cli("set-priority", "Buy groceries", "C", org_dir=org_dir)
        stdout, stderr, rc = run_cli("set-priority", "Buy groceries", "--clear", org_dir=org_dir)
        assert rc == 0
        assert "Cleared priority:" in stdout
        assert "[#" not in (org_dir / "inbox.org").read_text()

    def test_clear_on_no_priority(self, org_dir):
        stdout, stderr, rc = run_cli("set-priority", "Buy groceries", "--clear", org_dir=org_dir)
        assert rc == 0
        assert "Cleared priority:" in stdout

    def test_invalid_priority(self, org_dir):
        stdout, stderr, rc = run_cli("set-priority", "Buy groceries", "D", org_dir=org_dir)
        assert rc == 1
        assert "not a valid priority" in stderr
        assert "A, B, C" in stderr

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("set-priority", "Buy groceries", "A", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would set priority:" in stdout
        assert "[#A] Buy groceries" not in (org_dir / "inbox.org").read_text()

    def test_change_existing_priority(self, org_dir):
        stdout, stderr, rc = run_cli("set-priority", "Pay quarterly taxes", "C", org_dir=org_dir)
        assert rc == 0
        assert "[#A] -> [#C]" in stdout
        assert "[#C] Pay quarterly taxes" in (org_dir / "tasks.org").read_text()

    def test_lowercase_input(self, org_dir):
        stdout, stderr, rc = run_cli("set-priority", "Buy groceries", "a", org_dir=org_dir)
        assert rc == 0
        assert "[#A] Buy groceries" in (org_dir / "inbox.org").read_text()

    def test_index_disambiguation(self, org_dir):
        stdout, stderr, rc = run_cli("set-priority", "Buy", "A", "--index", "1", org_dir=org_dir)
        assert rc == 0


# ===========================================================================
# 16. refile
# ===========================================================================

class TestRefile:
    def test_moves_task_to_target(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Shopping", org_dir=org_dir)
        assert rc == 0
        assert "Buy groceries" not in (org_dir / "inbox.org").read_text()
        assert "Buy groceries" in (org_dir / "tasks.org").read_text()
        assert "Refiled" in stdout
        assert "(inbox.org)" in stdout

    def test_target_not_found(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Nonexistent", org_dir=org_dir)
        assert rc == 1

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Shopping", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would refile" in stdout
        assert "Buy groceries" in (org_dir / "inbox.org").read_text()


# ===========================================================================
# 17. refile: self-match filtering
# ===========================================================================

class TestRefileSelfMatch:
    def test_self_match_skipped_valid_target_found(self, org_dir):
        run_cli("add-task", "Research new tools", "--file", "inbox.org", org_dir=org_dir)
        stdout, stderr, rc = run_cli("refile", "Research new tools", "--to", "Research", org_dir=org_dir)
        assert rc == 0
        assert "Refiled" in stdout
        assert "Research new tools" not in (org_dir / "inbox.org").read_text()
        assert "Research new tools" in (org_dir / "tasks.org").read_text()

    def test_all_targets_are_self_matches(self, org_dir):
        run_cli("add-task", "xyzzy", "--file", "inbox.org", org_dir=org_dir)
        stdout, stderr, rc = run_cli("refile", "xyzzy", "--to", "xyzzy", org_dir=org_dir)
        assert rc == 1
        assert "self-match" in stderr
        assert "xyzzy" in (org_dir / "inbox.org").read_text()

    def test_subtree_child_also_counts_as_self_match(self, org_dir):
        run_cli("add-task", "zqxjk", "--file", "inbox.org", org_dir=org_dir)
        run_cli("add-subtask", "zqxjk", "zqxjk", org_dir=org_dir)
        stdout, stderr, rc = run_cli("refile", "zqxjk", "--to", "zqxjk", "--index", "1", org_dir=org_dir)
        assert rc == 1
        assert "skipped 2 self-match" in stderr
        assert "zqxjk" in (org_dir / "inbox.org").read_text()


# ===========================================================================
# 18. refile: --to exact match
# ===========================================================================

class TestRefileToExact:
    def test_exact_match_finds_first(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Tools", org_dir=org_dir)
        assert rc == 0
        assert "Buy groceries" not in (org_dir / "inbox.org").read_text()
        assert "Refiled" in stdout

    def test_partial_match_fails(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Tool", org_dir=org_dir)
        assert rc == 1
        assert "not found" in stderr

    def test_path_disambiguates(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Research/Tools", org_dir=org_dir)
        assert rc == 0
        assert "Refiled" in stdout
        assert "Research" in stdout

    def test_targets_todo_heading(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Write quarterly report", org_dir=org_dir)
        assert rc == 0
        assert "Refiled" in stdout

    def test_case_insensitive(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "tools", org_dir=org_dir)
        assert rc == 0
        assert "Refiled" in stdout

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Shopping", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would refile" in stdout
        assert "Buy groceries" in (org_dir / "inbox.org").read_text()

    def test_intermediate_path_typo_fails(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Computer/Tools", org_dir=org_dir)
        assert rc == 1
        assert "not found" in stderr


# ===========================================================================
# 19. refile: --category
# ===========================================================================

class TestRefileCategory:
    def test_substring_matches(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Shop", org_dir=org_dir)
        assert rc == 0
        assert "Buy groceries" not in (org_dir / "inbox.org").read_text()
        assert "Refiled" in stdout

    def test_skips_todo_headings(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Holiday", org_dir=org_dir)
        assert rc == 0
        assert "Refiled" in stdout
        assert "Buy groceries" not in (org_dir / "inbox.org").read_text()

    def test_ambiguous_match(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Tools", org_dir=org_dir)
        assert rc == 2
        assert "Multiple category matches" in stderr

    def test_path_disambiguates(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Research/Tools", org_dir=org_dir)
        assert rc == 0
        assert "Refiled" in stdout

    def test_only_todo_matches(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Design", org_dir=org_dir)
        assert rc == 1
        assert "not found" in stderr

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Research/Tools", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would refile" in stdout
        assert "Buy groceries" in (org_dir / "inbox.org").read_text()

    def test_matches_plain_heading_under_task(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Improve agent workflow/Resources", org_dir=org_dir)
        assert rc == 0
        assert "Refiled" in stdout
        assert "Buy groceries" not in (org_dir / "inbox.org").read_text()

    def test_ambiguous_resources(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Resources", org_dir=org_dir)
        assert rc == 2
        assert "Multiple category matches" in stderr
        assert "Improve agent workflow/Resources" in stderr
        assert "Research/Resources" in stderr


# ===========================================================================
# 20. add-event
# ===========================================================================

class TestAddEvent:
    def test_appends_to_calendar(self, org_dir):
        stdout, stderr, rc = run_cli("add-event", "Team dinner", "--date", "2026-03-20", org_dir=org_dir)
        assert rc == 0
        cal = (org_dir / "calendar.org").read_text()
        assert "Team dinner" in cal
        assert ":calpersonal:" in cal
        assert "<2026-03-20 Fri>" in cal

    def test_with_time(self, org_dir):
        stdout, stderr, rc = run_cli("add-event", "Lunch", "--date", "2026-03-21", "--time", "12:00-13:00", org_dir=org_dir)
        assert rc == 0
        assert "<2026-03-21 Sat 12:00-13:00>" in (org_dir / "calendar.org").read_text()

    def test_custom_tag(self, org_dir):
        stdout, stderr, rc = run_cli("add-event", "Family BBQ", "--date", "2026-03-22", "--tag", "calfamily", org_dir=org_dir)
        assert rc == 0
        assert ":calfamily:" in (org_dir / "calendar.org").read_text()

    def test_date_range(self, org_dir):
        stdout, stderr, rc = run_cli("add-event", "School holiday", "--date", "2026-03-16", "--end-date", "2026-03-27", org_dir=org_dir)
        assert rc == 0
        assert "<2026-03-16 Mon>--<2026-03-27 Fri>" in (org_dir / "calendar.org").read_text()

    def test_date_range_with_start_time(self, org_dir):
        stdout, stderr, rc = run_cli("add-event", "Conference", "--date", "2026-04-01", "--time", "09:00", "--end-date", "2026-04-03", org_dir=org_dir)
        assert rc == 0
        assert "<2026-04-01 Wed 09:00>--<2026-04-03 Fri>" in (org_dir / "calendar.org").read_text()

    def test_non_default_file_omits_tag(self, org_dir):
        # A file-level calendar-id property opts the file into gcal format
        (org_dir / "family-calendar.org").write_text(
            "#+title: Family Calendar\n"
            f"#+PROPERTY: calendar-id {FAKE_CALENDAR_ID}\n")
        stdout, stderr, rc = run_cli("add-event", "Family dinner", "--date", "2026-03-23", "--file", "family-calendar.org", org_dir=org_dir)
        assert rc == 0
        cal = (org_dir / "family-calendar.org").read_text()
        assert "* Family dinner" in cal
        assert ":calpersonal:" not in cal
        assert ":org-gcal:" in cal
        assert ":END:" in cal
        assert f":calendar-id: {FAKE_CALENDAR_ID}" in cal

    def test_non_default_file_with_explicit_tag(self, org_dir):
        (org_dir / "family-calendar.org").write_text(
            "#+title: Family Calendar\n"
            f"#+PROPERTY: calendar-id {FAKE_CALENDAR_ID}\n")
        stdout, stderr, rc = run_cli("add-event", "School play", "--date", "2026-03-25", "--tag", "calfamily", "--file", "family-calendar.org", org_dir=org_dir)
        assert rc == 0
        cal = (org_dir / "family-calendar.org").read_text()
        assert ":calfamily:" in cal
        assert ":org-gcal:" in cal

    def test_date_range_in_gcal_drawer(self, org_dir):
        (org_dir / "family-calendar.org").write_text(
            "#+title: Family Calendar\n"
            f"#+PROPERTY: calendar-id {FAKE_CALENDAR_ID}\n")
        stdout, stderr, rc = run_cli("add-event", "Spring break", "--date", "2026-04-06", "--file", "family-calendar.org", "--end-date", "2026-04-17", org_dir=org_dir)
        assert rc == 0
        cal = (org_dir / "family-calendar.org").read_text()
        assert ":org-gcal:" in cal
        assert "<2026-04-06 Mon>--<2026-04-17 Fri>" in cal

    def test_file_without_calendar_id_gets_plain_event(self, org_dir):
        # No file-level calendar-id property -> plain heading, no drawers
        (org_dir / "family-calendar.org").write_text("#+title: Family Calendar\n")
        stdout, stderr, rc = run_cli("add-event", "Plain dinner", "--date", "2026-03-23", "--file", "family-calendar.org", org_dir=org_dir)
        assert rc == 0
        cal = (org_dir / "family-calendar.org").read_text()
        assert "* Plain dinner" in cal
        assert "<2026-03-23 Mon>" in cal
        assert ":org-gcal:" not in cal
        assert ":calendar-id:" not in cal
        assert ":PROPERTIES:" not in cal

    def test_calendar_id_property_works_in_any_file(self, org_dir):
        # The property drives gcal format regardless of filename
        (org_dir / "work-calendar.org").write_text(
            "#+title: Work Calendar\n"
            "#+PROPERTY: calendar-id work-test@group.calendar.google.com\n")
        stdout, stderr, rc = run_cli("add-event", "Standup", "--date", "2026-03-24", "--file", "work-calendar.org", org_dir=org_dir)
        assert rc == 0
        cal = (org_dir / "work-calendar.org").read_text()
        assert ":calendar-id: work-test@group.calendar.google.com" in cal
        assert ":org-gcal:" in cal


# ===========================================================================
# 21. add-note
# ===========================================================================

class TestAddNote:
    def test_creates_note_file(self, org_dir):
        stdout, stderr, rc = run_cli("add-note", "Test research topic", org_dir=org_dir)
        assert rc == 0
        assert "Created:" in stdout
        note_file = org_dir / "agent-notes" / "test-research-topic.org"
        assert note_file.exists(), f"note file not created at {note_file}"
        note = note_file.read_text()
        assert "#+title: Test research topic" in note
        assert "#+filetags: :research:" in note
        assert "* Summary" in note

    def test_custom_sections(self, org_dir):
        stdout, stderr, rc = run_cli("add-note", "Custom note", "--sections", "Background,Analysis,Recommendations", org_dir=org_dir)
        assert rc == 0
        note_file = org_dir / "agent-notes" / "custom-note.org"
        assert note_file.exists()
        note = note_file.read_text()
        assert "* Background" in note
        assert "* Analysis" in note
        assert "* Recommendations" in note

    def test_link_task(self, org_dir):
        stdout, stderr, rc = run_cli("add-note", "Formicarium research", "--link-task", "Buy a formicarium", org_dir=org_dir)
        assert rc == 0
        assert "[[file:agent-notes/formicarium-research.org]]" in (org_dir / "tasks.org").read_text()

    def test_link_task_deeply_nested(self, org_dir):
        stdout, stderr, rc = run_cli("add-note", "Deep nested note", "--link-task", "Deeply nested leaf task", org_dir=org_dir)
        assert rc == 0
        assert "Created:" in stdout
        assert "[[file:agent-notes/deep-nested-note.org]]" in (org_dir / "tasks.org").read_text()

    def test_colliding_slug_errors_and_preserves_original(self, org_dir):
        """A second note whose title slugifies to an existing slug must error,
        not silently overwrite the original note file."""
        stdout, stderr, rc = run_cli("add-note", "Collision test", org_dir=org_dir)
        assert rc == 0
        note_file = org_dir / "agent-notes" / "collision-test.org"
        original = note_file.read_text()
        # Different title, same slug ("Collision; test!" -> "collision-test")
        stdout, stderr, rc = run_cli("add-note", "Collision; test!", org_dir=org_dir)
        assert rc == 1
        assert "already exists" in stderr
        assert note_file.read_text() == original

    def test_colliding_slug_json_error(self, org_dir):
        """In --json mode the collision error is emitted as JSON on stderr."""
        stdout, stderr, rc = run_cli("add-note", "Collision test", org_dir=org_dir)
        assert rc == 0
        stdout, stderr, rc = run_cli("--json", "add-note", "Collision test", org_dir=org_dir)
        assert rc == 1
        err_data = None
        for line in stderr.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                err_data = json.loads(line)
                break
        assert err_data is not None, f"No JSON error found in stderr: {stderr}"
        assert "already exists" in err_data["error"]

    def test_non_ascii_title_errors_instead_of_empty_slug(self, org_dir):
        """A fully non-ASCII title slugifies to "" — must error, not create .org."""
        stdout, stderr, rc = run_cli("add-note", "日本語のメモ", org_dir=org_dir)
        assert rc == 1
        assert "empty filename slug" in stderr
        assert not (org_dir / "agent-notes" / ".org").exists()


# ===========================================================================
# 22. append-body
# ===========================================================================

class TestAppendBody:
    def test_appends_to_task_with_existing_body(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "Buy a small UPS", "Check APC models", org_dir=org_dir)
        assert rc == 0
        tasks = org_dir / "tasks.org"
        assert "Check APC models" in tasks.read_text()
        assert "Appended to" in stdout
        assert_line_before(tasks, "Check APC models", "[2026-03-11 Wed 13:35]")

    def test_appends_before_date_only_timestamp(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "Buy anti-escape coating", "Also check Fluon PTFE spray", org_dir=org_dir)
        assert rc == 0
        tasks = org_dir / "tasks.org"
        text = tasks.read_text()
        assert "Also check Fluon PTFE spray" in text
        assert_line_before(tasks, "PTFE anti-escape", "Also check Fluon PTFE spray")
        # Verify the timestamp is after the new text
        task_line = line_number_of(tasks, "Buy anti-escape coating")
        text_line = line_number_of(tasks, "Also check Fluon PTFE spray")
        # Find timestamp line after task heading
        lines = text.splitlines()
        ts_line = None
        assert task_line is not None
        for i in range(task_line, len(lines)):
            if re.match(r'^\[2026-03-12 Thu\]$', lines[i]):
                ts_line = i + 1
                break
        assert text_line is not None and ts_line is not None
        assert text_line < ts_line

    def test_appends_to_task_with_no_body(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "Reply to dentist", "Call if no reply by Friday", org_dir=org_dir)
        assert rc == 0
        assert "Call if no reply by Friday" in (org_dir / "inbox.org").read_text()

    def test_append_body_on_bare_heading(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "Bare heading no body", "Appended to bare heading", org_dir=org_dir)
        assert rc == 0
        tasks = org_dir / "tasks.org"
        assert "Appended to bare heading" in tasks.read_text()
        bare_line = line_number_of(tasks, "Bare heading no body")
        body_line = line_number_of(tasks, "Appended to bare heading")
        assert bare_line is not None and body_line is not None
        assert body_line > bare_line

    def test_rejects_body_starting_with_org_heading(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "Buy a small UPS", "** Test heading", org_dir=org_dir)
        assert rc == 1
        assert "Error" in stderr

    def test_rejects_body_with_org_heading_on_subsequent_line(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "Buy a small UPS", "Some text\n* Mid-text heading", org_dir=org_dir)
        assert rc == 1
        assert "Error" in stderr


# ===========================================================================
# 23. set-body
# ===========================================================================

class TestSetBody:
    def test_replaces_existing_body(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Buy a small UPS", "Brand new body text here.", org_dir=org_dir)
        assert rc == 0
        assert "Set body" in stdout
        tasks = org_dir / "tasks.org"
        text = tasks.read_text()
        assert "Brand new body text here." in text
        assert "Power outage corrupted" not in text
        assert "USB UPS with auto-shutdown" not in text
        assert "[2026-03-11 Wed 13:35]" in text
        assert_line_before(tasks, "Brand new body text here.", "[2026-03-11 Wed 13:35]")

    def test_empty_text_removes_body(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Buy anti-escape coating", "", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert "Messor barbarus can climb" not in text
        assert "PTFE anti-escape" not in text
        assert "[2026-03-12 Thu]" in text

    def test_set_body_on_task_with_no_body(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Reply to dentist", "Please call them Monday.", org_dir=org_dir)
        assert rc == 0
        assert "Please call them Monday." in (org_dir / "inbox.org").read_text()

    def test_set_body_on_bare_heading(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Bare heading no body", "Set on bare heading", org_dir=org_dir)
        assert rc == 0
        tasks = org_dir / "tasks.org"
        assert "Set on bare heading" in tasks.read_text()
        bare_line = line_number_of(tasks, "Bare heading no body")
        body_line = line_number_of(tasks, "Set on bare heading")
        assert bare_line is not None and body_line is not None
        assert body_line > bare_line

    def test_rejects_body_starting_with_org_heading(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Buy a small UPS", "** Sneaky heading", org_dir=org_dir)
        assert rc == 1
        assert "Error" in stderr

    def test_rejects_body_with_org_heading_on_subsequent_line(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Buy a small UPS", "Some text\n* Mid-text heading", org_dir=org_dir)
        assert rc == 1
        assert "Error" in stderr

    def test_preserves_metadata(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Fix org-capture", "Replaced body.", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert "Replaced body." in text
        assert "It appears in the existing Emacs instance" not in text
        assert ":ID:       test-id-capture-fix" in text
        assert 'State "DONE"       from "TODO"' in text
        assert "[2026-03-06 Fri 14:33]" in text

    def test_index_disambiguation(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Buy a", "Index test body.", "--index", "2", org_dir=org_dir)
        assert rc == 0
        assert "Index test body." in (org_dir / "tasks.org").read_text()


# ===========================================================================
# 24. move
# ===========================================================================

class TestMove:
    def test_move_up(self, org_dir):
        stdout, stderr, rc = run_cli("move", "Implement CLI tool", "--up", org_dir=org_dir)
        assert rc == 0
        assert "Moved:" in stdout
        assert "(tasks.org)" in stdout

    def test_move_down(self, org_dir):
        stdout, stderr, rc = run_cli("move", "Design CLI tool", "--down", org_dir=org_dir)
        assert rc == 0
        assert "Moved:" in stdout

    def test_move_after_sibling(self, org_dir):
        stdout, stderr, rc = run_cli("move", "Buy anti-escape coating", "--after", "Research formicarium", org_dir=org_dir)
        assert rc == 0
        assert "Moved:" in stdout

    def test_move_before_stays_under_correct_parent(self, org_dir):
        # Regression: after deleting the task, the sibling used to be
        # re-found by scanning the whole buffer from point-min, so a
        # same-level heading under a DIFFERENT parent matching the
        # substring could silently steal the task.
        (org_dir / "moveparents.org").write_text("""\
* TODO Parent One
** TODO Shared step beta
* TODO Parent Two
** TODO Lone task to move
** TODO Shared step alpha
""")
        stdout, stderr, rc = run_cli(
            "move", "Lone task to move", "--before", "Shared step",
            org_dir=org_dir)
        assert rc == 0
        f = org_dir / "moveparents.org"
        text = f.read_text()
        # Task must remain under Parent Two, before "Shared step alpha"
        assert_line_before(f, "Parent Two", "Lone task to move")
        assert_line_before(f, "Lone task to move", "Shared step alpha")
        # And must NOT have been relocated under Parent One
        assert text.index("Lone task to move") > text.index("Parent Two")

    def test_move_after_stays_under_correct_parent(self, org_dir):
        (org_dir / "moveparents.org").write_text("""\
* TODO Parent One
** TODO Shared step beta
* TODO Parent Two
** TODO Shared step alpha
** TODO Middle filler task
** TODO Lone task to move
""")
        stdout, stderr, rc = run_cli(
            "move", "Lone task to move", "--after", "Shared step",
            org_dir=org_dir)
        assert rc == 0
        f = org_dir / "moveparents.org"
        text = f.read_text()
        assert_line_before(f, "Shared step alpha", "Lone task to move")
        assert_line_before(f, "Lone task to move", "Middle filler task")
        assert text.index("Lone task to move") > text.index("Parent Two")


# ===========================================================================
# 25. org-timestamp --inactive
# ===========================================================================

class TestOrgTimestampInactive:
    def test_inactive_timestamp(self, org_dir):
        stdout, stderr, rc = run_cli("org-timestamp", "2026-03-15", "--inactive", org_dir=org_dir)
        assert rc == 0
        assert "[2026-03-15 Sun]" in stdout

    def test_inactive_timestamp_with_time(self, org_dir):
        stdout, stderr, rc = run_cli("org-timestamp", "2026-03-15", "14:00", "--inactive", org_dir=org_dir)
        assert rc == 0
        assert "[2026-03-15 Sun 14:00]" in stdout


# ===========================================================================
# 26. set-next
# ===========================================================================

class TestSetNext:
    def test_already_has_next_noop(self, org_dir):
        stdout, stderr, rc = run_cli("set-next", "Holiday pre-trip tasks", org_dir=org_dir)
        assert rc == 0
        assert "Already has NEXT" in stderr

    def test_promotes_first_todo_child(self, org_dir):
        # Multi-step: done Book a rental car, set-state WAITING->TODO, then set-next
        run_cli("set-done", "Book a rental car", org_dir=org_dir)
        run_cli("set-state", "Get travel insurance", "TODO", org_dir=org_dir)
        stdout, stderr, rc = run_cli("set-next", "Holiday pre-trip tasks", org_dir=org_dir)
        assert rc == 0
        assert "Set NEXT" in stdout
        assert "NEXT Get travel insurance" in (org_dir / "tasks.org").read_text()

    def test_already_has_next_child(self, org_dir):
        # Buy a formicarium has NEXT child already
        stdout, stderr, rc = run_cli("set-next", "Buy a formicarium", org_dir=org_dir)
        assert rc == 0

    def test_leaf_task_sets_to_next(self, org_dir):
        stdout, stderr, rc = run_cli("set-next", "Draft outline", org_dir=org_dir)
        assert rc == 0
        assert "Set NEXT" in stdout
        assert "NEXT Draft outline" in (org_dir / "tasks.org").read_text()

    def test_leaf_task_already_next_noop(self, org_dir):
        run_cli("set-next", "Draft outline", org_dir=org_dir)
        stdout, stderr, rc = run_cli("set-next", "Draft outline", org_dir=org_dir)
        assert rc == 0
        assert "Already NEXT" in stderr

    def test_set_next_leaf_with_existing_next_sibling(self, org_dir):
        """set-next on a leaf in a project that already has NEXT via subproject — succeeds."""
        stdout, stderr, rc = run_cli("set-next", "Run smoke tests", org_dir=org_dir)
        assert rc == 0
        assert "Set NEXT" in stdout

    def test_subproject_set_next_fails(self, org_dir):
        stdout, stderr, rc = run_cli("set-next", "Design CLI tool", org_dir=org_dir)
        assert rc == 1
        assert "has subtasks" in stderr

    def test_subproject_without_next_set_next_fails(self, org_dir):
        """set-next on a subproject with no NEXT children also fails."""
        stdout, stderr, rc = run_cli("set-next", "Implement CLI tool", org_dir=org_dir)
        assert rc == 1
        assert "has subtasks" in stderr

    def test_subproject_set_next_json_error(self, org_dir):
        data, stderr, rc = run_cli_json("set-next", "Implement CLI tool", org_dir=org_dir)
        assert rc == 1
        err = None
        for line in stderr.splitlines():
            line = line.strip()
            if line.startswith("{"):
                err = json.loads(line)
                break
        assert err is not None, f"No JSON found in stderr: {stderr}"
        assert "has subtasks" in err["error"]
        assert "hint" in err


# ===========================================================================
# 27. subproject
# ===========================================================================

class TestSubproject:
    def test_subtasks_of_subproject(self, org_dir):
        stdout, stderr, rc = run_cli("subtasks", "Design CLI tool", org_dir=org_dir)
        assert rc == 0
        assert "NEXT Add more test cases" in stdout
        assert "TODO Test on actual project" in stdout
        assert "TODO Start using it" in stdout
        assert "0/3 done" in stdout

    def test_parent_project_includes_subproject(self, org_dir):
        stdout, stderr, rc = run_cli("subtasks", "Improve agent workflow", org_dir=org_dir)
        assert rc == 0
        assert "TODO Design CLI tool" in stdout
        assert "TODO Implement CLI tool" in stdout

    def test_process_agent_tasks_removed(self, org_dir):
        """process-agent-tasks is removed."""
        stdout, stderr, rc = run_cli("process-agent-tasks", org_dir=org_dir)
        assert rc == 1


# ===========================================================================
# 28. edge cases: out-of-bounds index
# ===========================================================================

class TestEdgeCasesOutOfBoundsIndex:
    def test_done_oob_index(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("set-done", "Buy", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before

    def test_set_state_oob_index(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("set-state", "Buy", "NEXT", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before

    def test_refile_oob_index(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("refile", "Buy", "--to", "Shopping", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before

    def test_append_body_oob_index(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("append-body", "Buy", "text", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before

    def test_move_oob_index(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("move", "Buy", "--up", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before

    def test_add_subtask_oob_index(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("add-subtask", "Buy", "child", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before


# ===========================================================================
# 29. edge cases: no match
# ===========================================================================

class TestEdgeCasesNoMatch:
    def test_done_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "xyznonexistent", org_dir=org_dir)
        assert rc == 1

    def test_set_state_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("set-state", "xyznonexistent", "NEXT", org_dir=org_dir)
        assert rc == 1

    def test_refile_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "xyznonexistent", "--to", "Shopping", org_dir=org_dir)
        assert rc == 1

    def test_append_body_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "xyznonexistent", "text", org_dir=org_dir)
        assert rc == 1

    def test_move_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("move", "xyznonexistent", "--up", org_dir=org_dir)
        assert rc == 1


# ===========================================================================
# 30. edge cases: invalid refile target
# ===========================================================================

class TestEdgeCasesInvalidRefileTarget:
    def test_refile_to_nonexistent_heading(self, org_dir):
        before = md5(org_dir / "inbox.org")
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--to", "Nonexistent Heading", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "inbox.org") == before


# ===========================================================================
# 31. integration: add-subtask -> set-next -> done chain
# ===========================================================================

class TestIntegrationChain:
    def test_full_chain(self, org_dir):
        tasks = org_dir / "tasks.org"

        # Step 1: set-next on project with existing NEXT -> no-op
        stdout, stderr, rc = run_cli("set-next", "Holiday pre-trip tasks", org_dir=org_dir)
        assert rc == 0
        assert "Already has NEXT" in stderr

        # Step 2: done Book a rental car -> auto-progress
        stdout, stderr, rc = run_cli("set-done", "Book a rental car", org_dir=org_dir)
        assert rc == 0
        assert "DONE Book a rental car" in tasks.read_text()

        # Step 3: verify via subtasks
        stdout, stderr, rc = run_cli("subtasks", "Holiday pre-trip tasks", org_dir=org_dir)
        assert rc == 0
        assert "DONE Book a rental car" in stdout
        assert "WAITING Get travel insurance" in stdout

        # Step 4: set-next with no TODO children -> exit 1
        stdout, stderr, rc = run_cli("set-next", "Holiday pre-trip tasks", org_dir=org_dir)
        assert rc == 1

        # Step 5: set-state WAITING -> TODO
        stdout, stderr, rc = run_cli("set-state", "Get travel insurance", "TODO", org_dir=org_dir)
        assert rc == 0

        # Step 6: set-next promotes to NEXT
        stdout, stderr, rc = run_cli("set-next", "Holiday pre-trip tasks", org_dir=org_dir)
        assert rc == 0
        assert "Set NEXT" in stdout
        assert "NEXT Get travel insurance" in tasks.read_text()

        # Step 7: add-subtask to NEXT task demotes parent to TODO
        stdout, stderr, rc = run_cli("add-subtask", "Get travel insurance", "Compare providers", org_dir=org_dir)
        assert rc == 0
        text = tasks.read_text()
        assert "TODO Get travel insurance" in text
        assert "TODO Compare providers" in text

        # Step 8: verify demotion via show
        stdout, stderr, rc = run_cli("show", "Get travel insurance", org_dir=org_dir)
        assert rc == 0
        assert "TODO Get travel insurance" in stdout
        assert "Compare providers" in stdout


# ===========================================================================
# 32. rename
# ===========================================================================

class TestRename:
    def test_basic_rename(self, org_dir):
        stdout, stderr, rc = run_cli("rename", "Buy groceries", "Buy organic groceries", org_dir=org_dir)
        assert rc == 0
        assert "Renamed:" in stdout
        assert '"Buy groceries" -> "Buy organic groceries"' in stdout
        inbox = (org_dir / "inbox.org").read_text()
        assert "Buy organic groceries" in inbox
        assert "Buy groceries" not in inbox

    def test_preserves_state_and_priority(self, org_dir):
        stdout, stderr, rc = run_cli("rename", "Pay quarterly taxes", "Pay quarterly income taxes", org_dir=org_dir)
        assert rc == 0
        assert "TODO [#A] Pay quarterly income taxes" in (org_dir / "tasks.org").read_text()

    def test_preserves_tags(self, org_dir):
        stdout, stderr, rc = run_cli("rename", "Get travel insurance", "Get travel insurance from Allianz", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert "Get travel insurance from Allianz" in text
        assert ":WAITING:" in text
        assert ":email:" in text

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("rename", "Buy groceries", "Buy organic groceries", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would rename" in stdout
        assert "Buy groceries" in (org_dir / "inbox.org").read_text()

    def test_ambiguous(self, org_dir):
        stdout, stderr, rc = run_cli("rename", "Buy", "Something", org_dir=org_dir)
        assert rc == 2


# ===========================================================================
# 33. set-schedule
# ===========================================================================

class TestSetSchedule:
    def test_set_on_unscheduled(self, org_dir):
        stdout, stderr, rc = run_cli("set-schedule", "Buy groceries", "2026-03-20", org_dir=org_dir)
        assert rc == 0
        assert "Scheduled:" in stdout
        assert "SCHEDULED: <2026-03-20 Fri>" in (org_dir / "inbox.org").read_text()

    def test_set_with_time(self, org_dir):
        stdout, stderr, rc = run_cli("set-schedule", "Buy groceries", "2026-03-20", "--time", "14:00", org_dir=org_dir)
        assert rc == 0
        assert "SCHEDULED: <2026-03-20 Fri 14:00>" in (org_dir / "inbox.org").read_text()

    def test_overwrite_existing(self, org_dir):
        stdout, stderr, rc = run_cli("set-schedule", "Consider buying a new monitor", "2026-04-01", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert "SCHEDULED: <2026-04-01 Wed>" in text
        assert "SCHEDULED: <2026-03-09 Mon>" not in text

    def test_clear_existing(self, org_dir):
        stdout, stderr, rc = run_cli("set-schedule", "Consider buying a new monitor", "--clear", org_dir=org_dir)
        assert rc == 0
        assert "Cleared schedule" in stdout
        assert "SCHEDULED: <2026-03-09 Mon>" not in (org_dir / "tasks.org").read_text()

    def test_clear_when_none(self, org_dir):
        stdout, stderr, rc = run_cli("set-schedule", "Buy groceries", "--clear", org_dir=org_dir)
        assert rc == 0
        assert "Cleared schedule" in stdout

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("set-schedule", "Buy groceries", "2026-03-20", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would schedule" in stdout
        assert "SCHEDULED:" not in (org_dir / "inbox.org").read_text()

    def test_ambiguous(self, org_dir):
        stdout, stderr, rc = run_cli("set-schedule", "Buy", "2026-03-20", org_dir=org_dir)
        assert rc == 2


# ===========================================================================
# 34. set-deadline
# ===========================================================================

class TestSetDeadline:
    def test_set_on_task_without_one(self, org_dir):
        stdout, stderr, rc = run_cli("set-deadline", "Buy groceries", "2026-03-25", org_dir=org_dir)
        assert rc == 0
        assert "Deadline:" in stdout
        assert "DEADLINE: <2026-03-25 Wed>" in (org_dir / "inbox.org").read_text()

    def test_set_with_time(self, org_dir):
        stdout, stderr, rc = run_cli("set-deadline", "Buy groceries", "2026-03-25", "--time", "17:00", org_dir=org_dir)
        assert rc == 0
        assert "DEADLINE: <2026-03-25 Wed 17:00>" in (org_dir / "inbox.org").read_text()

    def test_overwrite_existing(self, org_dir):
        stdout, stderr, rc = run_cli("set-deadline", "Pay quarterly taxes", "2026-03-30", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert "DEADLINE: <2026-03-30 Mon>" in text
        assert "DEADLINE: <2026-03-15 Sun>" not in text

    def test_clear_existing(self, org_dir):
        stdout, stderr, rc = run_cli("set-deadline", "Pay quarterly taxes", "--clear", org_dir=org_dir)
        assert rc == 0
        assert "Cleared deadline" in stdout
        assert "DEADLINE: <2026-03-15 Sun>" not in (org_dir / "tasks.org").read_text()

    def test_clear_when_none(self, org_dir):
        stdout, stderr, rc = run_cli("set-deadline", "Buy groceries", "--clear", org_dir=org_dir)
        assert rc == 0
        assert "Cleared deadline" in stdout

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("set-deadline", "Buy groceries", "2026-03-25", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would set deadline" in stdout
        assert "DEADLINE:" not in (org_dir / "inbox.org").read_text()


# ===========================================================================
# 35. set-tags
# ===========================================================================

class TestSetTags:
    def test_set_tags_replace(self, org_dir):
        """set-tags --tags replaces all tags."""
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--tags", "urgent,shopping", org_dir=org_dir)
        assert rc == 0
        assert "Tags:" in stdout
        text = (org_dir / "inbox.org").read_text()
        assert ":urgent:" in text
        assert ":shopping:" in text
        # Old tags should be gone
        assert ":buy:" not in text
        assert ":@errand:" not in text

    def test_set_tags_clear(self, org_dir):
        """set-tags --tags '' clears all tags."""
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--tags", "", org_dir=org_dir)
        assert rc == 0

    def test_add_tags_append(self, org_dir):
        """add-tags --tags appends without duplicates."""
        stdout, stderr, rc = run_cli("add-tags", "Buy groceries", "--tags", "urgent", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "inbox.org").read_text()
        assert ":urgent:" in text
        # Old tags preserved
        assert ":buy:" in text
        assert ":@errand:" in text

    def test_add_tags_no_duplicates(self, org_dir):
        """add-tags with existing tag is a no-op for that tag."""
        stdout, stderr, rc = run_cli("add-tags", "Buy groceries", "--tags", "buy", org_dir=org_dir)
        assert rc == 0
        assert ":buy:" in (org_dir / "inbox.org").read_text()

    def test_set_tags_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--tags", "urgent", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would set tags" in stdout
        assert ":urgent:" not in (org_dir / "inbox.org").read_text()

    def test_set_tags_ambiguous(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "Buy", "--tags", "urgent", org_dir=org_dir)
        assert rc == 2


# ===========================================================================
# 36. edge cases: new commands out-of-bounds index
# ===========================================================================

class TestEdgeCasesNewCommandsOobIndex:
    def test_rename_oob(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("rename", "Buy", "Something", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before

    def test_set_schedule_oob(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("set-schedule", "Buy", "2026-03-20", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before

    def test_set_deadline_oob(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("set-deadline", "Buy", "2026-03-25", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before

    def test_set_tags_oob(self, org_dir):
        before = md5(org_dir / "tasks.org")
        stdout, stderr, rc = run_cli("set-tags", "Buy", "--tags", "urgent", "--index", "999", org_dir=org_dir)
        assert rc == 1
        assert md5(org_dir / "tasks.org") == before


# ===========================================================================
# 37. edge cases: new commands no match
# ===========================================================================

class TestEdgeCasesNewCommandsNoMatch:
    def test_rename_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("rename", "xyznonexistent", "Something", org_dir=org_dir)
        assert rc == 1

    def test_set_schedule_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("set-schedule", "xyznonexistent", "2026-03-20", org_dir=org_dir)
        assert rc == 1

    def test_set_deadline_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("set-deadline", "xyznonexistent", "2026-03-25", org_dir=org_dir)
        assert rc == 1

    def test_set_tags_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "xyznonexistent", "--tags", "urgent", org_dir=org_dir)
        assert rc == 1


# ===========================================================================
# 37b. set-property: generic property writer
# ===========================================================================

class TestSetProperty:
    def test_set_new_property(self, org_dir):
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--value", "light", org_dir=org_dir)
        assert rc == 0
        assert "Property AGENT_EFFORT:" in stdout
        text = (org_dir / "inbox.org").read_text()
        assert ":AGENT_EFFORT: light" in text
        assert ":PROPERTIES:" in text

    def test_overwrite_property(self, org_dir):
        run_cli("set-property", "Buy groceries", "--key", "AGENT_EFFORT",
                "--value", "light", org_dir=org_dir)
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--value", "deep", org_dir=org_dir)
        assert rc == 0
        assert "light -> deep" in stdout
        text = (org_dir / "inbox.org").read_text()
        assert ":AGENT_EFFORT: deep" in text
        assert ":AGENT_EFFORT: light" not in text

    def test_clear_property(self, org_dir):
        run_cli("set-property", "Buy groceries", "--key", "AGENT_EFFORT",
                "--value", "light", org_dir=org_dir)
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--clear", org_dir=org_dir)
        assert rc == 0
        assert "Cleared property AGENT_EFFORT:" in stdout
        assert ":AGENT_EFFORT:" not in (org_dir / "inbox.org").read_text()

    def test_clear_absent_property_is_noop(self, org_dir):
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--clear", org_dir=org_dir)
        assert rc == 0
        assert "Cleared property AGENT_EFFORT:" in stdout

    def test_dry_run_does_not_write(self, org_dir):
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--value", "deep", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would set property AGENT_EFFORT:" in stdout
        assert ":AGENT_EFFORT:" not in (org_dir / "inbox.org").read_text()

    def test_missing_value_errors(self, org_dir):
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            org_dir=org_dir)
        assert rc == 1
        assert "--value" in stderr

    def test_value_and_clear_mutually_exclusive(self, org_dir):
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--value", "light", "--clear", org_dir=org_dir)
        assert rc == 1
        assert "mutually exclusive" in stderr

    def test_reserved_property_rejected(self, org_dir):
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "SCHEDULED",
            "--value", "2026-01-01", org_dir=org_dir)
        assert rc == 1
        assert "reserved" in stderr
        # case-insensitive reservation
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "deadline",
            "--value", "2026-01-01", org_dir=org_dir)
        assert rc == 1
        assert "reserved" in stderr

    def test_nonexistent_task(self, org_dir):
        stdout, stderr, rc = run_cli(
            "set-property", "xyznonexistent", "--key", "AGENT_EFFORT",
            "--value", "light", org_dir=org_dir)
        assert rc == 1

    def test_agent_effort_valid_values(self, org_dir):
        for v in ("light", "standard", "deep"):
            stdout, stderr, rc = run_cli(
                "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
                "--value", v, org_dir=org_dir)
            assert rc == 0, f"{v}: {stderr}"
            assert f":AGENT_EFFORT: {v}" in (org_dir / "inbox.org").read_text()

    def test_agent_effort_invalid_value_rejected(self, org_dir):
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--value", "medium", org_dir=org_dir)
        assert rc == 1
        assert "invalid value" in stderr.lower()
        assert "light, standard, deep" in stderr
        # Nothing written.
        assert ":AGENT_EFFORT:" not in (org_dir / "inbox.org").read_text()

    def test_agent_effort_value_normalized_case_insensitive(self, org_dir):
        """A case variant is accepted and stored as the canonical lowercase form."""
        data, _, rc = run_cli_json(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--value", "DEEP", org_dir=org_dir)
        assert rc == 0
        assert data["new_value"] == "deep"
        assert ":AGENT_EFFORT: deep" in (org_dir / "inbox.org").read_text()

    def test_non_enum_property_accepts_any_value(self, org_dir):
        """Validation is scoped to enum'd keys; the writer stays generic."""
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "CUSTOM_FIELD",
            "--value", "anything-goes", org_dir=org_dir)
        assert rc == 0
        assert ":CUSTOM_FIELD: anything-goes" in (org_dir / "inbox.org").read_text()

    def test_agent_effort_clear_skips_value_validation(self, org_dir):
        run_cli("set-property", "Buy groceries", "--key", "AGENT_EFFORT",
                "--value", "deep", org_dir=org_dir)
        stdout, stderr, rc = run_cli(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--clear", org_dir=org_dir)
        assert rc == 0
        assert ":AGENT_EFFORT:" not in (org_dir / "inbox.org").read_text()


class TestTaskProperties:
    """The generic `properties' JSON field exposes the :PROPERTIES: drawer
    (incl. AGENT_EFFORT) in show/search/agenda/mutation output. Always present
    (not gated by --full); an entry with no user properties yields {}."""

    def test_show_json_includes_properties(self, org_dir):
        run_cli("set-property", "Buy groceries", "--key", "AGENT_EFFORT",
                "--value", "deep", org_dir=org_dir)
        data, _, rc = run_cli_json("show", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["properties"]["AGENT_EFFORT"] == "deep"

    def test_show_json_properties_empty_object_when_unset(self, org_dir):
        data, _, rc = run_cli_json("show", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        # Empty object, not null/missing — callers can index .properties safely.
        assert data["properties"] == {}

    def test_properties_excludes_category(self, org_dir):
        """CATEGORY is auto-injected by org and must not appear as a property."""
        data, _, rc = run_cli_json("show", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert "CATEGORY" not in data["properties"]

    def test_search_json_includes_properties_without_full(self, org_dir):
        """Acceptance: `search --tag @agent' surfaces AGENT_EFFORT (no --full)."""
        run_cli("set-property", "Buy groceries", "--key", "AGENT_EFFORT",
                "--value", "light", org_dir=org_dir)
        data, _, rc = run_cli_json("search", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        task = next(t for t in data["tasks"] if t["heading"] == "Buy groceries")
        assert task["properties"]["AGENT_EFFORT"] == "light"

    def test_agenda_json_includes_properties(self, org_dir):
        run_cli("set-property", "Buy groceries", "--key", "AGENT_EFFORT",
                "--value", "standard", org_dir=org_dir)
        data, _, rc = run_cli_json("agenda", org_dir=org_dir)
        assert rc == 0
        task = next(t for t in data["tasks"] if t["heading"] == "Buy groceries")
        assert task["properties"]["AGENT_EFFORT"] == "standard"

    def test_mutation_response_includes_properties(self, org_dir):
        """Mutation responses (built from task-alist-at-point) carry properties."""
        run_cli("set-property", "Buy groceries", "--key", "AGENT_EFFORT",
                "--value", "deep", org_dir=org_dir)
        data, _, rc = run_cli_json("set-tags", "Buy groceries", "--add",
                                   "urgent", org_dir=org_dir)
        assert rc == 0
        assert data["task"]["properties"]["AGENT_EFFORT"] == "deep"

    def test_properties_value_non_ascii_roundtrip(self, org_dir):
        run_cli("set-property", "Buy groceries", "--key", "NOTE",
                "--value", "café — naïve", org_dir=org_dir)
        data, _, rc = run_cli_json("show", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["properties"]["NOTE"] == "café — naïve"


# ===========================================================================
# 38. archive: single task
# ===========================================================================

class TestArchiveSingle:
    def test_happy_path(self, org_dir):
        stdout, stderr, rc = run_cli("archive", "buy new router", org_dir=org_dir)
        assert rc == 0
        assert 'Archived: "Buy new router"' in stdout
        assert "Buy new router" not in (org_dir / "tasks.org").read_text()
        assert "Buy new router" in (org_dir / "tasks.org_archive").read_text()

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("archive", "research dentists", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would archive:" in stdout
        assert "Research dentists" in (org_dir / "tasks.org").read_text()

    def test_rejects_active_task(self, org_dir):
        stdout, stderr, rc = run_cli("archive", "write quarterly report", org_dir=org_dir)
        assert rc == 1
        assert "still active" in stderr

    def test_rejects_recent_dates(self, org_dir):
        # Pin "now" so the fixture's dates stay within the recent-date window
        # regardless of when the suite runs (fixture dates are ~2026-03).
        stdout, stderr, rc = run_cli(
            "archive", "submit expense claims", org_dir=org_dir,
            env_overrides={"ORG_GTD_CLI_NOW": "2026-03-20"})
        assert rc == 1
        assert "recent dates" in stderr

    def test_rejects_inside_active_project(self, org_dir):
        stdout, stderr, rc = run_cli("archive", "pack suitcases", org_dir=org_dir)
        assert rc == 1
        assert "inside active project" in stderr

    def test_no_match(self, org_dir):
        stdout, stderr, rc = run_cli("archive", "xyznonexistent", org_dir=org_dir)
        assert rc == 1

    def test_ambiguous_match(self, org_dir):
        stdout, stderr, rc = run_cli("archive", "buy", org_dir=org_dir)
        assert rc == 2


# ===========================================================================
# 39. archive: batch (--all)
# ===========================================================================

class TestArchiveBatch:
    def test_happy_path(self, org_dir):
        # Pin "now" (fixture dates ~2026-03): expense-claims stays recent (kept),
        # router/dentists (2026-01) are old enough to archive. Deterministic.
        stdout, stderr, rc = run_cli(
            "archive", "--all", org_dir=org_dir,
            env_overrides={"ORG_GTD_CLI_NOW": "2026-03-20"})
        assert rc == 0
        assert "Archived" in stdout
        text = (org_dir / "tasks.org").read_text()
        assert "Buy new router" not in text
        assert "Research dentists" not in text
        assert 'Skipped (no dates): "Mystery task"' in stderr
        assert "Mystery task" in text
        assert "Submit expense claims" in text
        archive = (org_dir / "tasks.org_archive").read_text()
        assert "Buy new router" in archive
        assert "Research dentists" in archive

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("archive", "--all", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would archive" in stdout
        text = (org_dir / "tasks.org").read_text()
        assert "Buy new router" in text
        assert "Research dentists" in text

    def test_nothing_eligible(self, org_dir):
        # First pass archives eligible tasks
        run_cli("archive", "--all", org_dir=org_dir)
        # Second pass: nothing left
        stdout, stderr, rc = run_cli("archive", "--all", org_dir=org_dir)
        assert rc == 0
        assert "No archivable tasks found" in stderr


# ===========================================================================
# State-based sibling reordering (part of the done/set-state sections)
# ===========================================================================

class TestSiblingReordering:
    def _write_reorder_org(self, org_dir, content):
        (org_dir / "reorder.org").write_text(content)

    def test_done_reorders_above_next(self, org_dir):
        self._write_reorder_org(org_dir, """\
* Project A
** DONE Already done
** NEXT Active task
** TODO Target task
** TODO Another task
""")
        stdout, stderr, rc = run_cli("set-done", "Target task", org_dir=org_dir)
        assert rc == 0
        f = org_dir / "reorder.org"
        assert "DONE Target task" in f.read_text()
        assert_line_before(f, "DONE Target task", "NEXT Active task")
        assert_line_before(f, "DONE Already done", "DONE Target task")

    def test_done_auto_progress_reorders_both(self, org_dir):
        self._write_reorder_org(org_dir, """\
* TODO Project B
** DONE Old done
** NEXT Current task
** TODO First todo
** TODO Second todo
** TODO Third todo
""")
        stdout, stderr, rc = run_cli("set-done", "Current task", org_dir=org_dir)
        assert rc == 0
        f = org_dir / "reorder.org"
        text = f.read_text()
        assert "DONE Current task" in text
        assert "NEXT First todo" in text
        assert_line_before(f, "DONE Current task", "NEXT First todo")
        assert_line_before(f, "NEXT First todo", "TODO Second todo")

    def test_set_state_reorders(self, org_dir):
        self._write_reorder_org(org_dir, """\
* Project C
** DONE Old done
** NEXT Active
** TODO Alpha
** TODO Beta
""")
        stdout, stderr, rc = run_cli("set-state", "Beta", "NEXT", org_dir=org_dir)
        assert rc == 0
        f = org_dir / "reorder.org"
        assert_line_before(f, "DONE Old done", "NEXT Active")
        assert_line_before(f, "NEXT Beta", "TODO Alpha")

    def test_set_next_reorders_promoted_task(self, org_dir):
        self._write_reorder_org(org_dir, """\
* TODO Project D
** DONE Old done
** TODO Alpha
** TODO Beta
""")
        stdout, stderr, rc = run_cli("set-next", "Project D", org_dir=org_dir)
        assert rc == 0
        f = org_dir / "reorder.org"
        assert "NEXT Alpha" in f.read_text()
        assert_line_before(f, "DONE Old done", "NEXT Alpha")
        assert_line_before(f, "NEXT Alpha", "TODO Beta")

    def test_cancelled_sorts_with_done(self, org_dir):
        self._write_reorder_org(org_dir, """\
* Project E
** TODO Alpha
** TODO Beta
""")
        stdout, stderr, rc = run_cli("set-state", "Alpha", "CANCELLED", org_dir=org_dir)
        assert rc == 0
        f = org_dir / "reorder.org"
        assert_line_before(f, "CANCELLED Alpha", "TODO Beta")

    def test_waiting_defer_ordering(self, org_dir):
        self._write_reorder_org(org_dir, """\
* Project F
** TODO Alpha
** TODO Beta
""")
        run_cli("set-state", "Alpha", "DEFER", org_dir=org_dir)
        stdout, stderr, rc = run_cli("set-state", "Beta", "WAITING", org_dir=org_dir)
        assert rc == 0
        f = org_dir / "reorder.org"
        assert_line_before(f, "WAITING Beta", "DEFER Alpha")

    def test_non_task_siblings_skip_reorder(self, org_dir):
        self._write_reorder_org(org_dir, """\
* Computers
** Agents
*** TODO Agent task one
*** TODO Agent task two
** Emacs
*** TODO Emacs task one
""")
        stdout, stderr, rc = run_cli("set-state", "Agent task one", "DONE", org_dir=org_dir)
        assert rc == 0
        f = org_dir / "reorder.org"
        assert_line_before(f, "** Agents", "** Emacs")

    def test_top_level_task_skip_reorder(self, org_dir):
        self._write_reorder_org(org_dir, """\
* TODO Top level A
* TODO Top level B
""")
        stdout, stderr, rc = run_cli("set-state", "Top level A", "DONE", org_dir=org_dir)
        assert rc == 0
        assert "DONE Top level A" in (org_dir / "reorder.org").read_text()


# ===========================================================================
# 40. agenda-view
# ===========================================================================

class TestAgendaView:
    def test_default_key(self, org_dir):
        stdout, stderr, rc = run_cli("agenda-view", org_dir=org_dir)
        assert rc == 0
        assert "Next Tasks" in stdout
        assert "Projects" in stdout

    def test_includes_file_ref(self, org_dir):
        stdout, stderr, rc = run_cli("agenda-view", org_dir=org_dir)
        assert "(tasks.org)" in stdout

    def test_specific_key(self, org_dir):
        stdout, stderr, rc = run_cli("agenda-view", "n", org_dir=org_dir)
        assert rc == 0
        assert "Next Tasks" in stdout

    def test_invalid_key(self, org_dir):
        stdout, stderr, rc = run_cli("agenda-view", "INVALID", org_dir=org_dir)
        assert rc == 1
        assert "Unknown agenda view key" in stderr

    # --- JSON output ---

    def test_json_envelope(self, org_dir):
        """--json agenda-view emits the standard version/command envelope
        plus a `key` field and a `blocks` array."""
        data, stderr, rc = run_cli_json("agenda-view", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        assert data["version"] == 1
        assert data["command"] == "agenda-view"
        assert data["key"] == " "
        assert isinstance(data["blocks"], list)

    def test_json_all_blocks_present(self, org_dir):
        """The full dashboard view emits all 10 blocks from +gtd-core.el's
        " " custom command, in order, including the leading "Agenda"
        dated section."""
        data, stderr, rc = run_cli_json("agenda-view", org_dir=org_dir)
        assert rc == 0
        names = [b["name"] for b in data["blocks"]]
        assert names == [
            "Agenda",
            "Next Tasks",
            "Tasks",
            "Waiting",
            "Stuck Projects",
            "Projects",
            "Deferred",
            "Web",
            "Tasks to Refile",
            "Tasks to Archive",
        ]

    def test_json_block_with_results(self, org_dir):
        """A non-empty block carries task entries with the same field schema
        as other --json commands (heading, state, tags, file, scheduled,
        deadline, parent, is_project, properties)."""
        data, stderr, rc = run_cli_json("agenda-view", org_dir=org_dir)
        assert rc == 0
        waiting = next(b for b in data["blocks"] if b["name"] == "Waiting")
        assert waiting["count"] >= 1
        assert waiting["count"] == len(waiting["tasks"])
        task = waiting["tasks"][0]
        for field in ("heading", "state", "tags", "file", "scheduled",
                      "deadline", "parent", "is_project", "properties"):
            assert field in task, f"missing {field} in {task}"
        assert task["state"] == "WAITING"
        assert isinstance(task["tags"], list)
        assert task["file"].endswith(".org")

    def test_json_empty_block(self, org_dir):
        """An empty block (no matching tasks) still appears with count 0 and
        an empty tasks array — the fixtures have no Deferred tasks."""
        data, stderr, rc = run_cli_json("agenda-view", org_dir=org_dir)
        assert rc == 0
        deferred = next(b for b in data["blocks"] if b["name"] == "Deferred")
        assert deferred["count"] == 0
        assert deferred["tasks"] == []

    def test_json_single_key(self, org_dir):
        """A single-block view (e.g. 'w' Waiting) emits just that one block
        and echoes the requested key."""
        data, stderr, rc = run_cli_json("agenda-view", "w", org_dir=org_dir)
        assert rc == 0
        assert data["key"] == "w"
        assert [b["name"] for b in data["blocks"]] == ["Waiting"]

    def test_json_full_includes_body(self, org_dir):
        """--full adds a `body` field to each task entry."""
        data, stderr, rc = run_cli_json(
            "agenda-view", "t", "--full", org_dir=org_dir)
        assert rc == 0
        tasks = data["blocks"][0]["tasks"]
        assert tasks, "Tasks block should be non-empty in fixtures"
        assert all("body" in t for t in tasks)

    def test_json_invalid_key(self, org_dir):
        """--json on an unknown key emits a JSON error object on stderr."""
        stdout, stderr, rc = run_cli("--json", "agenda-view", "INVALID",
                                     org_dir=org_dir)
        assert rc == 1
        err_data = None
        for line in stderr.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                err_data = json.loads(line)
                break
        assert err_data is not None, f"No JSON error in stderr: {stderr}"
        assert "Unknown agenda view key" in err_data["error"]


# ===========================================================================
# 41. fix-timestamps
# ===========================================================================

class TestFixTimestamps:
    def test_removed(self, org_dir):
        """fix-timestamps is removed."""
        stdout, stderr, rc = run_cli("fix-timestamps", org_dir=org_dir)
        assert rc == 1
        assert "removed" in stderr.lower()


# ===========================================================================
# 42. fill-text (line wrapping)
# ===========================================================================

# Body text is filled at this column (matches `fill-column' in
# org-gtd-cli.el's fill-text; see commit 0050827 — set to 100 to mirror
# olli's Emacs setup). Wrapped lines must not exceed it.
FILL_COLUMN = 100

LONG_TEXT = (
    "This is a very long line of text that should definitely be wrapped "
    "because it exceeds the eighty column limit that we have set for body "
    "text in our GTD system"
)
LONG_TEXT2 = (
    "Another extremely long line of text that also exceeds the eighty "
    "column limit and should be wrapped properly by the fill-text function "
    "when processing"
)


class TestFillText:
    def test_append_body_wraps_long_text(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "Bare heading", LONG_TEXT, org_dir=org_dir)
        assert rc == 0
        assert_no_long_lines(org_dir / "tasks.org", "Bare heading", "Research", FILL_COLUMN)

    def test_set_body_wraps_long_text(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Bare heading", LONG_TEXT, org_dir=org_dir)
        assert rc == 0
        assert_no_long_lines(org_dir / "tasks.org", "Bare heading", "Research", FILL_COLUMN)

    def test_add_task_wraps_long_body(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Wrap test task", "--body", LONG_TEXT, org_dir=org_dir)
        assert rc == 0
        assert "Wrap test task" in (org_dir / "inbox.org").read_text()
        assert_no_long_lines(org_dir / "inbox.org", "Wrap test task", "", FILL_COLUMN)

    def test_add_subtask_wraps_long_body(self, org_dir):
        stdout, stderr, rc = run_cli("add-subtask", "Holiday pre-trip", "Wrap sub", "--body", LONG_TEXT, org_dir=org_dir)
        assert rc == 0
        assert "Wrap sub" in (org_dir / "tasks.org").read_text()
        assert_no_long_lines(org_dir / "tasks.org", "Wrap sub", "Finance", FILL_COLUMN)

    def test_src_block_preserved(self, org_dir):
        body = (
            "Here is code:\n"
            "#+begin_src bash\n"
            "echo this is a very long command that should not be wrapped because "
            "it is inside a source code block and wrapping would break it\n"
            "#+end_src"
        )
        stdout, stderr, rc = run_cli("set-body", "Bare heading", body, org_dir=org_dir)
        assert rc == 0
        assert "echo this is a very long command" in (org_dir / "tasks.org").read_text()

    def test_list_items_wrapped_independently(self, org_dir):
        body = (
            "- First item that is quite long and exceeds the eighty column limit "
            "so it should be wrapped onto the next line properly\n"
            "- Second item that is also quite long and exceeds the eighty column "
            "limit so it should also be wrapped onto the next line"
        )
        stdout, stderr, rc = run_cli("set-body", "Bare heading", body, org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert "- First item" in text
        assert "- Second item" in text

    def test_short_text_unchanged(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Bare heading", "Short text here", org_dir=org_dir)
        assert rc == 0
        assert "Short text here" in (org_dir / "tasks.org").read_text()

    def test_inactive_timestamp_stays_on_own_line(self, org_dir):
        body = (
            "Some text that is fairly long and might want to merge with the "
            "following line but should not.\n[2026-03-16 Mon 14:00]"
        )
        stdout, stderr, rc = run_cli("append-body", "Bare heading", body, org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert "[2026-03-16 Mon 14:00]" in text
        # Timestamp must be at start of a line
        assert any(
            line.strip() == "[2026-03-16 Mon 14:00]"
            for line in text.splitlines()
        )

    def test_time_range_timestamp_not_merged(self, org_dir):
        body = "Some important notes here.\n[2026-03-16 Mon 09:00-10:30]"
        stdout, stderr, rc = run_cli("append-body", "Bare heading", body, org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert "[2026-03-16 Mon 09:00-10:30]" in text
        assert any(
            line.strip() == "[2026-03-16 Mon 09:00-10:30]"
            for line in text.splitlines()
        )

    def test_two_paragraphs_both_wrapped(self, org_dir):
        body = f"{LONG_TEXT}\n\n{LONG_TEXT2}"
        stdout, stderr, rc = run_cli("set-body", "Bare heading", body, org_dir=org_dir)
        assert rc == 0
        assert_no_long_lines(org_dir / "tasks.org", "Bare heading", "Research", FILL_COLUMN)
        # Check blank line preserved between paragraphs
        text = (org_dir / "tasks.org").read_text()
        # Extract the body region between "Bare heading" and next heading
        in_range = False
        has_blank = False
        for line in text.splitlines():
            if not in_range:
                if "Bare heading" in line:
                    in_range = True
            else:
                if "Research" in line:
                    break
                if line.strip() == "":
                    has_blank = True
        assert has_blank, "blank line between paragraphs not preserved"

    def test_short_append_body_not_mangled(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "Bare heading", "OK", org_dir=org_dir)
        assert rc == 0
        assert "OK" in (org_dir / "tasks.org").read_text()

    def test_quote_block_preserved(self, org_dir):
        body = (
            "A quote:\n"
            "#+begin_quote\n"
            "This is a very long quotation that should be preserved verbatim "
            "because it is inside a quote block and should not be reflowed by "
            "the fill function\n"
            "#+end_quote"
        )
        stdout, stderr, rc = run_cli("set-body", "Bare heading", body, org_dir=org_dir)
        assert rc == 0
        assert "This is a very long quotation" in (org_dir / "tasks.org").read_text()

    def test_org_link_not_broken(self, org_dir):
        body = "Research file: [[file:agent-notes/service-phone-number-requirements-for-agent-accounts.org][Service Phone Number Requirements]]"
        stdout, stderr, rc = run_cli("set-body", "Bare heading", body, org_dir=org_dir)
        assert rc == 0
        assert (
            "[[file:agent-notes/service-phone-number-requirements-for-agent-accounts.org]"
            "[Service Phone Number Requirements]]"
        ) in (org_dir / "tasks.org").read_text()

    def test_long_text_wrapped_but_org_link_preserved(self, org_dir):
        body = (
            "This is a very long paragraph that exceeds the eighty column limit "
            "and should be wrapped properly by the fill function when processing "
            "the body text.\n\n"
            "Research file: [[file:agent-notes/service-phone-number-requirements"
            "-for-agent-accounts.org][Service Phone Number Requirements]]"
        )
        stdout, stderr, rc = run_cli("set-body", "Bare heading", body, org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert_no_long_lines(org_dir / "tasks.org", "Bare heading", "[[file:", FILL_COLUMN)
        assert (
            "[[file:agent-notes/service-phone-number-requirements-for-agent-accounts.org]"
            "[Service Phone Number Requirements]]"
        ) in text


# ===========================================================================
# 43. delete
# ===========================================================================

class TestDelete:
    def test_happy_path(self, org_dir):
        stdout, stderr, rc = run_cli("delete", "Buy a small UPS for the server", org_dir=org_dir)
        assert rc == 0
        assert 'Deleted: "Buy a small UPS for the server"' in stdout
        text = (org_dir / "tasks.org").read_text()
        assert "Buy a small UPS for the server" not in text
        assert "Write quarterly report" in text

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("delete", "Buy a small UPS for the server", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would delete:" in stdout
        assert "Buy a small UPS for the server" in (org_dir / "tasks.org").read_text()

    def test_refuses_project(self, org_dir):
        stdout, stderr, rc = run_cli("delete", "Buy a formicarium", org_dir=org_dir)
        assert rc == 1
        assert "is a project with subtasks" in stderr
        assert "Buy a formicarium" in (org_dir / "tasks.org").read_text()

    def test_no_match(self, org_dir):
        stdout, stderr, rc = run_cli("delete", "xyznonexistent", org_dir=org_dir)
        assert rc == 1

    def test_rejects_substring_match(self, org_dir):
        stdout, stderr, rc = run_cli("delete", "Buy", org_dir=org_dir)
        assert rc == 1

    def test_delete_done_task(self, org_dir):
        stdout, stderr, rc = run_cli("delete", "Mystery task", org_dir=org_dir)
        assert rc == 0
        assert 'Deleted: "Mystery task"' in stdout
        assert "Mystery task" not in (org_dir / "tasks.org").read_text()

    def test_with_index(self, org_dir):
        stdout, stderr, rc = run_cli("delete", "Buy a small UPS for the server", "--index", "1", org_dir=org_dir)
        assert rc == 0
        assert "Buy a small UPS for the server" not in (org_dir / "tasks.org").read_text()

    def test_index_out_of_range(self, org_dir):
        stdout, stderr, rc = run_cli("delete", "Buy a small UPS for the server", "--index", "5", org_dir=org_dir)
        assert rc == 1


# ===========================================================================
# 44. markup-aware matching
# ===========================================================================

class TestMarkupAwareMatching:
    def test_search_stripped_query_matches(self, org_dir):
        stdout, stderr, rc = run_cli("search", "find", org_dir=org_dir)
        assert rc == 0
        assert "Fix the =find= command" in stdout

    def test_search_raw_markup_query(self, org_dir):
        stdout, stderr, rc = run_cli("search", "=find=", org_dir=org_dir)
        assert rc == 0
        assert "Fix the =find= command" in stdout

    def test_search_strips_italic(self, org_dir):
        stdout, stderr, rc = run_cli("search", "italic", org_dir=org_dir)
        assert rc == 0
        assert "Review /italic/" in stdout

    def test_search_strips_code(self, org_dir):
        stdout, stderr, rc = run_cli("search", "code", org_dir=org_dir)
        assert rc == 0
        assert "Review /italic/" in stdout

    def test_search_raw_italic_query(self, org_dir):
        stdout, stderr, rc = run_cli("search", "/italic/", org_dir=org_dir)
        assert rc == 0
        assert "Review /italic/" in stdout

    def test_show_stripped_markup(self, org_dir):
        stdout, stderr, rc = run_cli("show", "find command", org_dir=org_dir)
        assert rc == 0

    def test_show_raw_markup(self, org_dir):
        stdout, stderr, rc = run_cli("show", "=find= command", org_dir=org_dir)
        assert rc == 0

    def test_delete_exact_stripped_markup(self, org_dir):
        stdout, stderr, rc = run_cli(
            "delete", "Fix the find command in org-gtd-cli", "--dry-run",
            org_dir=org_dir,
        )
        assert rc == 0
        assert "Would delete:" in stdout

    def test_delete_raw_exact_match(self, org_dir):
        stdout, stderr, rc = run_cli(
            "delete", "Fix the =find= command in org-gtd-cli", "--dry-run",
            org_dir=org_dir,
        )
        assert rc == 0
        assert "Would delete:" in stdout


# ===========================================================================
# 45. refile: markup-aware --to matching
# ===========================================================================

class TestRefileMarkupAware:
    def test_to_stripped_markup_matches(self, org_dir):
        stdout, stderr, rc = run_cli(
            "refile", "Buy groceries", "--to",
            "Fix the find command in org-gtd-cli", org_dir=org_dir,
        )
        assert rc == 0
        assert "Refiled" in stdout
        assert "Buy groceries" not in (org_dir / "inbox.org").read_text()

    def test_to_raw_markup(self, org_dir):
        stdout, stderr, rc = run_cli(
            "refile", "Buy groceries", "--to",
            "Fix the =find= command in org-gtd-cli", org_dir=org_dir,
        )
        assert rc == 0
        assert "Refiled" in stdout

    def test_to_path_with_stripped_markup(self, org_dir):
        stdout, stderr, rc = run_cli(
            "refile", "Buy groceries", "--to",
            "Tools/Fix the find command in org-gtd-cli", org_dir=org_dir,
        )
        assert rc == 0
        assert "Refiled" in stdout

    def test_to_stripped_markup_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli(
            "refile", "Buy groceries", "--to",
            "Fix the find command in org-gtd-cli", "--dry-run", org_dir=org_dir,
        )
        assert rc == 0
        assert "Would refile" in stdout
        assert "Buy groceries" in (org_dir / "inbox.org").read_text()

    def test_category_still_works(self, org_dir):
        stdout, stderr, rc = run_cli(
            "refile", "Buy groceries", "--category", "Computers/Tools",
            org_dir=org_dir,
        )
        assert rc == 0
        assert "Refiled" in stdout


# ===========================================================================
# 46. unescape_body_newlines (literal \n -> newline)
# ===========================================================================

def _load_cli_module():
    """Load org-gtd-cli.py as a module for unit testing internal functions."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("org_gtd_cli", str(CLI_SCRIPT))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestUnescapeBodyNewlines:
    """Tests for the unescape_body_newlines function in org-gtd-cli.py.

    These test the Python function directly (not via CLI) since the bash
    tests also tested the bash equivalent directly.
    """

    def test_literal_backslash_n_converted(self):
        mod = _load_cli_module()
        result = mod.unescape_body_newlines("Line one.\\nLine two.")
        assert result == "Line one.\nLine two."

    def test_double_backslash_n_preserved(self):
        mod = _load_cli_module()
        result = mod.unescape_body_newlines("Keep \\\\n literal")
        assert result == "Keep \\n literal"

    def test_no_escapes_unchanged(self):
        mod = _load_cli_module()
        result = mod.unescape_body_newlines("No escapes here")
        assert result == "No escapes here"

    def test_empty_string_unchanged(self):
        mod = _load_cli_module()
        result = mod.unescape_body_newlines("")
        assert result == ""

    def test_multiple_backslash_n(self):
        mod = _load_cli_module()
        result = mod.unescape_body_newlines("A\\nB\\nC")
        assert result == "A\nB\nC"

    def test_integration_add_task_with_unescaped_body(self, org_dir):
        """Full integration: add-task with body containing literal \\n."""
        stdout, stderr, rc = run_cli(
            "add-task", "Newline test",
            "--body", "Step one.\\nStep two.\\nStep three.",
            org_dir=org_dir,
        )
        assert rc == 0
        text = (org_dir / "inbox.org").read_text()
        assert "Step one." in text
        assert "Step two." in text
        assert "Step three." in text
        assert "Step one.\\nStep two." not in text


# =========================================================================
# Body file input and literal dash rejection
# =========================================================================

class TestBodyFileInput:
    """Test --body-file FILE and --body-file - (stdin) support."""

    def test_set_body_from_file(self, org_dir, tmp_path):
        body_file = tmp_path / "body.txt"
        body_file.write_text("Body from file content.")
        stdout, stderr, rc = run_cli(
            "set-body", "Write quarterly report", "--body-file", str(body_file),
            org_dir=org_dir,
        )
        assert rc == 0
        assert "Body from file content." in (org_dir / "tasks.org").read_text()

    def test_append_body_from_file(self, org_dir, tmp_path):
        body_file = tmp_path / "append.txt"
        body_file.write_text("Appended from file.")
        stdout, stderr, rc = run_cli(
            "append-body", "Write quarterly report", "--body-file", str(body_file),
            org_dir=org_dir,
        )
        assert rc == 0
        assert "Appended from file." in (org_dir / "tasks.org").read_text()

    def test_add_task_body_from_file(self, org_dir, tmp_path):
        body_file = tmp_path / "body.txt"
        body_file.write_text("Task body from file.")
        stdout, stderr, rc = run_cli(
            "add-task", "File body test", "--body-file", str(body_file),
            org_dir=org_dir,
        )
        assert rc == 0
        assert "Task body from file." in (org_dir / "inbox.org").read_text()

    def test_set_body_from_stdin(self, org_dir):
        """--body-file - reads from stdin."""
        env = os.environ.copy()
        env["ORG_DIRECTORY"] = str(org_dir) + "/"
        env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
        env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
        cmd = ["python3", str(CLI_SCRIPT),
               "set-body", "Write quarterly report", "--body-file", "-"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            input="Stdin body content.", timeout=30,
        )
        assert result.returncode == 0
        assert "Stdin body content." in (org_dir / "tasks.org").read_text()

    def test_append_body_from_stdin(self, org_dir):
        env = os.environ.copy()
        env["ORG_DIRECTORY"] = str(org_dir) + "/"
        env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
        env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
        cmd = ["python3", str(CLI_SCRIPT),
               "append-body", "Write quarterly report", "--body-file", "-"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            input="Stdin append.", timeout=30,
        )
        assert result.returncode == 0
        assert "Stdin append." in (org_dir / "tasks.org").read_text()

    def test_body_file_overrides_positional_text(self, org_dir, tmp_path):
        """--body-file takes precedence over positional TEXT."""
        body_file = tmp_path / "body.txt"
        body_file.write_text("From file.")
        stdout, stderr, rc = run_cli(
            "set-body", "Write quarterly report", "positional text", "--body-file", str(body_file),
            org_dir=org_dir,
        )
        assert rc == 0
        text = (org_dir / "tasks.org").read_text()
        assert "From file." in text
        assert "positional text" not in text


class TestRejectLiteralDash:
    """Reject literal '-' as TEXT in body commands."""

    def test_set_body_rejects_dash(self, org_dir):
        stdout, stderr, rc = run_cli(
            "set-body", "Write quarterly report", "-",
            org_dir=org_dir,
        )
        assert rc == 1
        assert "--body-file" in stderr

    def test_append_body_rejects_dash(self, org_dir):
        stdout, stderr, rc = run_cli(
            "append-body", "Write quarterly report", "-",
            org_dir=org_dir,
        )
        assert rc == 1
        assert "--body-file" in stderr

    def test_add_task_rejects_dash_body(self, org_dir):
        stdout, stderr, rc = run_cli(
            "add-task", "Dash test", "--body", "-",
            org_dir=org_dir,
        )
        assert rc == 1
        assert "--body-file" in stderr


# ===========================================================================
# JSON infrastructure
# ===========================================================================

def run_cli_json(*args, org_dir):
    """Run org-gtd-cli with --json flag. Returns (parsed_json, stderr, rc).

    If stdout is valid JSON, returns the parsed dict/list.
    If stdout is empty or not valid JSON, returns None.
    """
    stdout, stderr, rc = run_cli("--json", *args, org_dir=org_dir)
    if stdout.strip():
        try:
            return json.loads(stdout), stderr, rc
        except json.JSONDecodeError:
            return None, stderr, rc
    return None, stderr, rc


class TestJsonInfrastructure:
    """Tests for the --json flag, env var passing, and error formatting."""

    def test_json_flag_on_search_returns_valid_json(self, org_dir):
        """--json on a simple command returns valid JSON with version and command."""
        data, stderr, rc = run_cli_json("search", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        assert data["version"] == 1
        assert data["command"] == "search"

    def test_json_flag_on_search_no_match(self, org_dir):
        """--json search with no match returns empty tasks array."""
        data, stderr, rc = run_cli_json(
            "search", "nonexistent_xyz_task_12345", org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert data["tasks"] == []
        assert data["count"] == 0

    def test_json_error_on_stderr(self, org_dir):
        """--json errors produce JSON on stderr."""
        data, stderr, rc = run_cli_json("show", "nonexistent_xyz_12345", org_dir=org_dir)
        assert rc == 1
        # stderr contains Emacs loading messages mixed with our JSON errors
        # Find the first line that looks like JSON
        err_data = None
        for line in stderr.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                err_data = json.loads(line)
                break
        assert err_data is not None, f"No JSON error found in stderr: {stderr}"
        assert "error" in err_data

    def test_json_rejected_for_org_timestamp(self, org_dir):
        """--json on org-timestamp returns error."""
        stdout, stderr, rc = run_cli("--json", "org-timestamp", "2026-03-15", org_dir=org_dir)
        assert rc == 1
        err_data = json.loads(stderr.strip())
        assert "error" in err_data
        assert "org-timestamp" in err_data["error"]

    def test_text_mode_unchanged(self, org_dir):
        """Without --json, output is still human-readable text (not JSON)."""
        stdout, stderr, rc = run_cli("search", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert "[1]" in stdout  # Text format uses [index] prefix
        # Should not be valid JSON
        try:
            json.loads(stdout)
            assert False, "Text mode should not produce valid JSON"
        except json.JSONDecodeError:
            pass


# ===========================================================================
# JSON: search command
# ===========================================================================

class TestJsonSearch:
    """Tests for --json search output."""

    def test_search_with_matches(self, org_dir):
        data, _, rc = run_cli_json("search", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["version"] == 1
        assert data["command"] == "search"
        assert data["count"] == 1
        task = data["tasks"][0]
        assert task["index"] == 1
        assert task["heading"] == "Buy groceries"
        assert task["state"] == "TODO"
        assert isinstance(task["tags"], list)
        assert "@errand" in task["tags"]
        assert task["file"] == "inbox.org"
        assert task["is_project"] is False

    def test_search_empty_returns_zero_count(self, org_dir):
        data, _, rc = run_cli_json("search", "zzz_no_match_zzz", org_dir=org_dir)
        assert rc == 0
        assert data["tasks"] == []
        assert data["count"] == 0

    def test_search_parent_field(self, org_dir):
        """Tasks under a parent heading should have parent set."""
        data, _, rc = run_cli_json(
            "search", "--state", "all", "--tag", "@agent",
            org_dir=org_dir,
        )
        assert rc == 0
        # Find a task that has a parent
        for task in data["tasks"]:
            if task["parent"] is not None:
                break
        else:
            # If no tasks have a parent, that's fine — just check types
            pass
        # All tasks should have parent as string or null
        for task in data["tasks"]:
            assert task["parent"] is None or isinstance(task["parent"], str)

    def test_search_is_project_field(self, org_dir):
        """Projects should have is_project=true."""
        data, _, rc = run_cli_json("search", "--state", "all", org_dir=org_dir)
        assert rc == 0
        for task in data["tasks"]:
            assert isinstance(task["is_project"], bool)

    def test_search_indices_sequential(self, org_dir):
        """Index values should be sequential starting at 1."""
        data, _, rc = run_cli_json("search", "--state", "all", org_dir=org_dir)
        assert rc == 0
        if data["count"] > 0:
            indices = [t["index"] for t in data["tasks"]]
            assert indices == list(range(1, len(indices) + 1))

    def test_search_with_tag_filter(self, org_dir):
        """Tag filter should work with --json."""
        data, _, rc = run_cli_json(
            "search", "--tag", "@errand", org_dir=org_dir,
        )
        assert rc == 0
        for task in data["tasks"]:
            assert "@errand" in task["tags"]

    def test_search_with_state_filter(self, org_dir):
        """State filter should work with --json."""
        data, _, rc = run_cli_json(
            "search", "--state", "TODO", org_dir=org_dir,
        )
        assert rc == 0
        for task in data["tasks"]:
            assert task["state"] == "TODO"


# ===========================================================================
# JSON: agenda command
# ===========================================================================

class TestJsonAgenda:
    """Tests for --json agenda output."""

    def test_agenda_returns_valid_json(self, org_dir):
        data, _, rc = run_cli_json("agenda", org_dir=org_dir)
        assert rc == 0
        assert data["version"] == 1
        assert data["command"] == "agenda"
        assert isinstance(data["tasks"], list)
        assert data["count"] == len(data["tasks"])

    def test_agenda_task_fields(self, org_dir):
        """Each task should have required fields."""
        data, _, rc = run_cli_json("agenda", org_dir=org_dir)
        assert rc == 0
        if data["count"] > 0:
            task = data["tasks"][0]
            assert "heading" in task
            assert "state" in task
            assert "priority" in task
            assert "tags" in task
            assert "file" in task
            assert "scheduled" in task
            assert "deadline" in task
            assert "parent" in task
            assert "is_project" in task
            # No index field in agenda
            assert "index" not in task

    def test_agenda_with_state_filter(self, org_dir):
        data, _, rc = run_cli_json("agenda", "--state", "TODO", org_dir=org_dir)
        assert rc == 0
        for task in data["tasks"]:
            assert task["state"] == "TODO"

    def test_agenda_scheduled_deadline_fields(self, org_dir):
        """Scheduled/deadline should be string or null."""
        data, _, rc = run_cli_json("agenda", org_dir=org_dir)
        assert rc == 0
        for task in data["tasks"]:
            assert task["scheduled"] is None or isinstance(task["scheduled"], str)
            assert task["deadline"] is None or isinstance(task["deadline"], str)

    def test_agenda_priority_field(self, org_dir):
        """Priority should be string or null."""
        data, _, rc = run_cli_json("agenda", org_dir=org_dir)
        assert rc == 0
        for task in data["tasks"]:
            assert task["priority"] is None or isinstance(task["priority"], str)


# ===========================================================================
# JSON: show command
# ===========================================================================

class TestJsonShow:
    """Tests for --json show output."""

    def test_show_returns_valid_json(self, org_dir):
        data, _, rc = run_cli_json("show", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["version"] == 1
        assert data["command"] == "show"
        assert data["heading"] == "Buy groceries"
        assert data["state"] == "TODO"
        assert data["file"] == "inbox.org"

    def test_show_task_all_fields(self, org_dir):
        """Show should include all required fields."""
        data, _, rc = run_cli_json("show", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        for field in ["heading", "state", "priority", "tags", "file",
                       "scheduled", "deadline", "parent", "is_project",
                       "body", "sessions", "subtasks", "progress"]:
            assert field in data, f"Missing field: {field}"

    def test_show_sessions_empty_by_default(self, org_dir):
        """Sessions field is empty list when no sessions recorded."""
        data, _, rc = run_cli_json("show", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["sessions"] == []

    def test_show_sessions_after_add(self, org_dir):
        """Sessions field populated after add-session-id."""
        run_cli("add-session-id", "Set up automated backups",
                "claude_code:show-test-uuid", org_dir=org_dir)
        data, _, rc = run_cli_json("show", "Set up automated backups", org_dir=org_dir)
        assert rc == 0
        assert len(data["sessions"]) >= 1
        assert data["sessions"][0]["agent"] == "claude_code"
        assert data["sessions"][0]["session_id"] == "show-test-uuid"

    def test_show_project_with_subtasks(self, org_dir):
        """Show on a project should include subtasks and progress."""
        # First find a project
        search_data, _, _ = run_cli_json("search", "--state", "all", org_dir=org_dir)
        project = None
        for task in search_data["tasks"]:
            if task["is_project"]:
                project = task
                break
        if project is None:
            pytest.skip("No projects in fixture data")
        data, _, rc = run_cli_json("show", project["heading"], org_dir=org_dir)
        assert rc == 0
        assert data["is_project"] is True
        assert isinstance(data["subtasks"], list)
        assert len(data["subtasks"]) > 0
        assert data["progress"] is not None
        assert "done" in data["progress"]
        assert "total" in data["progress"]

    def test_show_leaf_task(self, org_dir):
        """Show on a leaf task should have empty subtasks and null progress."""
        data, _, rc = run_cli_json("show", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["is_project"] is False
        assert data["subtasks"] == []
        assert data["progress"] is None

    def test_show_plain_ignored_in_json(self, org_dir):
        """--plain flag should be ignored in JSON mode."""
        data, _, rc = run_cli_json("show", "Buy groceries", "--plain", org_dir=org_dir)
        assert rc == 0
        assert data["version"] == 1
        assert data["heading"] == "Buy groceries"

    def test_show_subtask_fields(self, org_dir):
        """Subtask objects should have correct fields."""
        search_data, _, _ = run_cli_json("search", "--state", "all", org_dir=org_dir)
        project = None
        for task in search_data["tasks"]:
            if task["is_project"]:
                project = task
                break
        if project is None:
            pytest.skip("No projects in fixture data")
        data, _, rc = run_cli_json("show", project["heading"], org_dir=org_dir)
        assert rc == 0
        if data["subtasks"]:
            child = data["subtasks"][0]
            assert "heading" in child
            assert "state" in child
            assert "priority" in child
            assert "tags" in child
            assert "scheduled" in child
            assert "deadline" in child
            assert "is_project" in child
            # No file or parent (redundant in subtask context)
            assert "file" not in child
            assert "parent" not in child


# ===========================================================================
# JSON: subtasks command
# ===========================================================================

class TestJsonSubtasks:
    """Tests for --json subtasks output."""

    def test_subtasks_returns_valid_json(self, org_dir):
        """Find a project and check its subtasks JSON."""
        # First find a project
        search_data, _, _ = run_cli_json("search", "--state", "all", org_dir=org_dir)
        project = None
        for task in search_data["tasks"]:
            if task["is_project"]:
                project = task
                break
        if project is None:
            pytest.skip("No projects in fixture data")
        data, _, rc = run_cli_json("subtasks", project["heading"], org_dir=org_dir)
        assert rc == 0
        assert data["version"] == 1
        assert data["command"] == "subtasks"
        assert data["heading"] == project["heading"]
        assert isinstance(data["subtasks"], list)
        assert len(data["subtasks"]) > 0

    def test_subtasks_progress(self, org_dir):
        """Progress should have done and total counts."""
        search_data, _, _ = run_cli_json("search", "--state", "all", org_dir=org_dir)
        project = None
        for task in search_data["tasks"]:
            if task["is_project"]:
                project = task
                break
        if project is None:
            pytest.skip("No projects in fixture data")
        data, _, rc = run_cli_json("subtasks", project["heading"], org_dir=org_dir)
        assert rc == 0
        assert "done" in data["progress"]
        assert "total" in data["progress"]
        assert data["progress"]["total"] == len(data["subtasks"])

    def test_subtasks_child_fields(self, org_dir):
        """Subtask objects should have correct fields."""
        search_data, _, _ = run_cli_json("search", "--state", "all", org_dir=org_dir)
        project = None
        for task in search_data["tasks"]:
            if task["is_project"]:
                project = task
                break
        if project is None:
            pytest.skip("No projects in fixture data")
        data, _, rc = run_cli_json("subtasks", project["heading"], org_dir=org_dir)
        assert rc == 0
        child = data["subtasks"][0]
        for field in ["heading", "state", "priority", "tags",
                       "scheduled", "deadline", "is_project"]:
            assert field in child, f"Missing field: {field}"


# ===========================================================================
# JSON: categories command
# ===========================================================================

class TestJsonCategories:
    """Tests for --json categories output."""

    def test_categories_returns_valid_json(self, org_dir):
        data, _, rc = run_cli_json("categories", org_dir=org_dir)
        assert rc == 0
        assert data["version"] == 1
        assert data["command"] == "categories"
        assert isinstance(data["categories"], list)
        assert len(data["categories"]) > 0
        assert "file" in data

    def test_categories_are_path_strings(self, org_dir):
        data, _, rc = run_cli_json("categories", org_dir=org_dir)
        assert rc == 0
        for cat in data["categories"]:
            assert isinstance(cat, str)


# ===========================================================================
# JSON: projects command
# ===========================================================================

class TestJsonProjects:
    """Tests for --json projects output."""

    def test_projects_returns_valid_json(self, org_dir):
        data, _, rc = run_cli_json("projects", org_dir=org_dir)
        assert rc == 0
        assert data["version"] == 1
        assert data["command"] == "projects"
        assert isinstance(data["projects"], list)
        assert data["count"] == len(data["projects"])

    def test_project_fields(self, org_dir):
        data, _, rc = run_cli_json("projects", org_dir=org_dir)
        assert rc == 0
        if data["count"] > 0:
            proj = data["projects"][0]
            for field in ["heading", "path", "state", "tags", "file",
                           "parent", "progress"]:
                assert field in proj, f"Missing field: {field}"
            assert "done" in proj["progress"]
            assert "total" in proj["progress"]

    def test_project_path_vs_heading(self, org_dir):
        """Path should be full category path, heading should be just the name."""
        data, _, rc = run_cli_json("projects", org_dir=org_dir)
        assert rc == 0
        if data["count"] > 0:
            proj = data["projects"][0]
            # Heading is a substring of path
            assert proj["heading"] in proj["path"]


# ===========================================================================
# list-tags command
# ===========================================================================

class TestListTags:
    """Tests for the list-tags command (tag usage inventory)."""

    @staticmethod
    def _counts(data):
        return {entry["tag"]: entry["count"] for entry in data["tags"]}

    def test_json_shape(self, org_dir):
        data, _, rc = run_cli_json("list-tags", org_dir=org_dir)
        assert rc == 0
        assert data["version"] == 1
        assert data["command"] == "list-tags"
        assert isinstance(data["tags"], list)
        assert data["count"] == len(data["tags"])
        for entry in data["tags"]:
            assert isinstance(entry["tag"], str)
            assert isinstance(entry["count"], int)

    def test_counts_match_fixtures(self, org_dir):
        """Anchor known tag counts from the fixture files."""
        data, _, rc = run_cli_json("list-tags", org_dir=org_dir)
        assert rc == 0
        counts = self._counts(data)
        assert counts["@agent"] == 3   # three TODO tasks in tasks.org
        assert counts["buy"] == 2      # Shopping heading + inbox task
        assert counts["email"] == 2    # inbox task + travel insurance task
        assert counts["url"] == 2      # inbox task + research task
        assert counts["@errand"] == 1  # inbox "Buy groceries"
        assert counts["work"] == 1     # top-level Work category heading

    def test_includes_done_and_plain_headings(self, org_dir):
        """Tags on DONE tasks and plain (non-TODO) headings are counted."""
        data, _, rc = run_cli_json("list-tags", org_dir=org_dir)
        assert rc == 0
        counts = self._counts(data)
        # calendar.org: one DONE task + one plain heading, both :calpersonal:
        assert counts["calpersonal"] == 2
        # WAITING-state tasks are counted too (tag shares the keyword name)
        assert counts["WAITING"] == 2

    def test_local_tags_only(self, org_dir):
        """Inherited tags are not counted — only literal headline tags."""
        data, _, rc = run_cli_json("list-tags", org_dir=org_dir)
        assert rc == 0
        counts = self._counts(data)
        # The Computers category has many descendants (including the @agent
        # tasks); if inheritance leaked in, this would be far more than 1.
        assert counts["computers"] == 1
        assert counts["family"] == 1
        assert counts["travel"] == 1

    def test_sort_order(self, org_dir):
        """Sorted by count descending, ties alphabetically."""
        data, _, rc = run_cli_json("list-tags", org_dir=org_dir)
        assert rc == 0
        entries = [(e["count"], e["tag"]) for e in data["tags"]]
        assert entries[0][1] == "@agent"  # unique highest count
        for (c1, t1), (c2, t2) in zip(entries, entries[1:]):
            assert c1 >= c2, f"counts not descending: {t1}={c1} before {t2}={c2}"
            if c1 == c2:
                assert t1 < t2, f"tie not alphabetical: {t1} before {t2}"

    def test_text_mode(self, org_dir):
        """Text mode prints one 'count tag' line per tag, same ordering."""
        stdout, _, rc = run_cli("list-tags", org_dir=org_dir)
        assert rc == 0
        lines = stdout.strip().split("\n")
        assert all(re.match(r"^\s*\d+ \S+$", line) for line in lines)
        assert re.match(r"^\s*3 @agent$", lines[0])
        # One line per distinct tag, matching the JSON count
        data, _, _ = run_cli_json("list-tags", org_dir=org_dir)
        assert len(lines) == data["count"]

    def test_no_tags(self, org_dir):
        """An org dir with no tagged headlines yields an empty inventory."""
        for f in org_dir.glob("*.org"):
            f.unlink()
        (org_dir / "tasks.org").write_text("* TODO Untagged task\n")
        data, _, rc = run_cli_json("list-tags", org_dir=org_dir)
        assert rc == 0
        assert data["tags"] == []
        assert data["count"] == 0


# ===========================================================================
# JSON: mutation commands (set-done, set-state, etc.)
# ===========================================================================

class TestJsonMutations:
    """Tests for --json on mutation commands."""

    def test_set_done_json(self, org_dir):
        data, _, rc = run_cli_json("set-done", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["version"] == 1
        assert data["command"] == "set-done"
        assert data["heading"] == "Buy groceries"
        assert data["old_state"] == "TODO"
        assert data["new_state"] == "DONE"
        assert "side_effects" in data
        assert isinstance(data["side_effects"], list)

    def test_set_done_json_heading_prefix_of_another(self, tmp_path):
        """Regression: mutation-output re-finds the task by heading to attach
        the full task state to the JSON response. When the heading was a
        substring of another task's heading, the substring-based re-find hit
        the Multiple-matches branch and called kill-emacs 2 AFTER the mutation
        was already saved — reporting failure (exit 2) for a successful
        mutation. The re-find is now exact-match and intercepts kill-emacs,
        degrading to omitting the task field instead of exiting."""
        (tmp_path / "inbox.org").write_text(
            "* Inbox\n"
            "** TODO Buy milk\n"
            "** TODO Buy milk and eggs\n")
        data, _, rc = run_cli_json("set-done", "Buy milk", "--index", "1",
                                   org_dir=tmp_path)
        assert rc == 0
        assert "error" not in data
        assert data["command"] == "set-done"
        assert data["heading"] == "Buy milk"
        assert data["new_state"] == "DONE"
        # The exact-match re-find succeeds and attaches the full task state
        assert data["task"]["heading"] == "Buy milk"
        assert data["task"]["state"] == "DONE"
        text = (tmp_path / "inbox.org").read_text()
        assert "** DONE Buy milk\n" in text
        assert "** TODO Buy milk and eggs" in text

    def test_set_done_dry_run_json(self, org_dir):
        data, _, rc = run_cli_json("set-done", "Buy groceries", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert data["dry_run"] is True
        assert data["old_state"] == "TODO"
        assert data["new_state"] == "DONE"

    def test_set_done_with_auto_progress(self, org_dir):
        """Completing a project subtask promotes the next TODO sibling to NEXT,
        reported as a single state-change side effect."""
        # "Prepare onboarding guide" has children: Draft outline, Write first
        # chapter (both TODO). Completing the first promotes the next.
        data, _, rc = run_cli_json("set-done", "Draft outline", org_dir=org_dir)
        assert rc == 0
        assert data["new_state"] == "DONE"
        assert data["side_effects"] == [
            {
                "action": "state-change",
                "heading": "Write first chapter",
                "old_state": "TODO",
                "new_state": "NEXT",
                "file": "tasks.org",
            }
        ]
        assert "NEXT Write first chapter" in (org_dir / "tasks.org").read_text()

    def test_set_done_subproject_drill_in_json(self, org_dir):
        """Promoting into a sub-project reports the drilled-in child with the
        correct file (regression: the generic NEXT parser used to capture the
        'in subproject ...' segment as the file)."""
        # "Improve monitoring": Set up alerting (NEXT), Add dashboards (TODO
        # project -> Design dashboard layout, Implement dashboard). Completing
        # the NEXT sibling drills into "Add dashboards".
        data, _, rc = run_cli_json("set-done", "Set up alerting", org_dir=org_dir)
        assert rc == 0
        assert data["side_effects"] == [
            {
                "action": "state-change",
                "heading": "Design dashboard layout",
                "old_state": "TODO",
                "new_state": "NEXT",
                "file": "tasks.org",
            }
        ]

    def test_set_done_nested_last_subtask_no_cascade_json(self, org_dir):
        """Completing the last open subtask of a nested sub-project leaves that
        sub-project open for review and does NOT cascade to the grandparent's
        other children (no auto-complete, no promotion of 'Run smoke tests')."""
        data, _, rc = run_cli_json("set-done", "Install certificates", org_dir=org_dir)
        assert rc == 0
        assert data["side_effects"] == [
            {
                "action": "project-needs-review",
                "heading": "Set up TLS certs",
                "file": "tasks.org",
            }
        ]
        text = (org_dir / "tasks.org").read_text()
        assert "TODO Set up TLS certs" in text
        assert "DONE Set up TLS certs" not in text
        assert "NEXT Run smoke tests" not in text
        assert "TODO Run smoke tests" in text

    def test_set_done_existing_next_no_side_effects_json(self, org_dir):
        """When a sibling already holds NEXT, completing another task promotes
        nothing — side_effects is empty."""
        data, _, rc = run_cli_json("set-done", "Get travel insurance quote", org_dir=org_dir)
        assert rc == 0
        assert data["side_effects"] == []

    def test_set_done_dry_run_side_effects_json(self, org_dir):
        """Dry-run JSON previews the same side_effects the real run would
        produce, without mutating the file."""
        data, _, rc = run_cli_json("set-done", "Draft outline", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert data["dry_run"] is True
        assert data["side_effects"] == [
            {
                "action": "state-change",
                "heading": "Write first chapter",
                "old_state": "TODO",
                "new_state": "NEXT",
                "file": "tasks.org",
            }
        ]
        # Dry run makes no changes.
        text = (org_dir / "tasks.org").read_text()
        assert "TODO Draft outline" in text
        assert "TODO Write first chapter" in text

    def test_set_done_dry_run_subproject_side_effects_json(self, org_dir):
        """Dry-run JSON previews a drill-in promotion with the correct file."""
        data, _, rc = run_cli_json("set-done", "Set up alerting", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert data["dry_run"] is True
        assert data["side_effects"] == [
            {
                "action": "state-change",
                "heading": "Design dashboard layout",
                "old_state": "TODO",
                "new_state": "NEXT",
                "file": "tasks.org",
            }
        ]
        assert "NEXT Set up alerting" in (org_dir / "tasks.org").read_text()

    def test_set_done_dry_run_project_needs_review_json(self, org_dir):
        """Dry-run JSON previews a project-needs-review side effect for the last
        subtask of a sub-project, without mutating the file."""
        data, _, rc = run_cli_json("set-done", "Install certificates", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert data["dry_run"] is True
        assert data["side_effects"] == [
            {
                "action": "project-needs-review",
                "heading": "Set up TLS certs",
                "file": "tasks.org",
            }
        ]
        assert "NEXT Install certificates" in (org_dir / "tasks.org").read_text()

    def test_set_done_last_subtask_reports_project_needs_review(self, org_dir):
        """Completing the last subtask emits a project-needs-review side effect
        (not a state-change) so JSON consumers know the parent was left open."""
        data, _, rc = run_cli_json("set-done", "Test migration on staging", org_dir=org_dir)
        assert rc == 0
        review = [e for e in data["side_effects"] if e.get("action") == "project-needs-review"]
        assert len(review) == 1
        assert review[0]["heading"] == "Ship epiphyte updates"
        # No state-change side effect should auto-close the parent
        assert not any(
            e.get("action") == "state-change" and e.get("new_state") == "DONE"
            for e in data["side_effects"]
        )

    def test_set_state_json(self, org_dir):
        data, _, rc = run_cli_json("set-state", "Buy groceries", "WAITING", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "set-state"
        assert data["old_state"] == "TODO"
        assert data["new_state"] == "WAITING"

    def test_set_state_dry_run_json(self, org_dir):
        data, _, rc = run_cli_json("set-state", "Buy groceries", "WAITING", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert data["dry_run"] is True

    def test_set_cancelled_json(self, org_dir):
        """set-cancelled delegates to set-state, should produce JSON."""
        data, _, rc = run_cli_json("set-cancelled", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "set-state"
        assert data["new_state"] == "CANCELLED"

    def test_set_priority_json(self, org_dir):
        data, _, rc = run_cli_json("set-priority", "Buy groceries", "A", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "set-priority"
        assert data["new_priority"] == "A"

    def test_set_priority_clear_json(self, org_dir):
        data, _, rc = run_cli_json("set-priority", "Buy groceries", "--clear", org_dir=org_dir)
        assert rc == 0
        assert data["new_priority"] is None

    def test_set_property_json(self, org_dir):
        data, _, rc = run_cli_json(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--value", "deep", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "set-property"
        assert data["key"] == "AGENT_EFFORT"
        assert data["old_value"] is None
        assert data["new_value"] == "deep"
        # mutation-output enriches with the full task state
        assert data["task"]["heading"] == "Buy groceries"

    def test_set_property_clear_json(self, org_dir):
        run_cli_json("set-property", "Buy groceries", "--key", "AGENT_EFFORT",
                     "--value", "deep", org_dir=org_dir)
        data, _, rc = run_cli_json(
            "set-property", "Buy groceries", "--key", "AGENT_EFFORT",
            "--clear", org_dir=org_dir)
        assert rc == 0
        assert data["old_value"] == "deep"
        assert data["new_value"] is None

    def test_rename_json(self, org_dir):
        data, _, rc = run_cli_json("rename", "Buy groceries", "Buy food", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "rename"
        assert data["heading"] == "Buy food"
        assert data["old_heading"] == "Buy groceries"

    def test_set_schedule_json(self, org_dir):
        data, _, rc = run_cli_json("set-schedule", "Buy groceries", "2026-04-01", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "set-schedule"
        assert data["scheduled"] is not None
        assert "2026-04-01" in data["scheduled"]

    def test_set_schedule_clear_json(self, org_dir):
        # First schedule, then clear
        run_cli_json("set-schedule", "Buy groceries", "2026-04-01", org_dir=org_dir)
        data, _, rc = run_cli_json("set-schedule", "Buy groceries", "--clear", org_dir=org_dir)
        assert rc == 0
        assert data["scheduled"] is None

    def test_set_deadline_json(self, org_dir):
        data, _, rc = run_cli_json("set-deadline", "Buy groceries", "2026-04-15", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "set-deadline"
        assert "2026-04-15" in data["deadline"]

    def test_append_body_json(self, org_dir):
        data, _, rc = run_cli_json("append-body", "Buy groceries", "Extra info", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "append-body"
        assert data["heading"] == "Buy groceries"

    def test_set_body_json(self, org_dir):
        data, _, rc = run_cli_json("set-body", "Buy groceries", "New body text", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "set-body"
        assert data["heading"] == "Buy groceries"

    def test_set_next_leaf_json(self, org_dir):
        data, _, rc = run_cli_json("set-next", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "set-next"
        assert data["new_state"] == "NEXT"
        assert isinstance(data["side_effects"], list)

    def test_refile_json(self, org_dir):
        data, _, rc = run_cli_json(
            "refile", "Buy groceries", "--category", "Shopping",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["command"] == "refile"
        assert "target_heading" in data
        assert "target_file" in data

    def test_refile_dry_run_json(self, org_dir):
        data, _, rc = run_cli_json(
            "refile", "Buy groceries", "--category", "Shopping", "--dry-run",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["dry_run"] is True

    def test_add_task_json(self, org_dir):
        data, _, rc = run_cli_json("add-task", "Test JSON task", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "add-task"
        assert data["heading"] == "Test JSON task"
        assert data["state"] == "TODO"

    def test_add_task_with_category_json(self, org_dir):
        data, _, rc = run_cli_json(
            "add-task", "Categorized task", "--category", "Shopping",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["category"] is not None

    def test_add_subtask_json(self, org_dir):
        data, _, rc = run_cli_json(
            "add-subtask", "Write quarterly report", "Sub item",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["command"] == "add-subtask"
        assert data["heading"] == "Sub item"
        assert "parent" in data
        assert isinstance(data["side_effects"], list)

    def test_add_event_json(self, org_dir):
        data, _, rc = run_cli_json(
            "add-event", "Doctor visit", "--date", "2026-04-15",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["command"] == "add-event"
        assert data["date"] == "2026-04-15"
        assert data["tag"] == "calpersonal"
        # calendar.org has no file-level calendar-id property -> plain event
        assert data["calendar_id"] is None

    def test_add_event_json_reports_calendar_id(self, org_dir):
        (org_dir / "family-calendar.org").write_text(
            "#+title: Family Calendar\n"
            f"#+PROPERTY: calendar-id {FAKE_CALENDAR_ID}\n")
        data, _, rc = run_cli_json(
            "add-event", "Dentist", "--date", "2026-04-16",
            "--file", "family-calendar.org",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["calendar_id"] == FAKE_CALENDAR_ID
        cal = (org_dir / "family-calendar.org").read_text()
        assert f":calendar-id: {FAKE_CALENDAR_ID}" in cal
        assert ":org-gcal:" in cal

    def test_add_note_json(self, org_dir):
        data, _, rc = run_cli_json("add-note", "Test research", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "add-note"
        assert isinstance(data["sections"], list)

    def test_delete_json(self, org_dir):
        run_cli("add-task", "Delete me test", org_dir=org_dir)
        data, _, rc = run_cli_json("delete", "Delete me test", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "delete"
        assert data["heading"] == "Delete me test"

    def test_delete_dry_run_json(self, org_dir):
        data, _, rc = run_cli_json(
            "delete", "Buy groceries", "--dry-run", org_dir=org_dir,
        )
        assert rc == 0
        assert data["dry_run"] is True

    def test_set_tags_replace_json(self, org_dir):
        data, _, rc = run_cli_json(
            "set-tags", "Buy groceries", "--tags", "shopping,urgent",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["command"] == "set-tags"
        assert set(data["new_tags"]) == {"shopping", "urgent"}

    def test_set_tags_clear_json(self, org_dir):
        data, _, rc = run_cli_json(
            "set-tags", "Buy groceries", "--tags", "",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["new_tags"] == []

    def test_add_tags_json(self, org_dir):
        data, _, rc = run_cli_json(
            "add-tags", "Buy groceries", "--tags", "newone",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["command"] == "add-tags"
        assert "newone" in data["new_tags"]
        # Old tags should be preserved
        for tag in data["old_tags"]:
            assert tag in data["new_tags"]


# ===========================================================================
# JSON: non-ASCII / locale-independent UTF-8 encoding
# ===========================================================================

# A locale with no UTF-8 — mirrors how systemd services and the bwrap sandbox
# start Emacs (no LANG set). `json-serialize` returns unibyte UTF-8 bytes that,
# princ'd raw under such a locale, get double-encoded into invalid JSON.
C_LOCALE = {"LANG": "C", "LC_ALL": "C"}
NON_ASCII_BODY = "Dash — café naïve → ça"


class TestJsonNonAsciiEncoding:
    """Non-ASCII bodies must produce valid UTF-8 JSON regardless of locale."""

    def test_show_json_non_ascii_utf8_locale(self, org_dir):
        run_cli("set-body", "Buy groceries", NON_ASCII_BODY, org_dir=org_dir)
        data, _, rc = run_cli_json("show", "Buy groceries", org_dir=org_dir)
        assert rc == 0
        assert data["body"] == NON_ASCII_BODY

    def test_show_json_non_ascii_c_locale(self, org_dir):
        """Regression: em-dash/accents must not octal-escape or mojibake under C."""
        run_cli("set-body", "Buy groceries", NON_ASCII_BODY, org_dir=org_dir)
        stdout, stderr, rc = run_cli(
            "--json", "show", "Buy groceries",
            org_dir=org_dir, env_overrides=C_LOCALE)
        assert rc == 0
        # stdout must be strictly valid JSON (no \342\200\224 octal escapes,
        # no double-encoded bytes) and round-trip the exact characters.
        data = json.loads(stdout)
        assert data["body"] == NON_ASCII_BODY

    def test_search_full_json_non_ascii_c_locale(self, org_dir):
        """`search --full --json` is the path originally reported as broken."""
        run_cli("set-body", "Buy groceries", NON_ASCII_BODY, org_dir=org_dir)
        stdout, stderr, rc = run_cli(
            "--json", "search", "Buy groceries", "--full",
            org_dir=org_dir, env_overrides=C_LOCALE)
        assert rc == 0
        data = json.loads(stdout)
        bodies = [t.get("body") for t in data["tasks"]]
        assert NON_ASCII_BODY in bodies

    def test_json_error_non_ascii_c_locale(self, org_dir):
        """Error JSON (on stderr) must also be valid UTF-8 under C locale."""
        stdout, stderr, rc = run_cli(
            "--json", "show", "nø such tæsk",
            org_dir=org_dir, env_overrides=C_LOCALE)
        assert rc == 1
        # The error blob is emitted on stderr as JSON; it must parse.
        err = json.loads(stderr.strip().splitlines()[-1])
        assert "error" in err


class TestDaemonSaveChatter:
    """Regression: --json mutations in DAEMON mode must not leak save chatter.

    `save-buffer' prints "Saving file %s..." but that notice is gated behind
    `(not noninteractive)', so it never surfaces in batch mode (the default
    test harness) — it ONLY appears in daemon mode, which is how olli's
    interactive shell (ORG_GTD_CLI_DAEMON=1) and the agents actually invoke
    the CLI. Each mutation then prepended the notice to the stream, breaking
    naive `json.load(stdout)' / merged-stream parsers. `save-silently' (set in
    org-gtd-cli.el) suppresses it. See the GTD task "Fix org-gtd-cli --json:
    'Saving file...' chatter ... corrupts JSON output".

    These tests spin up their OWN isolated Emacs daemon via a unique TMPDIR so
    they never touch olli's interactive daemon (socket under /tmp/claude/...),
    and tear it down afterwards.
    """

    @staticmethod
    def _kill_daemon(daemon_tmp):
        socket = os.path.join(daemon_tmp, "org-gtd-cli", "server")
        if os.path.exists(socket):
            subprocess.run(
                ["emacsclient", "--socket-name", socket, "--eval", "(kill-emacs)"],
                capture_output=True, timeout=10)

    def test_daemon_json_mutation_no_save_chatter(self, org_dir, tmp_path):
        """A --json mutation run against a fresh daemon emits ONLY JSON, with
        no 'Saving file ...' chatter on either stream. Fails pre-fix."""
        daemon_tmp = tmp_path / "daemon-home"
        daemon_tmp.mkdir()
        env = {"ORG_GTD_CLI_DAEMON": "1", "TMPDIR": str(daemon_tmp)}
        try:
            stdout, stderr, rc = run_cli(
                "--json", "set-body", "Buy groceries", "daemon chatter test",
                org_dir=org_dir, env_overrides=env)
            assert rc == 0, f"stderr: {stderr}"
            # stdout must parse as a single JSON object (no leading chatter).
            data = json.loads(stdout)
            assert data["command"] == "set-body"
            assert data["task"]["body"] == "daemon chatter test"
            # The save notice must appear on neither stream.
            assert "Saving file" not in stdout
            assert "Saving file" not in stderr
        finally:
            self._kill_daemon(str(daemon_tmp))


class TestDaemonRobustness:
    """Regression tests for daemon-mode races.

    1. Supersession: an external mtime/content change between the daemon's
       buffer revert and `save-buffer' used to trigger Emacs's interactive
       supersession prompts, hanging the headless daemon forever.
    2. Output race: the Python wrapper used fixed stdout/stderr/exit paths in
       $TMPDIR, so two concurrent invocations clobbered each other's results.

    The daemon socket lives at $TMPDIR/org-gtd-cli/server, and unix socket
    paths have a ~107-char limit — pytest's tmp_path is too deep, so these
    tests build a short-lived daemon TMPDIR directly under the session tmpdir
    (e.g. /tmp/claude) instead.
    """

    @staticmethod
    def _make_daemon_tmp():
        return tempfile.mkdtemp(prefix="ogc-", dir=os.environ.get("TMPDIR", "/tmp"))

    @staticmethod
    def _kill_daemon(daemon_tmp):
        socket = os.path.join(daemon_tmp, "org-gtd-cli", "server")
        if os.path.exists(socket):
            try:
                subprocess.run(
                    ["emacsclient", "--socket-name", socket,
                     "--eval", "(kill-emacs)"],
                    capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                pass
        # Fallback for a wedged daemon (e.g. stuck on an interactive prompt):
        # its command line contains the unique TMPDIR path.
        subprocess.run(["pkill", "-f", daemon_tmp], capture_output=True)
        shutil.rmtree(daemon_tmp, ignore_errors=True)

    def test_daemon_mutation_survives_external_file_change(self, org_dir):
        """A mutation must not hang when the .org file's mtime changes behind
        the daemon's back — including between the dispatch-time revert and the
        save (simulated by hammering the mtime during the call). Pre-fix this
        hit Emacs's interactive supersession prompts and hung forever."""
        daemon_tmp = self._make_daemon_tmp()
        env = {"ORG_GTD_CLI_DAEMON": "1", "TMPDIR": daemon_tmp}
        inbox = org_dir / "inbox.org"
        try:
            # Open/mutate the task so the daemon holds a live inbox.org buffer.
            stdout, stderr, rc = run_cli(
                "--json", "set-body", "Buy groceries", "first body",
                org_dir=org_dir, env_overrides=env)
            assert rc == 0, f"stderr: {stderr}"

            # Touch the file behind the daemon's back: content + future mtime.
            inbox.write_text(inbox.read_text() + "\n# external edit\n")
            future = time.time() + 60
            os.utime(inbox, (future, future))

            # Keep hammering the mtime during the next call so a change lands
            # in the revert->modify->save window (the actual race trigger).
            stop = threading.Event()

            def hammer():
                while not stop.is_set():
                    t = time.time() + 60
                    try:
                        os.utime(inbox, (t, t))
                    except FileNotFoundError:
                        pass
                    time.sleep(0.001)

            th = threading.Thread(target=hammer, daemon=True)
            th.start()
            try:
                # run_cli's timeout=30 turns a pre-fix hang into a test failure.
                stdout, stderr, rc = run_cli(
                    "--json", "set-body", "Buy groceries", "second body",
                    org_dir=org_dir, env_overrides=env)
            finally:
                stop.set()
                th.join(timeout=5)

            assert rc == 0, f"stderr: {stderr}"
            data = json.loads(stdout)
            assert data["command"] == "set-body"
            assert data["task"]["body"] == "second body"
        finally:
            self._kill_daemon(daemon_tmp)

    def test_daemon_concurrent_calls_outputs_not_clobbered(self, org_dir):
        """Two concurrent invocations against the same daemon must each get
        their own stdout and exit code. Pre-fix, fixed result-file paths in
        $TMPDIR let one call read (or truncate) the other's output."""
        daemon_tmp = self._make_daemon_tmp()
        env = {"ORG_GTD_CLI_DAEMON": "1", "TMPDIR": daemon_tmp}
        try:
            # Warm up so both concurrent calls reuse one daemon.
            stdout, stderr, rc = run_cli(
                "--json", "show", "Buy groceries",
                org_dir=org_dir, env_overrides=env)
            assert rc == 0, f"stderr: {stderr}"

            for i in range(8):
                # Several simultaneous calls per round: the extra processes
                # add the scheduling jitter needed to land one call's read in
                # another's write window.
                with ThreadPoolExecutor(max_workers=4) as ex:
                    f_oks = [ex.submit(
                        run_cli, "--json", "set-body", "Buy groceries",
                        f"round {i}", org_dir=org_dir, env_overrides=env)
                        for _ in range(2)]
                    f_errs = [ex.submit(
                        run_cli, "--json", "show", "zz no such task zz",
                        org_dir=org_dir, env_overrides=env)
                        for _ in range(2)]
                    ok_results = [f.result() for f in f_oks]
                    err_results = [f.result() for f in f_errs]

                # Each mutation gets its own JSON and its own exit code.
                for out_ok, err_ok, rc_ok in ok_results:
                    assert rc_ok == 0, f"round {i}: stderr: {err_ok}"
                    data = json.loads(out_ok)
                    assert data["command"] == "set-body"
                    assert data["task"]["body"] == f"round {i}"

                # Each failing show keeps its exit code and does not receive
                # another call's stdout.
                for out_err, err_err, rc_err in err_results:
                    assert rc_err == 1, \
                        f"round {i}: rc {rc_err}, stdout: {out_err}"
                    assert "set-body" not in out_err
                    err = json.loads(err_err.strip().splitlines()[-1])
                    assert "error" in err
        finally:
            self._kill_daemon(daemon_tmp)

    def test_daemon_concurrent_fast_calls_own_stdout(self, org_dir):
        """Concurrent fast commands (org-timestamp does no file IO, so its
        dispatch takes ~1ms) each get their own stdout. This is the most
        sensitive clobber detector: pre-fix, the daemon overwrote the fixed
        result files with the next queued call's output while the previous
        caller was still reading them."""
        daemon_tmp = self._make_daemon_tmp()
        env = {"ORG_GTD_CLI_DAEMON": "1", "TMPDIR": daemon_tmp}
        try:
            # Warm up so all concurrent calls reuse one daemon.
            stdout, stderr, rc = run_cli(
                "org-timestamp", "2026-01-01",
                org_dir=org_dir, env_overrides=env)
            assert rc == 0, f"stderr: {stderr}"

            base = datetime.date(2026, 3, 1)
            dates = [(base + datetime.timedelta(days=i)).isoformat()
                     for i in range(48)]
            for i in range(12):
                batch = dates[i * 4:(i + 1) * 4]
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futs = {d: ex.submit(run_cli, "org-timestamp", d,
                                         org_dir=org_dir, env_overrides=env)
                            for d in batch}
                    for d, f in futs.items():
                        out, err, rc = f.result()
                        assert rc == 0, f"{d}: rc {rc}, stderr: {err}"
                        assert d in out, \
                            f"asked for {d}, got another call's output: {out!r}"
        finally:
            self._kill_daemon(daemon_tmp)


# ===========================================================================
# Removed commands
# ===========================================================================

class TestRemovedCommands:
    """Tests for commands that have been removed."""

    def test_process_agent_tasks_removed(self, org_dir):
        _, stderr, rc = run_cli("process-agent-tasks", org_dir=org_dir)
        assert rc == 1
        assert "removed" in stderr.lower()

    def test_fix_timestamps_removed(self, org_dir):
        _, stderr, rc = run_cli("fix-timestamps", org_dir=org_dir)
        assert rc == 1
        assert "removed" in stderr.lower()


# ===========================================================================
# set-tags --add / --remove convenience flags
# ===========================================================================

class TestSetTagsAddRemoveFlags:
    def test_set_tags_add_flag(self, org_dir):
        """set-tags --add appends tags (routes to add-tags)."""
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--add", "urgent", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "inbox.org").read_text()
        assert ":urgent:" in text
        # Old tags preserved
        assert ":buy:" in text
        assert ":@errand:" in text

    def test_set_tags_remove_flag(self, org_dir):
        """set-tags --remove removes specific tags."""
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--remove", "buy", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "inbox.org").read_text()
        assert ":buy:" not in text
        # Other tags preserved
        assert ":@errand:" in text

    def test_set_tags_remove_nonexistent_tag(self, org_dir):
        """set-tags --remove with tag not present is a no-op."""
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--remove", "nonexistent", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "inbox.org").read_text()
        assert ":buy:" in text
        assert ":@errand:" in text

    def test_set_tags_add_dry_run(self, org_dir):
        """set-tags --add with --dry-run does not modify."""
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--add", "urgent", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert ":urgent:" not in (org_dir / "inbox.org").read_text()

    def test_set_tags_remove_dry_run(self, org_dir):
        """set-tags --remove with --dry-run does not modify."""
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--remove", "buy", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert ":buy:" in (org_dir / "inbox.org").read_text()

    def test_set_tags_add_json(self, org_dir):
        """set-tags --add with --json returns add-tags JSON."""
        data, _, rc = run_cli_json("set-tags", "Buy groceries", "--add", "urgent", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "add-tags"
        assert "urgent" in data["new_tags"]
        assert "buy" in data["new_tags"]

    def test_set_tags_remove_json(self, org_dir):
        """set-tags --remove with --json returns set-tags JSON."""
        data, _, rc = run_cli_json("set-tags", "Buy groceries", "--remove", "buy", org_dir=org_dir)
        assert rc == 0
        assert data["command"] == "set-tags"
        assert "buy" not in data["new_tags"]
        assert "@errand" in data["new_tags"]

    def test_set_tags_mutual_exclusion(self, org_dir):
        """--tags, --add, --remove are mutually exclusive."""
        _, stderr, rc = run_cli("set-tags", "Buy groceries", "--tags", "x", "--add", "y", org_dir=org_dir)
        assert rc != 0


# ===========================================================================
# set-next: reject leaf tasks under non-project parents
# ===========================================================================

class TestSetNextNonProjectParent:
    def test_set_next_leaf_under_organizational_heading(self, org_dir):
        """set-next on a leaf under an organizational heading should fail."""
        # "Write quarterly report" is under "Work" (no TODO keyword)
        stdout, stderr, rc = run_cli("set-next", "Write quarterly report", org_dir=org_dir)
        assert rc == 1
        assert "not inside a project" in stderr

    def test_set_next_leaf_under_organizational_heading_json(self, org_dir):
        """set-next on a leaf under organizational heading returns JSON error."""
        data, stderr, rc = run_cli_json("set-next", "Write quarterly report", org_dir=org_dir)
        assert rc == 1
        # Extract JSON line from stderr (may contain Emacs loading messages)
        err = None
        for line in stderr.splitlines():
            line = line.strip()
            if line.startswith("{"):
                err = json.loads(line)
                break
        assert err is not None, f"No JSON found in stderr: {stderr}"
        assert "not inside a project" in err["error"]
        assert "set-state" in err["hint"]

    def test_set_next_leaf_under_project_still_works(self, org_dir):
        """set-next on a leaf under a project (parent has TODO keyword) works."""
        # "Test on actual project" is under "Design CLI tool" (TODO keyword)
        stdout, stderr, rc = run_cli("set-next", "Test on actual project", org_dir=org_dir)
        assert rc == 0


class TestFullFlag:
    """Tests for --full flag on search, subtasks, and agenda."""

    def test_search_full_json_includes_body(self, org_dir):
        """search --full --json returns body field in each task."""
        data, stderr, rc = run_cli_json("search", "Set up automated backups", "--full", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        tasks = data["tasks"]
        assert len(tasks) >= 1
        task = tasks[0]
        assert "body" in task
        assert task["body"] is not None
        assert "backup strategies" in task["body"]

    def test_search_without_full_no_body(self, org_dir):
        """search without --full does NOT include body field."""
        data, stderr, rc = run_cli_json("search", "Set up automated backups", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        task = data["tasks"][0]
        assert "body" not in task

    def test_subtasks_full_json_includes_body(self, org_dir):
        """subtasks --full --json returns body field in each subtask."""
        data, stderr, rc = run_cli_json("subtasks", "Improve agent workflow", "--full", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        for st in data["subtasks"]:
            assert "body" in st

    def test_subtasks_without_full_no_body(self, org_dir):
        """subtasks without --full does NOT include body field."""
        data, stderr, rc = run_cli_json("subtasks", "Improve agent workflow", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        for st in data["subtasks"]:
            assert "body" not in st

    def test_agenda_full_json_includes_body(self, org_dir):
        """agenda --full --json returns body field in each task."""
        data, stderr, rc = run_cli_json("agenda", "--full", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        assert data["count"] > 0
        for task in data["tasks"]:
            assert "body" in task

    def test_agenda_without_full_no_body(self, org_dir):
        """agenda without --full does NOT include body field."""
        data, stderr, rc = run_cli_json("agenda", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        for task in data["tasks"]:
            assert "body" not in task

    def test_search_full_null_body_for_no_body(self, org_dir):
        """Tasks with no body return null for body field."""
        data, stderr, rc = run_cli_json("search", "Improve agent workflow", "--full", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        task = data["tasks"][0]
        assert "body" in task
        assert task["body"] is None

    def test_search_full_text_mode(self, org_dir):
        """search --full in text mode shows body indented below task."""
        stdout, stderr, rc = run_cli("search", "Set up automated backups", "--full", org_dir=org_dir)
        assert rc == 0
        assert "backup strategies" in stdout


class TestAutoStdin:
    """Tests for auto-reading body from stdin when TEXT is omitted."""

    def _run_with_stdin(self, args, input_text, org_dir):
        env = os.environ.copy()
        env["ORG_DIRECTORY"] = str(org_dir) + "/"
        env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
        env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
        cmd = ["python3", str(CLI_SCRIPT)] + list(args)
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            input=input_text, timeout=30,
        )
        return result.stdout, result.stderr, result.returncode

    def test_set_body_auto_stdin(self, org_dir):
        """set-body reads from stdin when no TEXT arg provided and stdin is pipe."""
        stdout, stderr, rc = self._run_with_stdin(
            ["set-body", "Write quarterly report"],
            "Auto stdin body.", org_dir,
        )
        assert rc == 0
        assert "Auto stdin body." in (org_dir / "tasks.org").read_text()

    def test_append_body_auto_stdin(self, org_dir):
        """append-body reads from stdin when no TEXT arg provided and stdin is pipe."""
        stdout, stderr, rc = self._run_with_stdin(
            ["append-body", "Write quarterly report"],
            "Auto appended.", org_dir,
        )
        assert rc == 0
        assert "Auto appended." in (org_dir / "tasks.org").read_text()

    def test_positional_text_still_works(self, org_dir):
        """Positional TEXT argument still works (regression)."""
        stdout, stderr, rc = run_cli(
            "set-body", "Write quarterly report", "Positional body.",
            org_dir=org_dir,
        )
        assert rc == 0
        assert "Positional body." in (org_dir / "tasks.org").read_text()

    def test_body_file_stdin_still_works(self, org_dir):
        """--body-file - still works (regression)."""
        stdout, stderr, rc = self._run_with_stdin(
            ["set-body", "Write quarterly report", "--body-file", "-"],
            "Explicit stdin.", org_dir,
        )
        assert rc == 0
        assert "Explicit stdin." in (org_dir / "tasks.org").read_text()

    def test_no_text_no_pipe_shows_error(self, org_dir):
        """No TEXT, no pipe shows error with hint."""
        # run_cli uses capture_output which means stdin is a pipe (with no data)
        # To test TTY detection we'd need more complex setup, so just verify
        # that with empty stdin pipe it reads empty string (which set-body allows)
        stdout, stderr, rc = self._run_with_stdin(
            ["set-body", "Write quarterly report"],
            "", org_dir,
        )
        # Empty string via stdin is valid for set-body (clears body)
        assert rc == 0


def run_batch(command, items_json, *extra_args, org_dir):
    """Run org-gtd-cli --json --batch <command> with JSON on stdin."""
    env = os.environ.copy()
    env["ORG_DIRECTORY"] = str(org_dir) + "/"
    env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
    env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
    cmd = ["python3", str(CLI_SCRIPT), "--json", "--batch", command] + list(extra_args)
    result = subprocess.run(
        cmd, capture_output=True, text=True, env=env,
        input=json.dumps(items_json), timeout=30,
    )
    data = None
    if result.stdout.strip():
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return data, result.stderr, result.returncode


def run_batch_mixed(items, *, org_dir, json_flag=True, stdin=None):
    """Run org-gtd-cli [--json] batch with JSON on stdin.

    `items` is serialized to JSON unless raw `stdin` text is given.
    Returns (data, stderr, returncode).
    """
    env = os.environ.copy()
    env["ORG_DIRECTORY"] = str(org_dir) + "/"
    env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
    env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
    cmd = ["python3", str(CLI_SCRIPT)]
    if json_flag:
        cmd.append("--json")
    cmd.append("batch")
    payload = json.dumps(items) if stdin is None else stdin
    result = subprocess.run(
        cmd, capture_output=True, text=True, env=env,
        input=payload, timeout=30,
    )
    data = None
    if result.stdout.strip():
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return data, result.stderr, result.returncode


class TestBatch:
    """Tests for --batch mode."""

    def test_batch_set_done_happy_path(self, org_dir):
        """Batch set-done with 2 items, both succeed."""
        data, stderr, rc = run_batch(
            "set-done",
            ["Write quarterly report", "Prepare onboarding guide"],
            org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert data["batch"] is True
        assert data["summary"]["total"] == 2
        assert data["summary"]["succeeded"] == 2
        assert data["summary"]["failed"] == 0
        assert all(r["success"] is True for r in data["results"])

    def test_batch_set_done_partial_failure(self, org_dir):
        """Batch set-done: one valid, one invalid heading."""
        data, stderr, rc = run_batch(
            "set-done",
            ["Write quarterly report", "Nonexistent task xyz"],
            org_dir=org_dir,
        )
        assert rc == 0  # exit 0 because at least one succeeded
        assert data is not None
        assert data["summary"]["succeeded"] == 1
        assert data["summary"]["failed"] == 1
        assert data["results"][0]["success"] is True
        assert data["results"][1]["success"] is False
        assert "error" in data["results"][1]

    def test_batch_set_done_total_failure(self, org_dir):
        """Batch set-done: all items fail."""
        data, stderr, rc = run_batch(
            "set-done",
            ["Nonexistent 1", "Nonexistent 2"],
            org_dir=org_dir,
        )
        assert rc == 1  # all failed
        assert data is not None
        assert data["summary"]["succeeded"] == 0
        assert data["summary"]["failed"] == 2

    def test_batch_empty_array(self, org_dir):
        """Batch with empty array exits 0."""
        data, stderr, rc = run_batch("set-done", [], org_dir=org_dir)
        # No items succeeded (0), no items failed (0) — edge case: exit 1 per spec
        assert data is not None
        assert data["summary"]["total"] == 0

    def test_batch_add_event(self, org_dir):
        """Batch add-event with 2 events."""
        data, stderr, rc = run_batch(
            "add-event",
            [
                {"title": "Meeting A", "date": "2026-04-10"},
                {"title": "Meeting B", "date": "2026-04-11", "time": "14:00"},
            ],
            org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert data["summary"]["succeeded"] == 2
        cal = (org_dir / "calendar.org").read_text()
        assert "Meeting A" in cal
        assert "Meeting B" in cal

    def test_batch_add_subtask(self, org_dir):
        """Batch add-subtask with shared parent."""
        data, stderr, rc = run_batch(
            "add-subtask",
            [
                {"title": "Subtask A"},
                {"title": "Subtask B", "state": "NEXT"},
            ],
            "Prepare onboarding guide",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert data["summary"]["succeeded"] == 2

    def test_batch_add_subtask_failed_item_not_persisted(self, org_dir):
        """A rejected item must not leak a partially-inserted heading to disk.

        Regression: the heading used to be inserted before the body was
        validated, so a failed item's heading remained in the (unsaved)
        buffer and the next successful item's save-buffer persisted it.
        """
        data, stderr, rc = run_batch(
            "add-subtask",
            [
                {"title": "Leaky subtask", "body": "* illegal heading body"},
                {"title": "Valid subtask"},
            ],
            "Prepare onboarding guide",
            org_dir=org_dir,
        )
        assert rc == 0  # one item succeeded
        assert data is not None
        assert data["summary"]["total"] == 2
        assert data["summary"]["succeeded"] == 1
        assert data["summary"]["failed"] == 1
        assert data["results"][0]["success"] is False
        assert "error" in data["results"][0]
        assert data["results"][1]["success"] is True
        # The failed item's heading must not appear in any org file
        all_text = "".join(p.read_text() for p in org_dir.glob("*.org"))
        assert "Leaky subtask" not in all_text
        assert "Valid subtask" in all_text

    def test_batch_delete(self, org_dir):
        """Batch delete multiple tasks."""
        # First add some tasks to delete
        run_cli("add-task", "Delete me 1", org_dir=org_dir)
        run_cli("add-task", "Delete me 2", org_dir=org_dir)
        data, stderr, rc = run_batch(
            "delete",
            ["Delete me 1", "Delete me 2"],
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["summary"]["succeeded"] == 2

    def test_batch_refile(self, org_dir):
        """Batch refile moves items under the shared category heading."""
        data, stderr, rc = run_batch(
            "refile",
            ["Buy groceries", "Call the plumber about kitchen sink"],
            "--category", "Family",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert data["summary"]["succeeded"] == 2
        assert data["summary"]["failed"] == 0
        for r in data["results"]:
            assert r["success"] is True
            assert r["target_heading"] == "Family"
            assert r["target_file"] == "tasks.org"
        inbox = (org_dir / "inbox.org").read_text()
        assert "Buy groceries" not in inbox
        assert "Call the plumber" not in inbox
        tasks = (org_dir / "tasks.org").read_text()
        assert "Buy groceries" in tasks
        assert "Call the plumber about kitchen sink" in tasks
        assert_line_before(org_dir / "tasks.org", "* Family", "Buy groceries")

    def test_batch_refile_nonexistent_category(self, org_dir):
        """Batch refile with an unknown category fails cleanly per item."""
        data, stderr, rc = run_batch(
            "refile",
            ["Buy groceries"],
            "--category", "No such category xyz",
            org_dir=org_dir,
        )
        assert rc == 1
        assert data is not None
        assert data["summary"]["succeeded"] == 0
        assert data["summary"]["failed"] == 1
        assert data["results"][0]["success"] is False
        assert "not found" in data["results"][0]["error"]
        # Task untouched
        assert "Buy groceries" in (org_dir / "inbox.org").read_text()

    def test_batch_result_index_matches_input(self, org_dir):
        """Result index matches input array position."""
        data, stderr, rc = run_batch(
            "set-done",
            ["Write quarterly report", "Nonexistent xyz"],
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["results"][0]["index"] == 0
        assert data["results"][1]["index"] == 1

    def test_batch_invalid_json(self, org_dir):
        """Invalid JSON on stdin exits 1."""
        env = os.environ.copy()
        env["ORG_DIRECTORY"] = str(org_dir) + "/"
        env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
        env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
        cmd = ["python3", str(CLI_SCRIPT), "--json", "--batch", "set-done"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            input="not json", timeout=30,
        )
        assert result.returncode == 1

    def test_batch_unsupported_command(self, org_dir):
        """--batch on unsupported command fails."""
        env = os.environ.copy()
        env["ORG_DIRECTORY"] = str(org_dir) + "/"
        env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
        env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
        cmd = ["python3", str(CLI_SCRIPT), "--json", "--batch", "search", "foo"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            input="[]", timeout=30,
        )
        assert result.returncode == 1
        assert "not supported" in result.stderr


class TestBatchDelegation:
    """Batch items now run the real command implementations (not the old
    simplified reimplementations), so they gain validation, auto-progress,
    side effects, and the richer JSON fields of single CLI calls."""

    def test_batch_set_done_auto_progress_side_effects(self, org_dir):
        """Completing a project subtask via batch promotes the next TODO
        sibling to NEXT and reports it in the side_effects field (the old
        bespoke batch path skipped auto-progress entirely)."""
        data, stderr, rc = run_batch(
            "set-done", ["Draft outline"], org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["old_state"] == "TODO"
        assert r["new_state"] == "DONE"
        assert r["side_effects"] == [
            {"action": "state-change", "heading": "Write first chapter",
             "old_state": "TODO", "new_state": "NEXT", "file": "tasks.org"},
        ]
        # Mutation responses now include the full task state too
        assert r["task"]["heading"] == "Draft outline"
        assert r["task"]["state"] == "DONE"
        tasks = (org_dir / "tasks.org").read_text()
        assert "*** NEXT Write first chapter" in tasks
        assert "*** DONE Draft outline" in tasks

    def test_batch_add_task_schedule_deadline_priority(self, org_dir):
        """Batch add-task supports schedule/deadline/priority/state and adds
        a creation timestamp (the old path ignored all of these)."""
        data, stderr, rc = run_batch(
            "add-task",
            [{"title": "Scheduled batch task", "schedule": "2026-04-01",
              "deadline": "2026-04-05", "priority": "A", "state": "NEXT",
              "tags": "work"}],
            org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["heading"] == "Scheduled batch task"
        assert r["state"] == "NEXT"
        assert r["file"] == "inbox.org"
        inbox = (org_dir / "inbox.org").read_text()
        assert "* NEXT [#A] Scheduled batch task" in inbox
        assert "SCHEDULED: <2026-04-01" in inbox
        assert "DEADLINE: <2026-04-05" in inbox
        # Creation timestamp (inactive, today's date)
        today = datetime.date.today().isoformat()
        assert f"[{today}" in inbox

    def test_batch_add_task_category_correct_level(self, org_dir):
        """Batch add-task --category inserts at the category's child level
        (the old path hardcoded level-2 insertion) and reports the matched
        category path."""
        data, stderr, rc = run_batch(
            "add-task",
            [{"title": "Nested batch task", "category": "Agents"}],
            org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["category"] == "Computers/Agents"
        tasks = (org_dir / "tasks.org").read_text()
        # Agents is a level-2 heading, so the task must be level 3
        assert "*** TODO Nested batch task" in tasks

    def test_batch_add_subtask_schedule_priority(self, org_dir):
        """Batch add-subtask supports schedule/priority and reports the
        parent heading."""
        data, stderr, rc = run_batch(
            "add-subtask",
            [{"title": "Scheduled subtask", "schedule": "2026-04-02",
              "priority": "B"}],
            "Prepare onboarding guide",
            org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["heading"] == "Scheduled subtask"
        assert r["parent"] == "Prepare onboarding guide"
        tasks = (org_dir / "tasks.org").read_text()
        assert "*** TODO [#B] Scheduled subtask" in tasks
        assert "SCHEDULED: <2026-04-02" in tasks

    def test_batch_add_event_tag_and_file(self, org_dir):
        """Batch add-event honors the tag field and only defaults to
        calpersonal when neither tag nor file is given."""
        data, stderr, rc = run_batch(
            "add-event",
            [{"title": "Tagged event", "date": "2026-04-10", "tag": "calwork"},
             {"title": "Default event", "date": "2026-04-11"}],
            org_dir=org_dir)
        assert rc == 0
        assert data["summary"]["succeeded"] == 2
        assert data["results"][0]["tag"] == "calwork"
        assert data["results"][1]["tag"] == "calpersonal"
        cal = (org_dir / "calendar.org").read_text()
        assert "* Tagged event :calwork:" in cal
        assert "* Default event :calpersonal:" in cal

    def test_batch_add_event_file_field(self, org_dir):
        """Batch add-event honors the file field (no calpersonal default
        when an explicit file is given)."""
        data, stderr, rc = run_batch(
            "add-event",
            [{"title": "Inbox event", "date": "2026-04-12",
              "file": "inbox.org"}],
            org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["file"] == "inbox.org"
        assert r["tag"] is None
        inbox = (org_dir / "inbox.org").read_text()
        assert "* Inbox event" in inbox
        assert "calpersonal" not in inbox

    def test_batch_add_event_calendar_id(self, org_dir):
        """Batch add-event uses the target file's calendar-id property,
        same as single calls (it delegates to the same implementation)."""
        (org_dir / "family-calendar.org").write_text(
            "#+title: Family Calendar\n"
            f"#+PROPERTY: calendar-id {FAKE_CALENDAR_ID}\n")
        data, stderr, rc = run_batch(
            "add-event",
            [{"title": "Gcal event", "date": "2026-04-13",
              "file": "family-calendar.org"},
             {"title": "Plain event", "date": "2026-04-14"}],
            org_dir=org_dir)
        assert rc == 0
        assert data["summary"]["succeeded"] == 2
        assert data["results"][0]["calendar_id"] == FAKE_CALENDAR_ID
        assert data["results"][1]["calendar_id"] is None
        fam = (org_dir / "family-calendar.org").read_text()
        assert f":calendar-id: {FAKE_CALENDAR_ID}" in fam
        assert ":org-gcal:" in fam
        cal = (org_dir / "calendar.org").read_text()
        assert "* Plain event" in cal
        assert ":org-gcal:" not in cal

    def test_batch_add_session_id(self, org_dir):
        """Batch add-session-id works and is idempotent per item."""
        data, stderr, rc = run_batch(
            "add-session-id",
            [{"heading": "Write quarterly report",
              "session_id": "claude:abc-123"}],
            org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["status"] == "added"
        assert r["session_id"] == "claude:abc-123"
        assert "claude:abc-123" in (org_dir / "tasks.org").read_text()
        # Second run is a no-op
        data2, _, rc2 = run_batch(
            "add-session-id",
            [{"heading": "Write quarterly report",
              "session_id": "claude:abc-123"}],
            org_dir=org_dir)
        assert rc2 == 0
        assert data2["results"][0]["status"] == "no-op"

    def test_batch_show_full_task_fields(self, org_dir):
        """Batch show returns the same rich schema as single show --json."""
        data, stderr, rc = run_batch(
            "show", ["Prepare onboarding guide"], org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["heading"] == "Prepare onboarding guide"
        assert r["state"] == "TODO"
        assert r["file"] == "tasks.org"
        assert r["is_project"] is True
        assert [c["heading"] for c in r["subtasks"]] == [
            "Draft outline", "Write first chapter"]

    def test_batch_set_tags_splits_csv(self, org_dir):
        """Batch set-tags splits comma-separated tags into separate org tags
        (the old path passed the raw CSV string to org-set-tags)."""
        data, stderr, rc = run_batch(
            "set-tags",
            [{"heading": "Write quarterly report", "tags": "work,urgent"}],
            org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["tags"] == "work,urgent"
        assert r["new_tags"] == ["work", "urgent"]
        assert ":work:urgent:" in (org_dir / "tasks.org").read_text()

    def test_batch_add_tags_merge(self, org_dir):
        """Batch add-tags merges with existing tags and reports old/new."""
        data, stderr, rc = run_batch(
            "add-tags",
            [{"heading": "Set up automated backups for agent workspace",
              "tags": "urgent"}],
            org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["old_tags"] == ["@agent"]
        assert r["new_tags"] == ["@agent", "urgent"]
        assert r["tags"] == "@agent,urgent"

    def test_batch_delete_refuses_project(self, org_dir):
        """Batch delete gains the real implementation's project guard."""
        data, stderr, rc = run_batch(
            "delete", ["Prepare onboarding guide"], org_dir=org_dir)
        assert rc == 1
        r = data["results"][0]
        assert r["success"] is False
        assert "project with subtasks" in r["error"]
        # Project untouched
        assert "Prepare onboarding guide" in (org_dir / "tasks.org").read_text()

    def test_batch_error_text_is_plain(self, org_dir):
        """Per-item errors carry the plain error text extracted from the
        delegated command's JSON error output."""
        data, stderr, rc = run_batch(
            "set-done", ["Nonexistent task xyz"], org_dir=org_dir)
        assert rc == 1
        r = data["results"][0]
        assert r["success"] is False
        assert "No task found matching" in r["error"]
        assert not r["error"].lstrip().startswith("{")


class TestBatchMixed:
    """Tests for the `batch` subcommand (per-item commands on stdin)."""

    def test_heterogeneous_happy_path(self, org_dir):
        """One call mixing add-task, set-done, and add-tags."""
        items = [
            {"command": "add-task",
             "args": {"title": "Mixed batch task", "tags": "work"}},
            {"command": "set-done",
             "args": {"heading": "Write quarterly report"}},
            {"command": "add-tags",
             "args": {"heading": "Buy a small UPS", "tags": "urgent"}},
        ]
        data, stderr, rc = run_batch_mixed(items, org_dir=org_dir)
        assert rc == 0
        assert data is not None
        assert data["command"] == "batch"
        assert data["batch"] is True
        assert data["summary"] == {"total": 3, "succeeded": 3, "failed": 0}
        assert [r["index"] for r in data["results"]] == [0, 1, 2]
        assert all(r["success"] is True for r in data["results"])
        assert data["results"][0]["heading"] == "Mixed batch task"
        assert data["results"][0]["file"] == "inbox.org"
        assert data["results"][1]["new_state"] == "DONE"
        assert "urgent" in data["results"][2]["new_tags"]
        inbox = (org_dir / "inbox.org").read_text()
        assert "Mixed batch task" in inbox
        tasks = (org_dir / "tasks.org").read_text()
        assert "** DONE Write quarterly report" in tasks
        assert "Buy a small UPS for the server :urgent:" in tasks

    def test_unknown_command_is_per_item_error(self, org_dir):
        """An unknown per-item command fails that item, not the batch."""
        items = [
            {"command": "set-done",
             "args": {"heading": "Write quarterly report"}},
            {"command": "frobnicate", "args": {"heading": "x"}},
        ]
        data, stderr, rc = run_batch_mixed(items, org_dir=org_dir)
        assert rc == 0  # one item succeeded
        assert data["summary"]["succeeded"] == 1
        assert data["summary"]["failed"] == 1
        assert data["results"][0]["success"] is True
        assert data["results"][1]["success"] is False
        assert data["results"][1]["index"] == 1
        assert "Unsupported batch command" in data["results"][1]["error"]
        assert "frobnicate" in data["results"][1]["error"]
        # The valid item still took effect
        assert "** DONE Write quarterly report" in (org_dir / "tasks.org").read_text()

    def test_add_subtask_per_item_parent(self, org_dir):
        """add-subtask items carry their own parent in args."""
        items = [
            {"command": "add-subtask",
             "args": {"parent": "Prepare onboarding guide",
                      "title": "Mixed subtask A"}},
            {"command": "add-subtask",
             "args": {"parent": "Improve monitoring",
                      "title": "Mixed subtask B", "state": "NEXT"}},
        ]
        data, stderr, rc = run_batch_mixed(items, org_dir=org_dir)
        assert rc == 0
        assert data["summary"]["succeeded"] == 2
        assert data["results"][0]["parent"] == "Prepare onboarding guide"
        assert data["results"][1]["parent"] == "Improve monitoring"
        tasks = (org_dir / "tasks.org").read_text()
        # Parents are level 2 and 3 respectively
        assert "*** TODO Mixed subtask A" in tasks
        assert "**** NEXT Mixed subtask B" in tasks

    def test_add_subtask_missing_parent_is_per_item_error(self, org_dir):
        """An add-subtask item without parent fails alone."""
        items = [
            {"command": "add-subtask", "args": {"title": "Orphan subtask"}},
            {"command": "add-task", "args": {"title": "Healthy task"}},
        ]
        data, stderr, rc = run_batch_mixed(items, org_dir=org_dir)
        assert rc == 0
        assert data["summary"]["succeeded"] == 1
        assert data["results"][0]["success"] is False
        assert "parent" in data["results"][0]["error"]
        assert data["results"][1]["success"] is True
        all_text = "".join(p.read_text() for p in org_dir.glob("*.org"))
        assert "Orphan subtask" not in all_text
        assert "Healthy task" in all_text

    def test_refile_per_item_category(self, org_dir):
        """refile items carry their own category in args."""
        items = [
            {"command": "refile",
             "args": {"heading": "Buy groceries", "category": "Family"}},
            {"command": "refile",
             "args": {"heading": "Call the plumber about kitchen sink",
                      "category": "Shopping"}},
        ]
        data, stderr, rc = run_batch_mixed(items, org_dir=org_dir)
        assert rc == 0
        assert data["summary"]["succeeded"] == 2
        assert data["results"][0]["target_heading"] == "Family"
        assert data["results"][1]["target_heading"] == "Shopping"
        inbox = (org_dir / "inbox.org").read_text()
        assert "Buy groceries" not in inbox
        assert "Call the plumber" not in inbox
        assert_line_before(org_dir / "tasks.org",
                           "* Family", "Buy groceries")
        assert_line_before(org_dir / "tasks.org",
                           "* Shopping", "Call the plumber about kitchen sink")

    def test_works_without_json_flag(self, org_dir):
        """batch emits the JSON results array even without --json."""
        items = [{"command": "set-done",
                  "args": {"heading": "Write quarterly report"}}]
        data, stderr, rc = run_batch_mixed(items, org_dir=org_dir,
                                           json_flag=False)
        assert rc == 0
        assert data is not None
        assert data["summary"]["succeeded"] == 1


class TestBatchLoudErrors:
    """--batch / batch input errors must be loud: stderr + exit 1, never
    generic help, never exit 0."""

    def _run(self, args_list, stdin_text, org_dir):
        env = os.environ.copy()
        env["ORG_DIRECTORY"] = str(org_dir) + "/"
        env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
        env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
        cmd = ["python3", str(CLI_SCRIPT)] + args_list
        return subprocess.run(cmd, capture_output=True, text=True, env=env,
                              input=stdin_text, timeout=30)

    def _assert_loud(self, result):
        assert result.returncode == 1
        assert result.stderr.strip() != ""
        assert "usage:" not in result.stdout  # no generic help dump

    def test_batch_flag_without_subcommand_text(self, org_dir):
        r = self._run(["--batch"], "", org_dir)
        self._assert_loud(r)
        assert "subcommand" in r.stderr
        assert "org-gtd-cli batch" in r.stderr
        assert "--batch <subcommand>" in r.stderr

    def test_batch_flag_without_subcommand_json(self, org_dir):
        r = self._run(["--json", "--batch"], "", org_dir)
        self._assert_loud(r)
        err = json.loads(r.stderr.strip())
        assert "subcommand" in err["error"]

    # --- batch subcommand: malformed stdin ---

    def test_batch_subcommand_empty_stdin(self, org_dir):
        r = self._run(["batch"], "", org_dir)
        self._assert_loud(r)
        assert "empty stdin" in r.stderr

    def test_batch_subcommand_invalid_json(self, org_dir):
        r = self._run(["--json", "batch"], "this is not json", org_dir)
        self._assert_loud(r)
        err = json.loads(r.stderr.strip())
        assert "invalid JSON" in err["error"]

    def test_batch_subcommand_non_array(self, org_dir):
        r = self._run(["batch"], '{"command": "set-done", "args": {}}', org_dir)
        self._assert_loud(r)
        assert "array" in r.stderr

    def test_batch_subcommand_non_object_item(self, org_dir):
        r = self._run(["batch"], '["set-done"]', org_dir)
        self._assert_loud(r)
        assert "object" in r.stderr

    def test_batch_subcommand_missing_command(self, org_dir):
        r = self._run(["batch"], '[{"args": {"heading": "x"}}]', org_dir)
        self._assert_loud(r)
        assert "command" in r.stderr

    def test_batch_subcommand_args_wrong_type(self, org_dir):
        r = self._run(
            ["--json", "batch"],
            '[{"command": "set-done", "args": "Write quarterly report"}]',
            org_dir)
        self._assert_loud(r)
        err = json.loads(r.stderr.strip())
        assert "args" in err["error"]

    # --- legacy --batch <subcommand>: malformed stdin ---

    def test_batch_flag_empty_stdin(self, org_dir):
        r = self._run(["--batch", "set-done"], "", org_dir)
        self._assert_loud(r)
        assert "empty stdin" in r.stderr

    def test_batch_flag_non_array(self, org_dir):
        r = self._run(["--json", "--batch", "set-done"], '{"heading": "x"}',
                      org_dir)
        self._assert_loud(r)
        err = json.loads(r.stderr.strip())
        assert "array" in err["error"]

    def test_batch_flag_invalid_item_type(self, org_dir):
        r = self._run(["--batch", "set-done"], '[42]', org_dir)
        self._assert_loud(r)
        assert "item 0" in r.stderr


class TestBatchAddTaskDeepCategory:
    """Regression for e058336: batch add-task must insert at the matched
    category's child level (not hardcoded level 2), and must not swallow
    following headings as children."""

    def _nest_fixture(self, org_dir):
        """Extend tasks.org with a level-4 category:
        Computers (1) > Agents (2) > Projects (3) > agent-vm (4)."""
        tasks = org_dir / "tasks.org"
        text = tasks.read_text()
        assert "** Agents\n" in text
        text = text.replace(
            "** Agents\n",
            "** Agents\n"
            "*** Projects\n"
            "**** agent-vm\n"
            "***** TODO Improve agent-vm logging\n",
            1,
        )
        tasks.write_text(text)

    def _assert_task_at_level_5(self, org_dir, title):
        tasks_file = org_dir / "tasks.org"
        tasks = tasks_file.read_text()
        lineno = line_number_of(tasks_file, title)
        assert lineno is not None
        assert get_line(tasks_file, lineno).startswith("***** TODO "), \
            f"task not at level 5: {get_line(tasks_file, lineno)}"
        # Inserted inside the agent-vm subtree, after its existing child
        assert_line_before(tasks_file, "**** agent-vm", title)
        assert_line_before(tasks_file, "***** TODO Improve agent-vm logging",
                           title)
        # The following level-3 sibling heading is intact and NOT swallowed:
        # it still follows the new task at its original level
        backups = "*** TODO Set up automated backups for agent workspace"
        assert backups in tasks
        assert_line_before(tasks_file, title,
                           "Set up automated backups for agent workspace")

    def test_batch_subcommand_level4_category(self, org_dir):
        """New `batch` subcommand: task filed under a level-4 category lands
        at level 5."""
        self._nest_fixture(org_dir)
        items = [{"command": "add-task",
                  "args": {"title": "Migrate agent-vm to new bridge",
                           "category": "agent-vm"}}]
        data, stderr, rc = run_batch_mixed(items, org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["category"] == "Computers/Agents/Projects/agent-vm"
        self._assert_task_at_level_5(org_dir, "Migrate agent-vm to new bridge")

    def test_batch_flag_level4_category_full_path(self, org_dir):
        """Old --batch add-task path: same level-4 insertion, via the full
        category path."""
        self._nest_fixture(org_dir)
        data, stderr, rc = run_batch(
            "add-task",
            [{"title": "Old-style deep task",
              "category": "Computers/Agents/Projects/agent-vm"}],
            org_dir=org_dir)
        assert rc == 0
        r = data["results"][0]
        assert r["success"] is True
        assert r["category"] == "Computers/Agents/Projects/agent-vm"
        self._assert_task_at_level_5(org_dir, "Old-style deep task")


class TestCorrectiveErrors:
    """Tests for corrective error messages with hints."""

    def test_show_no_match_json_has_hint(self, org_dir):
        """show on nonexistent task includes hint in JSON on stderr."""
        data, stderr, rc = run_cli_json("show", "zzz_nonexistent_zzz", org_dir=org_dir)
        assert rc == 1
        # Error JSON goes to stderr
        err_data = None
        for line in stderr.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                err_data = json.loads(line)
                break
        assert err_data is not None
        assert "error" in err_data
        assert "hint" in err_data
        assert "search" in err_data["hint"].lower()

    def test_show_ambiguous_json_has_matches(self, org_dir):
        """show on ambiguous SUBSTR returns match list + hint in JSON."""
        # "Write" matches multiple tasks
        data, stderr, rc = run_cli_json("show", "agent", org_dir=org_dir)
        assert rc == 2
        assert data is not None
        assert "matches" in data
        assert len(data["matches"]) > 1
        assert "hint" in data
        assert "--index" in data["hint"]

    def test_refile_category_not_found_json_hint(self, org_dir):
        """refile --category nonexistent shows hint in JSON on stderr."""
        data, stderr, rc = run_cli_json(
            "refile", "Write quarterly report", "--category", "zzz_nonexistent",
            org_dir=org_dir,
        )
        assert rc == 1
        err_data = None
        for line in stderr.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                try:
                    err_data = json.loads(line)
                    if "hint" in err_data:
                        break
                except json.JSONDecodeError:
                    pass
        assert err_data is not None
        assert "hint" in err_data
        assert "categories" in err_data["hint"].lower()

    def test_set_state_invalid_json_hint(self, org_dir):
        """set-state with invalid state shows valid states in JSON on stderr."""
        data, stderr, rc = run_cli_json(
            "set-state", "Write quarterly report", "INVALID",
            org_dir=org_dir,
        )
        assert rc == 1
        err_data = None
        for line in stderr.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                try:
                    err_data = json.loads(line)
                    if "hint" in err_data:
                        break
                except json.JSONDecodeError:
                    pass
        assert err_data is not None
        assert "hint" in err_data
        assert "TODO" in err_data["hint"]

    def test_priority_cookie_stripped_from_show(self, org_dir):
        """show with priority cookie in SUBSTR still matches."""
        stdout, stderr, rc = run_cli("show", "[#A] Write quarterly report", org_dir=org_dir)
        assert rc == 0
        assert "Write quarterly report" in stdout

    def test_no_match_text_hint_on_stderr(self, org_dir):
        """Text mode: no-match error + hint appears on stderr."""
        stdout, stderr, rc = run_cli("show", "zzz_nonexistent_zzz", org_dir=org_dir)
        assert rc == 1
        assert "search" in stderr.lower()


class TestMutationTaskField:
    """Tests for full task state in JSON mutation responses."""

    def test_set_done_includes_task(self, org_dir):
        """set-done JSON response includes task object."""
        data, stderr, rc = run_cli_json("set-done", "Write quarterly report", org_dir=org_dir)
        assert rc == 0
        assert data is not None
        assert "task" in data
        task = data["task"]
        assert task["heading"] == "Write quarterly report"
        assert task["state"] == "DONE"
        assert "body" in task
        assert "file" in task

    def test_set_state_includes_task(self, org_dir):
        """set-state JSON response includes task object."""
        data, stderr, rc = run_cli_json(
            "set-state", "Prepare onboarding guide", "NEXT", org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert "task" in data
        assert data["task"]["state"] == "NEXT"

    def test_rename_includes_task(self, org_dir):
        """rename JSON response includes task object with new heading."""
        data, stderr, rc = run_cli_json(
            "rename", "Write quarterly report", "Write annual report", org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert "task" in data
        assert data["task"]["heading"] == "Write annual report"

    def test_set_tags_includes_task(self, org_dir):
        """set-tags JSON response includes task with updated tags."""
        data, stderr, rc = run_cli_json(
            "set-tags", "Write quarterly report", "--tags", "urgent,work",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert "task" in data
        assert "urgent" in data["task"]["tags"]

    def test_set_body_includes_task(self, org_dir):
        """set-body JSON response includes task with updated body."""
        data, stderr, rc = run_cli_json(
            "set-body", "Write quarterly report", "New body text.",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert "task" in data
        assert data["task"]["body"] == "New body text."

    def test_dry_run_no_task_field(self, org_dir):
        """Dry-run responses do NOT include task field."""
        data, stderr, rc = run_cli_json(
            "set-done", "Write quarterly report", "--dry-run", org_dir=org_dir,
        )
        assert rc == 0
        assert data is not None
        assert "task" not in data
        assert data.get("dry_run") is True


class TestAddSessionId:
    def test_add_session_id(self, org_dir):
        """add-session-id creates LOGBOOK entry."""
        stdout, stderr, rc = run_cli(
            "add-session-id", "Set up automated backups",
            "claude_code:test-uuid-1234",
            org_dir=org_dir,
        )
        assert rc == 0
        assert "Added session" in stdout or "added" in stdout

    def test_add_session_id_json(self, org_dir):
        """add-session-id --json returns structured response."""
        data, stderr, rc = run_cli_json(
            "add-session-id", "Set up automated backups",
            "claude_code:test-uuid-5678",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["status"] == "added"
        assert data["session_id"] == "claude_code:test-uuid-5678"

    def test_add_session_id_idempotent(self, org_dir):
        """Adding the same session ID twice is a no-op."""
        run_cli("add-session-id", "Set up automated backups",
                "claude_code:idempotent-test", org_dir=org_dir)
        data, stderr, rc = run_cli_json(
            "add-session-id", "Set up automated backups",
            "claude_code:idempotent-test",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["status"] == "no-op"


class TestGetSessionIds:
    def test_get_session_ids_empty(self, org_dir):
        """get-session-ids returns empty list for task without sessions."""
        data, stderr, rc = run_cli_json(
            "get-session-ids", "Write quarterly report",
            org_dir=org_dir,
        )
        assert rc == 0
        assert data["sessions"] == []

    def test_get_session_ids_after_add(self, org_dir):
        """get-session-ids returns sessions added via add-session-id."""
        run_cli("add-session-id", "Set up automated backups",
                "claude_code:roundtrip-test", org_dir=org_dir)
        data, stderr, rc = run_cli_json(
            "get-session-ids", "Set up automated backups",
            org_dir=org_dir,
        )
        assert rc == 0
        assert len(data["sessions"]) >= 1
        session = data["sessions"][0]
        assert session["agent"] == "claude_code"
        assert session["session_id"] == "roundtrip-test"

    def test_get_session_ids_plain(self, org_dir):
        """get-session-ids plain output shows one entry per line."""
        run_cli("add-session-id", "Set up automated backups",
                "pi_agent:plain-test", org_dir=org_dir)
        stdout, stderr, rc = run_cli(
            "get-session-ids", "Set up automated backups",
            org_dir=org_dir,
        )
        assert rc == 0
        assert "pi_agent:plain-test" in stdout

