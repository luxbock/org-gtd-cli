#!/usr/bin/env python3
"""org-gtd-cli: CLI for org-mode GTD system management.

Thin dispatch layer — all org logic lives in org-gtd-cli.el.
This script parses arguments and calls Emacs in batch mode.
"""

import argparse
import os
import subprocess
import sys
import tempfile

# --- Paths (set by Nix wrapper or environment) ---
CORE_FILE = os.environ.get("ORG_GTD_CORE_FILE", "")
ELISP_FILE = os.environ.get("ORG_GTD_ELISP_FILE", "")
ORG_DIR = os.environ.get("ORG_DIRECTORY", os.path.expanduser("~/Nextcloud/org/"))
EMACS_BIN = "emacs"


# --- Helpers ---

def escape_elisp(s: str) -> str:
    """Escape a string for use as an elisp string literal."""
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


def to_elisp(value: str | None) -> str:
    """Convert a Python value to elisp: None/empty -> nil, otherwise quoted string."""
    if value is None or value == "":
        return "nil"
    return f'"{escape_elisp(value)}"'


def unescape_body_newlines(text: str) -> str:
    """Convert literal \\n sequences to actual newlines in body text.

    Agents behind skill constraints write \\\\n in JSON which arrives as
    two literal characters (\\, n).  Preserves intended literal \\\\n
    (double-backslash n) via placeholder.
    """
    # Protect intentional \\n
    text = text.replace("\\\\n", "\x00")
    # Convert \n to real newlines
    text = text.replace("\\n", "\n")
    # Restore \\n
    text = text.replace("\x00", "\\n")
    return text


def normalize_tags(tag_list: list[str] | None) -> str | None:
    """Normalize repeated --tag flags into wire format for elisp.

    Each --tag flag is an AND constraint. Commas within a --tag are OR.
    + within a --tag is equivalent to separate --tag flags.

    Wire format: AND groups joined by |, OR alternatives within groups
    joined by ,. Example: --tag @agent --tag @errand,@phone
    -> "@agent|@errand,@phone"
    """
    if not tag_list:
        return None

    and_groups = []
    for tag_value in tag_list:
        # Split + into separate AND groups (backwards compatible)
        parts = tag_value.split("+")
        and_groups.extend(parts)

    if not and_groups:
        return None

    return "|".join(and_groups)


def run_elisp(expr: str) -> int:
    """Run an elisp expression in batch Emacs. Returns exit code."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            EMACS_BIN, "--batch", "-q",
            "--eval", f'(setq user-emacs-directory "{tmpdir}/")',
            "--eval", f'(setenv "ORG_DIRECTORY" "{ORG_DIR}")',
            "-l", CORE_FILE,
            "-l", ELISP_FILE,
            "--eval", expr,
        ]
        # Emacs --batch sends its own diagnostics to stderr; let them through
        result = subprocess.run(cmd, capture_output=False)
        return result.returncode


# --- Grouped help formatter ---

class CompactHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Suppress the auto-generated subcommand list — we use the epilog instead."""

    def _format_action(self, action):
        # Skip the subparsers action entirely
        if isinstance(action, argparse._SubParsersAction):
            return ""
        return super()._format_action(action)


# --- Command handlers ---
# Each handler extracts args from the namespace and calls run_elisp.

def cmd_org_timestamp(args):
    expr = (f'(org-gtd-cli/org-timestamp {to_elisp(args.date)} '
            f'{to_elisp(args.time)} {to_elisp("t" if args.inactive else None)})')
    return run_elisp(expr)


def cmd_agenda(args):
    tag = normalize_tags(args.tag)
    expr = (f'(org-gtd-cli/agenda {to_elisp(args.state)} '
            f'{to_elisp(tag)} {to_elisp(getattr(args, "from"))} {to_elisp(args.to)})')
    return run_elisp(expr)


def cmd_search(args):
    tag = normalize_tags(args.tag)
    expr = (f'(org-gtd-cli/search {to_elisp(args.substr)} '
            f'{to_elisp(args.state)} {to_elisp(tag)} {to_elisp(args.file)})')
    return run_elisp(expr)


