#!/usr/bin/env python3
"""org-gtd-cli: CLI for org-mode GTD system management.

Thin dispatch layer — all org logic lives in org-gtd-cli.el.
This script parses arguments and calls Emacs in batch mode.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# --- Paths (set by Nix wrapper or environment) ---
CORE_FILE = os.environ.get("ORG_GTD_CORE_FILE", "")
ELISP_FILE = os.environ.get("ORG_GTD_ELISP_FILE", "")
ORG_DIR = os.environ.get("ORG_DIRECTORY", os.path.expanduser("~/Nextcloud/org/"))
EMACS_BIN = "emacs"
EMACSCLIENT_BIN = "emacsclient"

# Daemon mode: opt-in via ORG_GTD_CLI_DAEMON=1
DAEMON_ENABLED = os.environ.get("ORG_GTD_CLI_DAEMON") == "1"
_TMPDIR = os.environ.get("TMPDIR", "/tmp")
# Daemon state (socket + user-emacs-dir) MUST be per-user. On a shared host
# (e.g. convox, where both `olli` and `agent` reach this code) a path that is
# not namespaced by uid lets whoever runs first create a 0700 dir that wedges
# the other user's daemon: os.path.exists() reads the resulting EACCES as
# "absent", and the wrapper then either latches onto a foreign, stale daemon or
# silently fails to bind. Root the state at XDG_RUNTIME_DIR (/run/user/$UID —
# already per-user and auto-cleaned on logout) when set, else a uid-suffixed
# dir under TMPDIR. Either way the dir name carries our uid.
_DAEMON_BASE = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or _TMPDIR,
    f"org-gtd-cli-{os.getuid()}",
)
# Socket dir needs 700 permissions (Emacs server security requirement)
_SOCKET_DIR = _DAEMON_BASE
SOCKET_PATH = os.path.join(_SOCKET_DIR, "server")


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


def validate_target(args):
    """Ensure exactly one of SUBSTR/parent or --id addresses the task (non-batch path)."""
    substr = getattr(args, 'substr', None) or getattr(args, 'parent', None)
    tid = getattr(args, 'task_id', None)
    if tid and substr:
        print("Error: --id and SUBSTR are mutually exclusive", file=sys.stderr)
        return False
    if not tid and not substr:
        print("Error: provide SUBSTR or --id", file=sys.stderr)
        return False
    return True


def id_wrap(expr, args, *, mutation):
    """Wrap EXPR to bind forced-id / forced-create-id for this one call (let -> daemon-safe)."""
    tid = getattr(args, 'task_id', None)
    create = mutation and not getattr(args, 'dry_run', False)
    if not tid and not create:
        return expr
    return (f'(let ((org-gtd-cli/forced-id {to_elisp(tid)}) '
            f'(org-gtd-cli/forced-create-id {"t" if create else "nil"})) {expr})')


def resolve_body_text(text: str | None, body_file: str | None,
                      auto_stdin: bool = False) -> str | None:
    """Resolve body text from positional arg, --body-file, or stdin.

    Precedence: --body-file > positional text > auto-stdin.
    --body-file with path "-" reads stdin.
    Rejects literal '-' as positional text (agent likely intended stdin).

    When auto_stdin is True and neither text nor body_file is provided,
    reads from stdin if it's a pipe (not a TTY).
    """
    if body_file is not None:
        if body_file == "-":
            return sys.stdin.read()
        with open(body_file) as f:
            return f.read()
    if text is not None:
        if text == "-":
            print("Error: literal '-' as body text is not supported. "
                  "Use --body-file - to read from stdin, or "
                  "--body-file FILE to read from a file.",
                  file=sys.stderr)
            return None  # sentinel — caller should exit 1
        return text
    if auto_stdin and not sys.stdin.isatty():
        return sys.stdin.read()
    return None


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


def _run_batch(expr: str, json_mode: bool = False, full_mode: bool = False) -> int:
    """Run an elisp expression in batch Emacs. Returns exit code."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            EMACS_BIN, "--batch", "-q",
            "--eval", f'(setq user-emacs-directory "{tmpdir}/")',
            "--eval", f'(setenv "ORG_DIRECTORY" "{escape_elisp(ORG_DIR)}")',
            "-l", CORE_FILE,
            "-l", ELISP_FILE,
            "--eval", expr,
        ]
        env = os.environ.copy()
        if json_mode:
            env["ORG_GTD_CLI_JSON"] = "1"
        if full_mode:
            env["ORG_GTD_CLI_FULL"] = "1"
        # Emacs --batch sends its own diagnostics to stderr; let them through
        result = subprocess.run(cmd, capture_output=False, env=env)
        return result.returncode


