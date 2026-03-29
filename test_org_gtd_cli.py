"""
Pytest test suite for org-gtd-cli.

Black-box CLI testing: calls the org-gtd-cli Python wrapper via subprocess
and asserts on stdout, stderr, exit code, and file contents.

Port of the 3148-line bash test suite (46 sections, 744+ assertions).
"""

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CLI_SCRIPT = Path(__file__).parent / "org-gtd-cli.py"
CORE_FILE = Path(__file__).parent / "gtd-core.el"
ELISP_FILE = Path(__file__).parent / "org-gtd-cli.el"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org_dir(tmp_path):
    """Copy fixture org files to a temp directory."""
    for f in FIXTURES_DIR.glob("*.org"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "agent-notes").mkdir(exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(*args, org_dir):
    """Run org-gtd-cli with the given arguments.

    Returns (stdout, stderr, returncode).
    """
    env = os.environ.copy()
    env["ORG_DIRECTORY"] = str(org_dir) + "/"
    env["ORG_GTD_CORE_FILE"] = str(CORE_FILE)
    env["ORG_GTD_ELISP_FILE"] = str(ELISP_FILE)
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
        shutil.copy(f, org_dir / f.name)
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
        assert "Error" in stdout

    def test_category_path_match(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Path task", "--category", "Computers/Agents", org_dir=org_dir)
        assert rc == 0
        assert "TODO Path task" in (org_dir / "tasks.org").read_text()
        assert "Computers/Agents" in stdout

    def test_ambiguous_category(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Ambig task", "--category", "Tools", org_dir=org_dir)
        assert rc == 2
        assert "Multiple category matches" in stdout
        assert "Computers/Tools" in stdout
        assert "Research/Tools" in stdout

    def test_path_disambiguates(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Disambig task", "--category", "Research/Tools", org_dir=org_dir)
        assert rc == 0
        assert "TODO Disambig task" in (org_dir / "tasks.org").read_text()
        assert "Research/Tools" in stdout

    def test_wrong_path_not_found(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Wrong path", "--category", "Work/Agents", org_dir=org_dir)
        assert rc == 1
        assert "not found" in stdout

    def test_category_skips_todo_headings_holiday(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Holiday test task", "--category", "Holiday", org_dir=org_dir)
        assert rc == 0
        assert "TODO Holiday test task" in (org_dir / "tasks.org").read_text()
        assert "Travel/Holiday Trip" in stdout

    def test_category_only_todo_matches_returns_not_found(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Design test task", "--category", "Design", org_dir=org_dir)
        assert rc == 1
        assert "not found" in stdout

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
        assert "Multiple category matches" in stdout
        assert "Improve agent workflow/Resources" in stdout
        assert "Research/Resources" in stdout


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
        assert "No matches." in stdout

    def test_empty_substr_returns_exit_1(self, org_dir):
        stdout, stderr, rc = run_cli("search", "", org_dir=org_dir)
        assert rc == 1
        assert "Error" in stdout

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
        assert "file not found" in stdout

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
        assert "No matches." in stdout


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

    def test_shows_logbook_drawers(self, org_dir):
        stdout, stderr, rc = run_cli("show", "Fix org-capture workspace", org_dir=org_dir)
        assert rc == 0
        assert ":LOGBOOK:" in stdout

    def test_task_with_org_link(self, org_dir):
        stdout, stderr, rc = run_cli("show", "interesting article", org_dir=org_dir)
        assert rc == 0
        assert "[[https://example.com" in stdout

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
        assert "File not found" in stdout


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
    def test_finds_agent_tasks(self, org_dir):
        stdout, stderr, rc = run_cli("process-agent-tasks", org_dir=org_dir)
        assert rc == 0
        assert "Set up automated backups" in stdout
        assert "Improve agent workflow" in stdout
        assert "Buy a formicarium" in stdout
        assert "Found 3 agent tasks" in stdout
        assert "research backup strategies" in stdout
        assert "2/5 done" in stdout
        assert "Project: Agents" not in stdout
        assert "Project: Pet Ants" not in stdout


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
        assert "[1]" in stdout
        assert "[2]" in stdout

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

    def test_all_siblings_done_auto_completes_parent(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Test migration on staging", org_dir=org_dir)
        assert rc == 0
        assert "Auto-completed project" in stdout
        assert "Ship epiphyte updates" in stdout
        assert "DONE Ship epiphyte updates" in (org_dir / "tasks.org").read_text()

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

    def test_dry_run_auto_complete_preview(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Test migration on staging", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would auto-complete project" in stdout
        assert "Ship epiphyte updates" in stdout
        assert "NEXT Test migration on staging" in (org_dir / "tasks.org").read_text()

    def test_dry_run_subproject_drill_in_preview(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Set up alerting", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would auto-progress" in stdout
        assert "in subproject" in stdout
        assert "NEXT Set up alerting" in (org_dir / "tasks.org").read_text()

    def test_cascading_auto_complete(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Install certificates", org_dir=org_dir)
        assert rc == 0
        assert "Auto-completed project" in stdout
        assert "Set up TLS certs" in stdout
        assert "Auto-progressed" in stdout
        assert "Run smoke tests" in stdout
        assert "DONE Set up TLS certs" in (org_dir / "tasks.org").read_text()
        assert "NEXT Run smoke tests" in (org_dir / "tasks.org").read_text()

    def test_cascading_dry_run_preview(self, org_dir):
        stdout, stderr, rc = run_cli("set-done", "Install certificates", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would auto-complete project" in stdout
        assert "Set up TLS certs" in stdout
        assert "Would auto-progress" in stdout
        assert "Run smoke tests" in stdout
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
        assert "not a valid state" in stdout
        assert "TODO, NEXT, DONE, WAITING, DEFER, CANCELLED" in stdout

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
        assert "not a valid priority" in stdout
        assert "A, B, C" in stdout

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
        assert "self-match" in stdout
        assert "xyzzy" in (org_dir / "inbox.org").read_text()

    def test_subtree_child_also_counts_as_self_match(self, org_dir):
        run_cli("add-task", "zqxjk", "--file", "inbox.org", org_dir=org_dir)
        run_cli("add-subtask", "zqxjk", "zqxjk", org_dir=org_dir)
        stdout, stderr, rc = run_cli("refile", "zqxjk", "--to", "zqxjk", "--index", "1", org_dir=org_dir)
        assert rc == 1
        assert "skipped 2 self-match" in stdout
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
        assert "not found" in stdout

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
        assert "not found" in stdout


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
        assert "Multiple category matches" in stdout

    def test_path_disambiguates(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Research/Tools", org_dir=org_dir)
        assert rc == 0
        assert "Refiled" in stdout

    def test_only_todo_matches(self, org_dir):
        stdout, stderr, rc = run_cli("refile", "Buy groceries", "--category", "Design", org_dir=org_dir)
        assert rc == 1
        assert "not found" in stdout

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
        assert "Multiple category matches" in stdout
        assert "Improve agent workflow/Resources" in stdout
        assert "Research/Resources" in stdout


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
        # Create the non-default calendar file
        (org_dir / "family-calendar.org").write_text("#+title: Family Calendar\n")
        stdout, stderr, rc = run_cli("add-event", "Family dinner", "--date", "2026-03-23", "--file", "family-calendar.org", org_dir=org_dir)
        assert rc == 0
        cal = (org_dir / "family-calendar.org").read_text()
        assert "* Family dinner" in cal
        assert ":calpersonal:" not in cal
        assert ":org-gcal:" in cal
        assert ":END:" in cal
        assert ":calendar-id:" in cal

    def test_non_default_file_with_explicit_tag(self, org_dir):
        (org_dir / "family-calendar.org").write_text("#+title: Family Calendar\n")
        stdout, stderr, rc = run_cli("add-event", "School play", "--date", "2026-03-25", "--tag", "calfamily", "--file", "family-calendar.org", org_dir=org_dir)
        assert rc == 0
        cal = (org_dir / "family-calendar.org").read_text()
        assert ":calfamily:" in cal
        assert ":org-gcal:" in cal

    def test_date_range_in_gcal_drawer(self, org_dir):
        (org_dir / "family-calendar.org").write_text("#+title: Family Calendar\n")
        stdout, stderr, rc = run_cli("add-event", "Spring break", "--date", "2026-04-06", "--file", "family-calendar.org", "--end-date", "2026-04-17", org_dir=org_dir)
        assert rc == 0
        cal = (org_dir / "family-calendar.org").read_text()
        assert ":org-gcal:" in cal
        assert "<2026-04-06 Mon>--<2026-04-17 Fri>" in cal


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
        assert "Error" in stdout

    def test_rejects_body_with_org_heading_on_subsequent_line(self, org_dir):
        stdout, stderr, rc = run_cli("append-body", "Buy a small UPS", "Some text\n* Mid-text heading", org_dir=org_dir)
        assert rc == 1
        assert "Error" in stdout


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
        assert "Error" in stdout

    def test_rejects_body_with_org_heading_on_subsequent_line(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Buy a small UPS", "Some text\n* Mid-text heading", org_dir=org_dir)
        assert rc == 1
        assert "Error" in stdout

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
        assert "Already has NEXT" in stdout

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
        stdout, stderr, rc = run_cli("set-next", "Pay quarterly taxes", org_dir=org_dir)
        assert rc == 0
        assert "Set NEXT" in stdout
        assert "NEXT [#A] Pay quarterly taxes" in (org_dir / "tasks.org").read_text()

    def test_leaf_task_already_next_noop(self, org_dir):
        run_cli("set-next", "Pay quarterly taxes", org_dir=org_dir)
        stdout, stderr, rc = run_cli("set-next", "Pay quarterly taxes", org_dir=org_dir)
        assert rc == 0
        assert "Already NEXT" in stdout

    def test_subproject_set_next(self, org_dir):
        stdout, stderr, rc = run_cli("set-next", "Design CLI tool", org_dir=org_dir)
        assert rc == 0
        assert "Already has NEXT" in stdout


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

    def test_process_agent_tasks_subtask_count_and_file_ref(self, org_dir):
        stdout, stderr, rc = run_cli("process-agent-tasks", org_dir=org_dir)
        assert "2/5 done" in stdout
        assert "DONE What are the current pain points? (tasks.org)" in stdout


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
        assert "Already has NEXT" in stdout

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
    def test_add_tag(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--add", "urgent", org_dir=org_dir)
        assert rc == 0
        assert "Tags:" in stdout
        text = (org_dir / "inbox.org").read_text()
        assert ":urgent:" in text
        assert ":buy:" in text
        assert ":@errand:" in text

    def test_remove_tag(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--remove", "@errand", org_dir=org_dir)
        assert rc == 0
        assert ":buy:" in (org_dir / "inbox.org").read_text()

    def test_add_and_remove_in_one_call(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--add", "urgent,@home", "--remove", "@errand", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "inbox.org").read_text()
        assert ":urgent:" in text
        assert ":@home:" in text

    def test_remove_nonexistent(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--remove", "nonexistent", org_dir=org_dir)
        assert rc == 0
        text = (org_dir / "inbox.org").read_text()
        assert ":buy:" in text
        assert ":@errand:" in text

    def test_add_existing(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--add", "buy", org_dir=org_dir)
        assert rc == 0
        assert ":buy:" in (org_dir / "inbox.org").read_text()

    def test_dry_run(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "Buy groceries", "--add", "urgent", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would set tags" in stdout
        assert ":urgent:" not in (org_dir / "inbox.org").read_text()

    def test_ambiguous(self, org_dir):
        stdout, stderr, rc = run_cli("set-tags", "Buy", "--add", "urgent", org_dir=org_dir)
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
        stdout, stderr, rc = run_cli("set-tags", "Buy", "--add", "urgent", "--index", "999", org_dir=org_dir)
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
        stdout, stderr, rc = run_cli("set-tags", "xyznonexistent", "--add", "urgent", org_dir=org_dir)
        assert rc == 1


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
        assert "still active" in stdout

    def test_rejects_recent_dates(self, org_dir):
        stdout, stderr, rc = run_cli("archive", "submit expense claims", org_dir=org_dir)
        assert rc == 1
        assert "recent dates" in stdout

    def test_rejects_inside_active_project(self, org_dir):
        stdout, stderr, rc = run_cli("archive", "pack suitcases", org_dir=org_dir)
        assert rc == 1
        assert "inside active project" in stdout

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
        stdout, stderr, rc = run_cli("archive", "--all", org_dir=org_dir)
        assert rc == 0
        assert "Archived" in stdout
        text = (org_dir / "tasks.org").read_text()
        assert "Buy new router" not in text
        assert "Research dentists" not in text
        assert 'Skipped (no dates): "Mystery task"' in stdout
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
        assert "No archivable tasks found" in stdout


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
        assert "Unknown agenda view key" in stdout


# ===========================================================================
# 41. fix-timestamps
# ===========================================================================

class TestFixTimestamps:
    def test_adds_missing_timestamps(self, org_dir):
        stdout, stderr, rc = run_cli("fix-timestamps", org_dir=org_dir)
        assert rc == 0
        assert "Fixed" in stdout
        assert "Bare heading no body" in stdout
        assert "Mystery task" in stdout
        tasks = org_dir / "tasks.org"
        assert "Bare heading no body" in tasks.read_text()
        # Timestamp should be right after the bare heading
        bare_line = line_number_of(tasks, "Bare heading no body")
        assert bare_line is not None
        next_content = get_line(tasks, bare_line + 1)
        assert next_content is not None
        assert re.match(r'^\[[-0-9]+ [A-Z][a-z]+( [0-9:]+)?\]$', next_content)

    def test_dry_run(self, org_dir):
        # Take snapshot before
        before_text = (org_dir / "tasks.org").read_text()
        stdout, stderr, rc = run_cli("fix-timestamps", "--dry-run", org_dir=org_dir)
        assert rc == 0
        assert "Would fix" in stdout
        assert (org_dir / "tasks.org").read_text() == before_text

    def test_idempotent(self, org_dir):
        run_cli("fix-timestamps", org_dir=org_dir)
        stdout, stderr, rc = run_cli("fix-timestamps", org_dir=org_dir)
        assert rc == 0
        assert "nothing to fix" in stdout

    def test_preserves_body_text(self, org_dir):
        stdout, stderr, rc = run_cli("fix-timestamps", org_dir=org_dir)
        assert rc == 0
        tasks = org_dir / "tasks.org"
        assert "Some old task with no dates at all" in tasks.read_text()
        mystery_line = line_number_of(tasks, "Mystery task")
        assert mystery_line is not None
        ts_line_no = mystery_line + 2  # body is at +1, timestamp at +2
        ts_content = get_line(tasks, ts_line_no)
        assert ts_content is not None
        assert re.match(r'^\[[-0-9]+ [A-Z][a-z]+( [0-9:]+)?\]$', ts_content)

    def test_skips_non_todo_headings(self, org_dir):
        stdout, stderr, rc = run_cli("fix-timestamps", org_dir=org_dir)
        assert "Agents" not in stdout
        assert "Pet Ants" not in stdout


# ===========================================================================
# 42. fill-text (line wrapping)
# ===========================================================================

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
        assert_no_long_lines(org_dir / "tasks.org", "Bare heading", "Research", 80)

    def test_set_body_wraps_long_text(self, org_dir):
        stdout, stderr, rc = run_cli("set-body", "Bare heading", LONG_TEXT, org_dir=org_dir)
        assert rc == 0
        assert_no_long_lines(org_dir / "tasks.org", "Bare heading", "Research", 80)

    def test_add_task_wraps_long_body(self, org_dir):
        stdout, stderr, rc = run_cli("add-task", "Wrap test task", "--body", LONG_TEXT, org_dir=org_dir)
        assert rc == 0
        assert "Wrap test task" in (org_dir / "inbox.org").read_text()
        assert_no_long_lines(org_dir / "inbox.org", "Wrap test task", "", 80)

    def test_add_subtask_wraps_long_body(self, org_dir):
        stdout, stderr, rc = run_cli("add-subtask", "Holiday pre-trip", "Wrap sub", "--body", LONG_TEXT, org_dir=org_dir)
        assert rc == 0
        assert "Wrap sub" in (org_dir / "tasks.org").read_text()
        assert_no_long_lines(org_dir / "tasks.org", "Wrap sub", "Finance", 80)

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
        assert_no_long_lines(org_dir / "tasks.org", "Bare heading", "Research", 80)
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
        assert_no_long_lines(org_dir / "tasks.org", "Bare heading", "[[file:", 80)
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
        assert "is a project with subtasks" in stdout
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