def cmd_show(args):
    expr = (f'(org-gtd-cli/show {to_elisp(args.substr)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.plain else None)})')
    return run_elisp(expr)


def cmd_subtasks(args):
    expr = f'(org-gtd-cli/subtasks {to_elisp(args.substr)} {to_elisp(args.index)})'
    return run_elisp(expr)


def cmd_categories(args):
    expr = f'(org-gtd-cli/categories {to_elisp(args.file)})'
    return run_elisp(expr)


def cmd_projects(_args):
    return run_elisp("(org-gtd-cli/projects)")


def cmd_process_agent_tasks(_args):
    return run_elisp("(org-gtd-cli/process-agent-tasks)")


def cmd_add_task(args):
    title = args.title or args.title_flag
    if not title:
        print("Error: TITLE is required", file=sys.stderr)
        return 1
    body = unescape_body_newlines(args.body) if args.body else args.body
    expr = (f'(org-gtd-cli/add-task {to_elisp(title)} {to_elisp(body)} '
            f'{to_elisp(args.tags)} {to_elisp(args.schedule)} '
            f'{to_elisp(args.deadline)} {to_elisp(args.priority)} '
            f'{to_elisp(args.file)} {to_elisp(args.category)} '
            f'{to_elisp(args.state)})')
    return run_elisp(expr)


def cmd_add_subtask(args):
    body = unescape_body_newlines(args.body) if args.body else args.body
    expr = (f'(org-gtd-cli/add-subtask {to_elisp(args.parent)} '
            f'{to_elisp(args.title)} {to_elisp(body)} '
            f'{to_elisp(args.tags)} {to_elisp(args.schedule)} '
            f'{to_elisp(args.deadline)} {to_elisp(args.priority)} '
            f'{to_elisp(args.state)} {to_elisp(args.index)})')
    return run_elisp(expr)


def cmd_add_event(args):
    expr = (f'(org-gtd-cli/add-event {to_elisp(args.title)} '
            f'{to_elisp(args.date)} {to_elisp(args.time)} '
            f'{to_elisp(args.tag)} {to_elisp(args.file)} '
            f'{to_elisp(args.end_date)})')
    return run_elisp(expr)


def cmd_add_note(args):
    title = args.title_pos or args.title
    if not title:
        print("Error: TITLE is required (positional or --title)", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/add-note {to_elisp(title)} '
            f'{to_elisp(args.link_task)} {to_elisp(args.tags)} '
            f'{to_elisp(args.sections)})')
    return run_elisp(expr)


def cmd_append_body(args):
    expr = (f'(org-gtd-cli/append-body {to_elisp(args.substr)} '
            f'{to_elisp(args.text)} {to_elisp(args.index)})')
    return run_elisp(expr)


def cmd_set_body(args):
    expr = (f'(org-gtd-cli/set-body {to_elisp(args.substr)} '
            f'{to_elisp(args.text)} {to_elisp(args.index)})')
    return run_elisp(expr)