def _socket_is_ours() -> bool:
    """True iff the daemon socket exists and is owned by the current user.

    A bare os.path.exists() treats an EACCES (a foreign-owned 0700 dir
    squatting our path) as "absent", after which the wrapper wedges trying to
    bind a socket it has no permission to create. Checking st_uid makes reuse
    safe and the failure mode loud instead of silent.
    """
    try:
        return os.stat(SOCKET_PATH).st_uid == os.getuid()
    except OSError:
        return False


def _ensure_daemon() -> None:
    """Start the Emacs daemon if it's not already running."""
    if _socket_is_ours():
        return
    # If our per-uid dir somehow exists but is owned by another user, refuse
    # rather than silently fall through to a daemon we cannot drive.
    try:
        if os.stat(_SOCKET_DIR).st_uid != os.getuid():
            print(f"Error: daemon dir {_SOCKET_DIR} is owned by another user; "
                  "refusing to reuse a foreign daemon", file=sys.stderr)
            return
    except FileNotFoundError:
        pass
    os.makedirs(_SOCKET_DIR, mode=0o700, exist_ok=True)
    user_emacs_dir = os.path.join(_DAEMON_BASE, "emacs.d")
    os.makedirs(user_emacs_dir, exist_ok=True)
    cmd = [
        EMACS_BIN, "--daemon", "-q",
        "--eval", f'(setq server-name "{escape_elisp(SOCKET_PATH)}")',
        "--eval", f'(setq user-emacs-directory "{user_emacs_dir}/")',
        "--eval", f'(setenv "ORG_DIRECTORY" "{escape_elisp(ORG_DIR)}")',
        "-l", CORE_FILE,
        "-l", ELISP_FILE,
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Wait for socket to appear
    for _ in range(200):  # 200 * 50ms = 10s
        if _socket_is_ours():
            return
        time.sleep(0.05)
    print("Error: Emacs daemon failed to start (timeout)", file=sys.stderr)


def _read_file_safe(path: str) -> str:
    """Read a file's contents, returning empty string if missing."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _run_daemon(expr: str, json_mode: bool = False, full_mode: bool = False, *, _retried: bool = False) -> int:
    """Run an elisp expression via emacsclient against the daemon."""
    _ensure_daemon()

    # Unique per-invocation output dir: concurrent CLI calls share the daemon,
    # and fixed paths would let one call clobber another's stdout/exit code.
    out_dir = tempfile.mkdtemp(prefix="org-gtd-cli-out-", dir=_TMPDIR)
    stdout_file = os.path.join(out_dir, "stdout")
    stderr_file = os.path.join(out_dir, "stderr")
    exit_file = os.path.join(out_dir, "exit")

    json_flag = "t" if json_mode else "nil"
    full_flag = "t" if full_mode else "nil"
    wrapped = (f'(org-gtd-cli/daemon-dispatch'
               f' (lambda () {expr})'
               f' {json_flag}'
               f' {full_flag}'
               f' "{escape_elisp(ORG_DIR)}"'
               f' "{escape_elisp(stdout_file)}"'
               f' "{escape_elisp(stderr_file)}"'
               f' "{escape_elisp(exit_file)}")')

    try:
        result = subprocess.run(
            [EMACSCLIENT_BIN, "--socket-name", SOCKET_PATH, "--eval", wrapped],
            capture_output=True, text=True,
        )

        if result.returncode != 0 and not _retried:
            # Stale socket or daemon died — clean up and retry once
            # (the retry allocates its own output dir)
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass
            return _run_daemon(expr, json_mode, full_mode, _retried=True)

        if result.returncode != 0:
            print(f"Error: emacsclient failed: {result.stderr.strip()}", file=sys.stderr)
            return 1

        stdout = _read_file_safe(stdout_file)
        stderr = _read_file_safe(stderr_file)
        exit_code_str = _read_file_safe(exit_file).strip()
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)

    try:
        return int(exit_code_str)
    except ValueError:
        return 0


def run_elisp(expr: str, json_mode: bool = False, full_mode: bool = False) -> int:
    """Run an elisp expression. Uses daemon if enabled, otherwise batch."""
    if DAEMON_ENABLED:
        return _run_daemon(expr, json_mode, full_mode)
    return _run_batch(expr, json_mode, full_mode)


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
    if args.json:
        print('{"error": "--json is not supported for org-timestamp"}',
              file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/org-timestamp {to_elisp(args.date)} '
            f'{to_elisp(args.time)} {to_elisp("t" if args.inactive else None)})')
    return run_elisp(expr)


def cmd_agenda(args):
    tag = normalize_tags(args.tag)
    expr = (f'(org-gtd-cli/agenda {to_elisp(args.state)} '
            f'{to_elisp(tag)} {to_elisp(getattr(args, "from"))} {to_elisp(args.to)})')
    return run_elisp(expr, json_mode=args.json, full_mode=getattr(args, 'full', False))


def cmd_search(args):
    tag = normalize_tags(args.tag)
    if not args.substr and not tag and not args.state:
        print("Error: provide SUBSTR, --tag, or --state", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/search {to_elisp(args.substr)} '
            f'{to_elisp(args.state)} {to_elisp(tag)} {to_elisp(args.file)})')
    return run_elisp(expr, json_mode=args.json, full_mode=getattr(args, 'full', False))


def cmd_show(args):
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/show {to_elisp(args.substr)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.plain else None)})')
    expr = id_wrap(expr, args, mutation=False)
    return run_elisp(expr, json_mode=args.json)


def cmd_subtasks(args):
    if not validate_target(args):
        return 1
    expr = f'(org-gtd-cli/subtasks {to_elisp(args.substr)} {to_elisp(args.index)})'
    expr = id_wrap(expr, args, mutation=False)
    return run_elisp(expr, json_mode=args.json, full_mode=getattr(args, 'full', False))


def cmd_categories(args):
    expr = f'(org-gtd-cli/categories {to_elisp(args.file)})'
    return run_elisp(expr, json_mode=args.json)


def cmd_outline(args):
    expr = f'(org-gtd-cli/outline {to_elisp(args.file)})'
    return run_elisp(expr, json_mode=args.json,
                     full_mode=getattr(args, 'full', False))


def cmd_projects(args):
    return run_elisp("(org-gtd-cli/projects)", json_mode=args.json)


def cmd_list_tags(args):
    return run_elisp("(org-gtd-cli/list-tags)", json_mode=args.json)


def cmd_process_agent_tasks(_args):
    print("Error: process-agent-tasks has been removed. "
          "Use: search --tag @agent --state TODO,NEXT [--json]",
          file=sys.stderr)
    return 1


def cmd_add_task(args):
    title = args.title or args.title_flag
    if not title:
        print("Error: TITLE is required", file=sys.stderr)
        return 1
    raw_body = resolve_body_text(args.body, args.body_file)
    if args.body == "-" and raw_body is None:
        return 1  # resolve_body_text already printed error
    body = unescape_body_newlines(raw_body) if raw_body else raw_body
    expr = (f'(org-gtd-cli/add-task {to_elisp(title)} {to_elisp(body)} '
            f'{to_elisp(args.tags)} {to_elisp(args.schedule)} '
            f'{to_elisp(args.deadline)} {to_elisp(args.priority)} '
            f'{to_elisp(args.file)} {to_elisp(args.category)} '
            f'{to_elisp(args.state)})')
    return run_elisp(expr, json_mode=args.json)


def cmd_add_subtask(args):
    # With --id addressing the parent, a lone positional is the TITLE, but
    # argparse fills the first optional positional (parent) — shift it over.
    if getattr(args, 'task_id', None) and args.parent and not args.title:
        args.title = args.parent
        args.parent = None
    if not args.title:
        print("Error: TITLE is required", file=sys.stderr)
        return 1
    if not validate_target(args):
        return 1
    raw_body = resolve_body_text(args.body, args.body_file)
    if args.body == "-" and raw_body is None:
        return 1
    body = unescape_body_newlines(raw_body) if raw_body else raw_body
    expr = (f'(org-gtd-cli/add-subtask {to_elisp(args.parent)} '
            f'{to_elisp(args.title)} {to_elisp(body)} '
            f'{to_elisp(args.tags)} {to_elisp(args.schedule)} '
            f'{to_elisp(args.deadline)} {to_elisp(args.priority)} '
            f'{to_elisp(args.state)} {to_elisp(args.index)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_add_event(args):
    if not args.title:
        print("Error: TITLE is required", file=sys.stderr)
        return 1
    if not args.date:
        print("Error: --date is required", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/add-event {to_elisp(args.title)} '
            f'{to_elisp(args.date)} {to_elisp(args.time)} '
            f'{to_elisp(args.tag)} {to_elisp(args.file)} '
            f'{to_elisp(args.end_date)})')
    return run_elisp(expr, json_mode=args.json)


def cmd_add_note(args):
    title = args.title_pos or args.title
    if not title:
        print("Error: TITLE is required (positional or --title)", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/add-note {to_elisp(title)} '
            f'{to_elisp(args.link_task)} {to_elisp(args.tags)} '
            f'{to_elisp(args.sections)})')
    return run_elisp(expr, json_mode=args.json)


def cmd_append_body(args):
    # With --id addressing the task, a lone positional is the TEXT, but
    # argparse fills the first optional positional (substr) — shift it over.
    if getattr(args, 'task_id', None) and args.substr and args.text is None:
        args.text = args.substr
        args.substr = None
    text = resolve_body_text(args.text, args.body_file, auto_stdin=True)
    if args.text == "-" and text is None:
        return 1
    if text is None:
        print("Error: provide TEXT, --body-file, or pipe to stdin", file=sys.stderr)
        return 1
    text = unescape_body_newlines(text) if text else text
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/append-body {to_elisp(args.substr)} '
            f'{to_elisp(text)} {to_elisp(args.index)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_set_body(args):
    # With --id addressing the task, a lone positional is the TEXT, but
    # argparse fills the first optional positional (substr) — shift it over.
    if getattr(args, 'task_id', None) and args.substr and args.text is None:
        args.text = args.substr
        args.substr = None
    text = resolve_body_text(args.text, args.body_file, auto_stdin=True)
    if args.text == "-" and text is None:
        return 1
    if text is None and args.body_file is None:
        print("Error: provide TEXT, --body-file, or pipe to stdin", file=sys.stderr)
        return 1
    text = unescape_body_newlines(text) if text else text
    # set-body allows empty string to remove body — pass "" not nil
    text_elisp = '""' if text is not None and text == "" else to_elisp(text)
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/set-body {to_elisp(args.substr)} '
            f'{text_elisp} {to_elisp(args.index)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_add_session_id(args):
    if not args.substr or not args.session_id:
        print("Error: SUBSTR and SESSION_ID are required", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/add-session-id {to_elisp(args.substr)} '
            f'{to_elisp(args.session_id)} {to_elisp(args.index)})')
    return run_elisp(expr, json_mode=args.json)


def cmd_get_session_ids(args):
    expr = (f'(org-gtd-cli/get-session-ids {to_elisp(args.substr)} '
            f'{to_elisp(args.index)})')
    return run_elisp(expr, json_mode=args.json)


def cmd_set_done(args):
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/set-done {to_elisp(args.substr)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_set_state(args):
    # With --id addressing the task, a lone positional is the STATE, but
    # argparse fills the first optional positional (substr) — shift it over.
    if getattr(args, 'task_id', None) and args.substr and not args.state:
        args.state = args.substr
        args.substr = None
    if not args.state:
        print("Error: STATE is required", file=sys.stderr)
        return 1
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/set-state {to_elisp(args.substr)} '
            f'{to_elisp(args.state)} {to_elisp(args.index)} '
            f'{to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_set_priority(args):
    # With --id addressing the task, a lone positional is the PRIORITY, but
    # argparse fills the first optional positional (substr) — shift it over.
    if getattr(args, 'task_id', None) and args.substr and not args.priority:
        args.priority = args.substr
        args.substr = None
    if not args.priority and not args.clear:
        print("Error: provide a PRIORITY (A, B, or C) or --clear", file=sys.stderr)
        return 1
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/set-priority {to_elisp(args.substr)} '
            f'{to_elisp(args.priority)} {to_elisp("t" if args.clear else None)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_set_cancelled(args):
    expr = (f'(org-gtd-cli/set-state {to_elisp(args.substr)} '
            f'"CANCELLED" {to_elisp(args.index)} '
            f'{to_elisp("t" if args.dry_run else None)})')
    return run_elisp(expr, json_mode=args.json)


def cmd_set_next(args):
    if not validate_target(args):
        return 1
    expr = f'(org-gtd-cli/set-next {to_elisp(args.substr)} {to_elisp(args.index)})'
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_refile(args):
    if not validate_target(args):
        return 1
    if args.to and args.category:
        print("Error: --to and --category are mutually exclusive", file=sys.stderr)
        return 1
    if not args.to and not args.category:
        print("Error: one of --to or --category is required", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/refile {to_elisp(args.substr)} '
            f'{to_elisp(args.to)} {to_elisp(args.category)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


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
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/move {to_elisp(args.substr)} '
            f'{to_elisp(direction)} {to_elisp(sibling)} {to_elisp(args.index)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_rename(args):
    # With --id addressing the task, a lone positional is the NEWTITLE, but
    # argparse fills the first optional positional (substr) — shift it over.
    if getattr(args, 'task_id', None) and args.substr and not args.newtitle:
        args.newtitle = args.substr
        args.substr = None
    if not args.newtitle:
        print("Error: NEWTITLE is required", file=sys.stderr)
        return 1
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/rename {to_elisp(args.substr)} '
            f'{to_elisp(args.newtitle)} {to_elisp(args.index)} '
            f'{to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_set_schedule(args):
    # With --id addressing the task, a lone positional is the DATE, but
    # argparse fills the first optional positional (substr) — shift it over.
    if getattr(args, 'task_id', None) and args.substr and not args.date:
        args.date = args.substr
        args.substr = None
    if not args.date and not args.clear:
        print("Error: provide a DATE or --clear", file=sys.stderr)
        return 1
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/set-schedule {to_elisp(args.substr)} '
            f'{to_elisp(args.date)} {to_elisp(args.time)} '
            f'{to_elisp("t" if args.clear else None)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_set_deadline(args):
    # With --id addressing the task, a lone positional is the DATE, but
    # argparse fills the first optional positional (substr) — shift it over.
    if getattr(args, 'task_id', None) and args.substr and not args.date:
        args.date = args.substr
        args.substr = None
    if not args.date and not args.clear:
        print("Error: provide a DATE or --clear", file=sys.stderr)
        return 1
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/set-deadline {to_elisp(args.substr)} '
            f'{to_elisp(args.date)} {to_elisp(args.time)} '
            f'{to_elisp("t" if args.clear else None)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_set_tags(args):
    if not validate_target(args):
        return 1
    add_flag = getattr(args, 'add', None)
    remove_flag = getattr(args, 'remove', None)
    if add_flag:
        # Route --add to add-tags
        expr = (f'(org-gtd-cli/add-tags {to_elisp(args.substr)} '
                f'{to_elisp(add_flag)} '
                f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
        expr = id_wrap(expr, args, mutation=True)
        return run_elisp(expr, json_mode=args.json)
    if remove_flag:
        # Route --remove to remove-tags
        expr = (f'(org-gtd-cli/remove-tags {to_elisp(args.substr)} '
                f'{to_elisp(remove_flag)} '
                f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
        expr = id_wrap(expr, args, mutation=True)
        return run_elisp(expr, json_mode=args.json)
    if args.tags is None:
        print("Error: --tags, --add, or --remove is required", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/set-tags {to_elisp(args.substr)} '
            f'{to_elisp(args.tags)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_add_tags(args):
    if not validate_target(args):
        return 1
    if args.tags is None:
        print("Error: --tags is required", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/add-tags {to_elisp(args.substr)} '
            f'{to_elisp(args.tags)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_set_property(args):
    if not args.key:
        print("Error: --key NAME is required", file=sys.stderr)
        return 1
    if args.value is None and not args.clear:
        print("Error: provide --value VALUE or --clear", file=sys.stderr)
        return 1
    if args.value is not None and args.clear:
        print("Error: --value and --clear are mutually exclusive", file=sys.stderr)
        return 1
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/set-property {to_elisp(args.substr)} '
            f'{to_elisp(args.key)} {to_elisp(args.value)} '
            f'{to_elisp("t" if args.clear else None)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_agenda_view(args):
    key = args.key if args.key else " "
    expr = f'(org-gtd-cli/agenda-view {to_elisp(key)} {to_elisp(args.date)})'
    return run_elisp(expr, json_mode=args.json,
                     full_mode=getattr(args, 'full', False))


def cmd_archive(args):
    if args.all and (args.substr or getattr(args, 'task_id', None)):
        print("Error: --all and SUBSTR are mutually exclusive", file=sys.stderr)
        return 1
    if args.all:
        expr = f'(org-gtd-cli/archive-all {to_elisp("t" if args.dry_run else None)})'
        return run_elisp(expr, json_mode=args.json)
    # Single-task form: address by SUBSTR or --id
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/archive {to_elisp(args.substr)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_delete(args):
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/delete {to_elisp(args.substr)} '
            f'{to_elisp(args.index)} {to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
    return run_elisp(expr, json_mode=args.json)


def cmd_fix_timestamps(_args):
    print("Error: fix-timestamps has been removed.", file=sys.stderr)
    return 1


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
  list-tags         List all tags in use with counts
  process-agent-tasks  (removed, use: search --tag @agent --state TODO,NEXT)

Creating:
  add-task          Add a task (default: inbox)
  add-subtask       Add a child task under a parent
  add-event         Add a calendar event
  add-note          Create a note file in agent-notes/

Modifying:
  set-done          Mark task DONE (with auto-progress)
  set-state         Change TODO state (DONE here skips set-done's auto-progress)
  set-next          Promote task/child to NEXT
  set-priority      Set priority A/B/C
  set-cancelled     Mark task CANCELLED
  refile            Move task to a different heading
  move              Reorder a task among siblings
  rename            Change task heading text
  set-schedule      Set/clear SCHEDULED timestamp
  set-deadline      Set/clear DEADLINE timestamp
  set-tags          Replace all tags
  add-tags          Append tags (no duplicates)
  set-property      Set or clear a generic org property
  append-body       Append text to task body
  set-body          Replace task body

Batch:
  batch             Run many commands in one call (JSON array of
                    {"command", "args"} objects on stdin)
                    Homogeneous alternative: --batch <subcommand>

Maintenance:
  archive           Archive completed tasks
  delete            Delete a task (exact match, no projects)
  fix-timestamps    (removed)
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
        usage="org-gtd-cli [--json] <command> [options]",
    )
    parser.add_argument("--json", action="store_true",
                        help="Output structured JSON instead of human-readable text")
    parser.add_argument("--batch", action="store_true",
                        help="With a subcommand: read a JSON array of items from "
                             "stdin, run them all in one process (for mixed "
                             "commands, see the 'batch' subcommand)")
    sub = parser.add_subparsers(dest="command")

    # --- Querying ---

    p = sub.add_parser("show", help="Show full task details")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring to match (optional with --batch)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--plain", action="store_true", help="Minimal output")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("search", help="Find tasks by heading substring")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring to match (optional when --tag or --state is provided)")
    p.add_argument("--state", help="Filter by state (comma-separated, or 'all')")
    p.add_argument("--tag", "--tags", action="append", dest="tag",
                   help="Filter by tag (repeat for AND, comma within for OR)")
    p.add_argument("--file", help="Restrict to a single file")
    p.add_argument("--full", action="store_true",
                   help="Include body text in results")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("agenda", help="List tasks with state/tag/date filters")
    p.add_argument("--state", help="Filter by state (comma-separated)")
    p.add_argument("--tag", "--tags", action="append", dest="tag",
                   help="Filter by tag (repeat for AND, comma within for OR)")
    p.add_argument("--from", dest="from", help="Start date (YYYY-MM-DD)")
    p.add_argument("--to", help="End date (YYYY-MM-DD)")
    p.add_argument("--full", action="store_true",
                   help="Include body text in results")
    p.set_defaults(func=cmd_agenda)

    p = sub.add_parser("agenda-view", help="Run a pre-built agenda view")
    p.add_argument("key", nargs="?", default=" ",
                   help="Agenda view key (default: ' ' for full dashboard)")
    p.add_argument("--full", action="store_true",
                   help="Include body text in results (--json only)")
    p.add_argument("--date", help="Target date YYYY-MM-DD for the dated block")
    p.set_defaults(func=cmd_agenda_view)

    p = sub.add_parser("subtasks", help="List children of a project")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Parent heading substring")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--full", action="store_true",
                   help="Include body text in results")
    p.set_defaults(func=cmd_subtasks)

    p = sub.add_parser("categories", help="Show category tree for refile targets")
    p.add_argument("--file", help="Target file (default: tasks.org)")
    p.set_defaults(func=cmd_categories)

    p = sub.add_parser("outline", help="Full nested outline of an org file")
    p.add_argument("--file", help="Target file (default: tasks.org)")
    p.add_argument("--full", action="store_true",
                   help="Include server-rendered body_html")
    p.set_defaults(func=cmd_outline)

    p = sub.add_parser("projects", help="List active projects with progress")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("list-tags", help="List all tags in use with counts")
    p.set_defaults(func=cmd_list_tags)

    p = sub.add_parser("process-agent-tasks",
                       help="(removed) Use: search --tag @agent --state TODO,NEXT")
    p.set_defaults(func=cmd_process_agent_tasks)

    # --- Creating ---

    p = sub.add_parser("add-task", help="Add a task (default: inbox)")
    p.add_argument("title", nargs="?", default=None, metavar="TITLE",
                   help="Task title")
    p.add_argument("--title", dest="title_flag", default=None,
                   help="Task title (alternative to positional)")
    p.add_argument("--body", help="Body text below the heading")
    p.add_argument("--body-file", dest="body_file",
                   help="Read body from FILE (use - for stdin)")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--schedule", help="SCHEDULED date")
    p.add_argument("--deadline", help="DEADLINE date")
    p.add_argument("--priority", help="Priority: A, B, or C")
    p.add_argument("--file", help="Target file (relative to ORG_DIRECTORY)")
    p.add_argument("--category", help="Insert under this heading in tasks.org")
    p.add_argument("--state", help="Initial state (default: TODO)")
    p.set_defaults(func=cmd_add_task)

    p = sub.add_parser("add-subtask", help="Add a child task under a parent")
    p.add_argument("parent", nargs="?", default=None, metavar="SUBSTR",
                   help="Parent heading substring")
    p.add_argument("title", nargs="?", default=None, metavar="TITLE",
                   help="Subtask title (optional with --batch)")
    p.add_argument("--id", dest="task_id",
                   help="Resolve the PARENT task by its org :ID:")
    p.add_argument("--body", help="Body text")
    p.add_argument("--body-file", dest="body_file",
                   help="Read body from FILE (use - for stdin)")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--schedule", help="SCHEDULED date")
    p.add_argument("--deadline", help="DEADLINE date")
    p.add_argument("--priority", help="Priority: A, B, or C")
    p.add_argument("--state", help="Initial state (default: TODO)")
    p.add_argument("--index", help="Disambiguate parent with 1-based index")
    p.set_defaults(func=cmd_add_subtask)

    p = sub.add_parser("add-event", help="Add a calendar event")
    p.add_argument("title", nargs="?", default=None, metavar="TITLE",
                   help="Event title (optional with --batch)")
    p.add_argument("--date", help="Event date (YYYY-MM-DD)")
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
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring (optional with --batch)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_done)

    p = sub.add_parser(
        "set-state",
        help="Change TODO state (set-state DONE skips set-done's auto-progress)",
        description="Change TODO state. Note: `set-state SUBSTR DONE` bypasses "
                    "set-done's auto-progress side effects (sibling NEXT "
                    "promotion, project-needs-review tagging) — prefer "
                    "set-done for completing tasks.")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("state", nargs="?", default=None, metavar="STATE",
                   help="Target state: TODO, NEXT, DONE, WAITING, DEFER, CANCELLED")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_state)

    p = sub.add_parser("set-next", help="Promote task/child to NEXT")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_set_next)

    p = sub.add_parser("set-priority", help="Set priority A/B/C")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("priority", nargs="?", default=None, metavar="PRIORITY",
                   help="Priority: A, B, or C")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
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
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring (optional with --batch)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--to", help="Exact match on heading text")
    p.add_argument("--category", help="Substring match on category headings")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_refile)

    p = sub.add_parser("move", help="Reorder a task among siblings")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    direction = p.add_mutually_exclusive_group()
    direction.add_argument("--up", action="store_true", help="Move up")
    direction.add_argument("--down", action="store_true", help="Move down")
    direction.add_argument("--before", metavar="SIBLING", help="Move before sibling")
    direction.add_argument("--after", metavar="SIBLING", help="Move after sibling")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("rename", help="Change task heading text")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("newtitle", nargs="?", default=None, metavar="NEWTITLE",
                   help="New heading text")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("set-schedule", help="Set/clear SCHEDULED timestamp")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("date", nargs="?", default=None, metavar="DATE",
                   help="Date (YYYY-MM-DD)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--time", help="Time (HH:MM)")
    p.add_argument("--clear", action="store_true", help="Remove schedule")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_schedule)

    p = sub.add_parser("set-deadline", help="Set/clear DEADLINE timestamp")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("date", nargs="?", default=None, metavar="DATE",
                   help="Date (YYYY-MM-DD)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--time", help="Time (HH:MM)")
    p.add_argument("--clear", action="store_true", help="Remove deadline")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_deadline)

    p = sub.add_parser("set-tags", help="Replace all tags")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring (optional with --batch)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    tag_group = p.add_mutually_exclusive_group()
    tag_group.add_argument("--tags", help="Tags to set (comma-separated, empty string to clear)")
    tag_group.add_argument("--add", help="Tags to add (comma-separated)")
    tag_group.add_argument("--remove", help="Tags to remove (comma-separated)")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_tags)

    p = sub.add_parser("add-tags", help="Append tags (no duplicates)")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring (optional with --batch)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--tags", help="Tags to add (comma-separated, optional with --batch)")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_add_tags)

    p = sub.add_parser("set-property",
                       help="Set or clear a generic org property on a task")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--key", required=True, metavar="NAME",
                   help="Property name (e.g. AGENT_EFFORT)")
    p.add_argument("--value", default=None, metavar="VALUE",
                   help="Property value to set")
    p.add_argument("--clear", action="store_true", help="Remove the property")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_property)

    p = sub.add_parser("append-body", help="Append text to task body")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("text", nargs="?", default=None, metavar="TEXT",
                   help="Text to append (optional when --body-file is used)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--body-file", dest="body_file",
                   help="Read text from FILE (use - for stdin)")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_append_body)

    p = sub.add_parser("set-body", help="Replace task body")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("text", nargs="?", default=None, metavar="TEXT",
                   help="New body text (optional when --body-file is used)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--body-file", dest="body_file",
                   help="Read text from FILE (use - for stdin)")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_set_body)

    # --- Session tracking ---

    p = sub.add_parser("add-session-id", help="Add agent session ID to task LOGBOOK")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring (optional with --batch)")
    p.add_argument("session_id", nargs="?", default=None, metavar="SESSION_ID",
                   help="Session ID in format agent:uuid (optional with --batch)")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_add_session_id)

    p = sub.add_parser("get-session-ids", help="Get agent session IDs from task LOGBOOK")
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_get_session_ids)

    # --- Batch ---

    p = sub.add_parser(
        "batch",
        help="Run many commands in one call (JSON array on stdin)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
Run several commands in one Emacs process.

stdin: JSON array of {"command": NAME, "args": {...}} objects.
Supported commands: add-event, add-session-id, add-subtask, add-task,
add-tags, delete, refile, set-done, set-tags, show.

Args use the same field names as --batch items:
  add-task        title (required), body, tags, schedule, deadline,
                  priority, file, category, state
  add-subtask     parent (required), title (required), body, tags,
                  schedule, deadline, priority, state
  add-event       title (required), date (required), time, tag, file,
                  end_date
  refile          heading (required), category (required)
  set-done        heading (required)
  delete          heading (required)
  show            heading (required)
  set-tags        heading (required), tags
  add-tags        heading (required), tags (required)
  add-session-id  heading (required), session_id (required)

Output: JSON with one result per input item, in order, plus a summary
(same shape as --batch <subcommand>). A failing item does not abort
the rest. Exit 0 if at least one item succeeded, 1 otherwise.

Example:
  echo '[{"command": "add-task", "args": {"title": "Buy milk"}},
         {"command": "set-done", "args": {"heading": "Call plumber"}}]' \\
    | org-gtd-cli --json batch
""")
    p.set_defaults(func=cmd_batch_mixed)

    # --- Maintenance ---

    p = sub.add_parser("archive", help="Archive completed tasks")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--all", action="store_true", help="Archive all eligible tasks")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("delete", help="Delete a task (exact match, no projects)")
    p.add_argument("substr", nargs="?", default=None, metavar="HEADING",
                   help="Exact heading text (optional with --batch)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("fix-timestamps", help="(removed)")
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


BATCH_COMMANDS = {
    "add-event", "add-session-id", "add-subtask", "add-task", "delete",
    "refile", "set-done", "show", "set-tags", "add-tags",
}


def batch_input_error(msg: str, json_mode: bool) -> int:
    """Print a batch input error to stderr (JSON error object in --json mode).

    Returns 1 so callers can `return batch_input_error(...)`.
    """
    if json_mode:
        print(json.dumps({"error": msg}), file=sys.stderr)
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


def read_batch_stdin(json_mode: bool):
    """Read and validate the JSON array on stdin for batch modes.

    Returns the parsed list, or None after printing an error (callers
    must exit 1).
    """
    if sys.stdin.isatty():
        batch_input_error("batch mode requires a JSON array on stdin", json_mode)
        return None
    json_str = sys.stdin.read().strip()
    if not json_str:
        batch_input_error("empty stdin (expected a JSON array)", json_mode)
        return None
    try:
        items = json.loads(json_str)
    except json.JSONDecodeError as e:
        batch_input_error(f"invalid JSON on stdin: {e}", json_mode)
        return None
    if not isinstance(items, list):
        batch_input_error("expected a JSON array of batch items", json_mode)
        return None
    return items


def cmd_batch(args):
    """Handle --batch mode: read JSON array from stdin, execute in one Emacs process."""
    command = args.command
    if command not in BATCH_COMMANDS:
        print(f"Error: --batch is not supported for '{command}'", file=sys.stderr)
        return 1

    items = read_batch_stdin(args.json)
    if items is None:
        return 1
    for i, item in enumerate(items):
        if not isinstance(item, (str, dict)):
            return batch_input_error(
                f"item {i}: expected a string or object, got {type(item).__name__}",
                args.json)

    # Shared args for commands that need them
    shared_arg = None
    if command == "add-subtask":
        shared_arg = getattr(args, 'parent', None)
        if not shared_arg:
            print("Error: --batch add-subtask requires parent SUBSTR positional", file=sys.stderr)
            return 1
    elif command == "refile":
        shared_arg = getattr(args, 'category', None)
        if not shared_arg:
            print("Error: --batch refile requires --category", file=sys.stderr)
            return 1

    expr = (f'(org-gtd-cli/batch {to_elisp(command)} '
            f'{to_elisp(json.dumps(items))} {to_elisp(shared_arg)})')
    return run_elisp(expr, json_mode=True)


def cmd_batch_mixed(args):
    """Handle the `batch` subcommand: per-item commands from a JSON array on stdin."""
    items = read_batch_stdin(args.json)
    if items is None:
        return 1
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return batch_input_error(
                f'item {i}: expected an object with "command" and "args"',
                args.json)
        command = item.get("command")
        if not isinstance(command, str) or not command:
            return batch_input_error(
                f'item {i}: missing required field "command"', args.json)
        if "args" in item and not isinstance(item["args"], dict):
            return batch_input_error(
                f'item {i}: "args" must be a JSON object', args.json)

    expr = f'(org-gtd-cli/batch-mixed {to_elisp(json.dumps(items))})'
    return run_elisp(expr, json_mode=True)


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        if args.batch:
            batch_input_error(
                "--batch requires a subcommand. Use 'org-gtd-cli batch' "
                "(per-item {\"command\", \"args\"} objects on stdin) or "
                "'org-gtd-cli --batch <subcommand>' (one command, "
                "homogeneous items on stdin)",
                args.json)
            sys.exit(1)
        parser.print_help()
        sys.exit(0)

    if not CORE_FILE or not ELISP_FILE:
        print("Error: ORG_GTD_CORE_FILE and ORG_GTD_ELISP_FILE must be set",
              file=sys.stderr)
        sys.exit(1)

    # Handle --batch mode (the `batch` subcommand dispatches normally;
    # a redundant --batch flag on it is ignored)
    if args.batch and args.command != "batch":
        rc = cmd_batch(args)
        sys.exit(rc)

    rc = args.func(args)
    sys.exit(rc)


if __name__ == "__main__":
    main()