def cmd_set_done(args):
    expr = (f'(org-gtd-cli/set-done {to_elisp(args.substr)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_set_state(args):
    expr = (f'(org-gtd-cli/set-state {to_elisp(args.substr)} '
            f'{to_elisp(args.state)} {to_elisp(args.index)} '
            f'{to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_set_priority(args):
    if not args.priority and not args.clear:
        print("Error: provide a PRIORITY (A, B, or C) or --clear", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/set-priority {to_elisp(args.substr)} '
            f'{to_elisp(args.priority)} {to_elisp("t" if args.clear else None)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_set_cancelled(args):
    expr = (f'(org-gtd-cli/set-state {to_elisp(args.substr)} '
            f'"CANCELLED" {to_elisp(args.index)} '
            f'{to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_set_next(args):
    expr = f'(org-gtd-cli/set-next {to_elisp(args.substr)} {to_elisp(args.index)})'
    return run_elisp(expr)


def cmd_refile(args):
    if args.to and args.category:
        print("Error: --to and --category are mutually exclusive", file=sys.stderr)
        return 1
    if not args.to and not args.category:
        print("Error: one of --to or --category is required", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/refile {to_elisp(args.substr)} '
            f'{to_elisp(args.to)} {to_elisp(args.category)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_move(args):
    direction = None
    sibling = None
    if args.up:
        direction = "up"
    elif args.down:
        direction = "down"
    elif args.before:
        direction = "before"
        sibling = args.before
    elif args.after:
        direction = "after"
        sibling = args.after
    if not direction:
        print("Error: one of --up, --down, --before, --after is required",
              file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/move {to_elisp(args.substr)} '
            f'{to_elisp(direction)} {to_elisp(sibling)} {to_elisp(args.index)})')
    return run_elisp(expr)


def cmd_rename(args):
    expr = (f'(org-gtd-cli/rename {to_elisp(args.substr)} '
            f'{to_elisp(args.newtitle)} {to_elisp(args.index)} '
            f'{to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_set_schedule(args):
    if not args.date and not args.clear:
        print("Error: provide a DATE or --clear", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/set-schedule {to_elisp(args.substr)} '
            f'{to_elisp(args.date)} {to_elisp(args.time)} '
            f'{to_elisp("t" if args.clear else None)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_set_deadline(args):
    if not args.date and not args.clear:
        print("Error: provide a DATE or --clear", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/set-deadline {to_elisp(args.substr)} '
            f'{to_elisp(args.date)} {to_elisp(args.time)} '
            f'{to_elisp("t" if args.clear else None)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_set_tags(args):
    if not args.add and not args.remove:
        print("Error: at least one of --add or --remove is required",
              file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/set-tags {to_elisp(args.substr)} '
            f'{to_elisp(args.add)} {to_elisp(args.remove)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_agenda_view(args):
    key = args.key if args.key else " "
    expr = f'(org-gtd-cli/agenda-view {to_elisp(key)})'
    return run_elisp(expr)


def cmd_archive(args):
    if args.all and args.substr:
        print("Error: --all and SUBSTR are mutually exclusive", file=sys.stderr)
        return 1
    if not args.all and not args.substr:
        print("Error: provide SUBSTR or --all", file=sys.stderr)
        return 1
    if args.all:
        expr = f'(org-gtd-cli/archive-all {to_elisp("t" if args.dry_run else None)})'
    else:
        expr = (f'(org-gtd-cli/archive {to_elisp(args.substr)} '
                f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_delete(args):
    expr = (f'(org-gtd-cli/delete {to_elisp(args.heading)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr)


def cmd_fix_timestamps(args):  # noqa: unused 'args' used for dry_run
    expr = f'(org-gtd-cli/fix-timestamps {to_elisp("t" if args.dry_run else None)})'
    return run_elisp(expr)


# --- Parser construction ---

def build_parser() -> argparse.ArgumentParser:
    epilog = """\
Querying:
  show              Show full task details
  search            Find tasks by heading substring
  agenda            List tasks with state/tag/date filters
  agenda-view       Run a pre-built agenda view
  subtasks          List children of a project
  categories        Show category tree for refile targets
  projects          List active projects with progress
  process-agent-tasks  Structured work queue for @agent tasks

Creating:
  add-task          Add a task (default: inbox)
  add-subtask       Add a child task under a parent
  add-event         Add a calendar event
  add-note          Create a note file in agent-notes/

Modifying:
  set-done          Mark task DONE (with auto-progress)
  set-state         Change TODO state
  set-next          Promote task/child to NEXT
  set-priority      Set priority A/B/C
  set-cancelled     Mark task CANCELLED
  refile            Move task to a different heading
  move              Reorder a task among siblings
  rename            Change task heading text
  set-schedule      Set/clear SCHEDULED timestamp
  set-deadline      Set/clear DEADLINE timestamp
  set-tags          Add/remove tags
  append-body       Append text to task body
  set-body          Replace task body

Maintenance:
  archive           Archive completed tasks
  delete            Delete a task (exact match, no projects)
  fix-timestamps    Add missing creation timestamps
  org-timestamp     Generate formatted org timestamp

Environment:
  ORG_DIRECTORY     Path to org files (default: ~/Nextcloud/org/)

Exit codes: 0 success, 1 error, 2 ambiguous match

Run 'org-gtd-cli <command> -h' for command details."""

    parser = argparse.ArgumentParser(
        prog="org-gtd-cli",
        description="CLI for org-mode GTD system management",
        epilog=epilog,
        formatter_class=CompactHelpFormatter,
        usage="org-gtd-cli <command> [options]",
    )
    sub = parser.add_subparsers(dest="command")

    # --- Querying ---

    p = sub.add_parser("show", help="Show full task details")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring to match")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--plain", action="store_true", help="Minimal output")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("search", help="Find tasks by heading substring")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring to match")
    p.add_argument("--state", help="Filter by state (comma-separated, or 'all')")
    p.add_argument("--tag", "--tags", action="append", dest="tag",
                   help="Filter by tag (repeat for AND, comma within for OR)")
    p.add_argument("--file", help="Restrict to a single file")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("agenda", help="List tasks with state/tag/date filters")
    p.add_argument("--state", help="Filter by state (comma-separated)")
    p.add_argument("--tag", "--tags", action="append", dest="tag",
                   help="Filter by tag (repeat for AND, comma within for OR)")
    p.add_argument("--from", dest="from", help="Start date (YYYY-MM-DD)")
    p.add_argument("--to", help="End date (YYYY-MM-DD)")
    p.set_defaults(func=cmd_agenda)

    p = sub.add_parser("agenda-view", help="Run a pre-built agenda view")
    p.add_argument("key", nargs="?", default=" ",
                   help="Agenda view key (default: ' ' for full dashboard)")
    p.set_defaults(func=cmd_agenda_view)

    p = sub.add_parser("subtasks", help="List children of a project")
    p.add_argument("substr", metavar="SUBSTR", help="Parent heading substring")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_subtasks)

    p = sub.add_parser("categories", help="Show category tree for refile targets")
    p.add_argument("--file", help="Target file (default: tasks.org)")
    p.set_defaults(func=cmd_categories)

    p = sub.add_parser("projects", help="List active projects with progress")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("process-agent-tasks",
                       help="Structured work queue for @agent tasks")
    p.set_defaults(func=cmd_process_agent_tasks)

    # --- Creating ---

    p = sub.add_parser("add-task", help="Add a task (default: inbox)")
    p.add_argument("title", nargs="?", default=None, metavar="TITLE",
                   help="Task title")
    p.add_argument("--title", dest="title_flag", default=None,
                   help="Task title (alternative to positional)")
    p.add_argument("--body", help="Body text below the heading")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--schedule", help="SCHEDULED date")
    p.add_argument("--deadline", help="DEADLINE date")
    p.add_argument("--priority", help="Priority: A, B, or C")
    p.add_argument("--file", help="Target file (relative to ORG_DIRECTORY)")
    p.add_argument("--category", help="Insert under this heading in tasks.org")
    p.add_argument("--state", help="Initial state (default: TODO)")
    p.set_defaults(func=cmd_add_task)

    p = sub.add_parser("add-subtask", help="Add a child task under a parent")
    p.add_argument("parent", metavar="SUBSTR", help="Parent heading substring")
    p.add_argument("title", metavar="TITLE", help="Subtask title")
    p.add_argument("--body", help="Body text")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--schedule", help="SCHEDULED date")
    p.add_argument("--deadline", help="DEADLINE date")
    p.add_argument("--priority", help="Priority: A, B, or C")
    p.add_argument("--state", help="Initial state (default: TODO)")
    p.add_argument("--index", help="Disambiguate parent with 1-based index")
    p.set_defaults(func=cmd_add_subtask)

    p = sub.add_parser("add-event", help="Add a calendar event")
    p.add_argument("title", metavar="TITLE", help="Event title")
    p.add_argument("--date", required=True, help="Event date (YYYY-MM-DD)")
    p.add_argument("--end-date", dest="end_date", help="End date for multi-day events")
    p.add_argument("--time", help="Event time (HH:MM or HH:MM-HH:MM)")
    p.add_argument("--tag", help="Tag (default: calpersonal)")
    p.add_argument("--file", help="Target file (default: calendar.org)")
    p.set_defaults(func=cmd_add_event)

    p = sub.add_parser("add-note", help="Create a note file in agent-notes/")
    p.add_argument("title_pos", nargs="?", default=None, metavar="TITLE",
                   help="Note title")
    p.add_argument("--title", help="Note title (alternative to positional)")
    p.add_argument("--link-task", dest="link_task", help="Link to a task by SUBSTR")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--sections", help="Comma-separated section names")
    p.set_defaults(func=cmd_add_note)

    # --- Modifying ---

    p = sub.add_parser("set-done", help="Mark task DONE (with auto-progress)")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_done)

    p = sub.add_parser("set-state", help="Change TODO state")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("state", metavar="STATE",
                   help="Target state: TODO, NEXT, DONE, WAITING, DEFER, CANCELLED")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_state)

    p = sub.add_parser("set-next", help="Promote task/child to NEXT")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_set_next)

    p = sub.add_parser("set-priority", help="Set priority A/B/C")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("priority", nargs="?", default=None, metavar="PRIORITY",
                   help="Priority: A, B, or C")
    p.add_argument("--clear", action="store_true", help="Remove priority")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_priority)

    p = sub.add_parser("set-cancelled", help="Mark task CANCELLED")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_cancelled)

    p = sub.add_parser("refile", help="Move task to a different heading")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("--to", help="Exact match on heading text")
    p.add_argument("--category", help="Substring match on category headings")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_refile)

    p = sub.add_parser("move", help="Reorder a task among siblings")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    direction = p.add_mutually_exclusive_group()
    direction.add_argument("--up", action="store_true", help="Move up")
    direction.add_argument("--down", action="store_true", help="Move down")
    direction.add_argument("--before", metavar="SIBLING", help="Move before sibling")
    direction.add_argument("--after", metavar="SIBLING", help="Move after sibling")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("rename", help="Change task heading text")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("newtitle", metavar="NEWTITLE", help="New heading text")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("set-schedule", help="Set/clear SCHEDULED timestamp")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("date", nargs="?", default=None, metavar="DATE",
                   help="Date (YYYY-MM-DD)")
    p.add_argument("--time", help="Time (HH:MM)")
    p.add_argument("--clear", action="store_true", help="Remove schedule")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_schedule)

    p = sub.add_parser("set-deadline", help="Set/clear DEADLINE timestamp")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("date", nargs="?", default=None, metavar="DATE",
                   help="Date (YYYY-MM-DD)")
    p.add_argument("--time", help="Time (HH:MM)")
    p.add_argument("--clear", action="store_true", help="Remove deadline")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_deadline)

    p = sub.add_parser("set-tags", help="Add/remove tags")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("--add", help="Tags to add (comma-separated)")
    p.add_argument("--remove", help="Tags to remove (comma-separated)")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_tags)

    p = sub.add_parser("append-body", help="Append text to task body")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("text", metavar="TEXT", help="Text to append")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_append_body)

    p = sub.add_parser("set-body", help="Replace task body")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("text", metavar="TEXT", help="New body text")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_set_body)

    # --- Maintenance ---

    p = sub.add_parser("archive", help="Archive completed tasks")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("--all", action="store_true", help="Archive all eligible tasks")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("delete", help="Delete a task (exact match, no projects)")
    p.add_argument("heading", metavar="HEADING",
                   help="Exact heading text (case-insensitive)")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("fix-timestamps", help="Add missing creation timestamps")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_fix_timestamps)

    p = sub.add_parser("org-timestamp",
                       help="Generate formatted org timestamp")
    p.add_argument("date", metavar="DATE", help="Date (YYYY-MM-DD)")
    p.add_argument("time", nargs="?", default=None, metavar="TIME",
                   help="Time (HH:MM)")
    p.add_argument("--inactive", action="store_true",
                   help="Use inactive [brackets] instead of <active>")
    p.set_defaults(func=cmd_org_timestamp)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if not CORE_FILE or not ELISP_FILE:
        print("Error: ORG_GTD_CORE_FILE and ORG_GTD_ELISP_FILE must be set",
              file=sys.stderr)
        sys.exit(1)

    rc = args.func(args)
    sys.exit(rc)


if __name__ == "__main__":
    main()
