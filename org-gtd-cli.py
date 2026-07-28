#!/usr/bin/env python3
"""org-gtd-cli: CLI for org-mode GTD system management.

Thin dispatch layer — all org logic lives in org-gtd-cli.el.
This script parses arguments and calls Emacs in batch mode.
"""

import argparse
import contextlib
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

# --- Paths (set by Nix wrapper or environment) ---
CORE_FILE = os.environ.get("ORG_GTD_CORE_FILE", "")
ELISP_FILE = os.environ.get("ORG_GTD_ELISP_FILE", "")
ORG_DIR = os.environ.get("ORG_DIRECTORY", os.path.expanduser("~/org/"))
EMACS_BIN = "emacs"
EMACSCLIENT_BIN = "emacsclient"


def _canonical_path(path: str) -> str:
    """Return a stable absolute path for daemon identity inputs."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def _file_identity(path: str) -> dict[str, object]:
    """Return path and content metadata that should select a fresh daemon."""
    real_path = _canonical_path(path)
    identity: dict[str, object] = {"path": real_path}
    try:
        st = os.stat(real_path)
        identity.update({
            "exists": True,
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
        })
        with open(real_path, "rb") as f:
            identity["sha256"] = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        identity["exists"] = False
    return identity


def _daemon_identity_hash() -> str:
    """Hash daemon state inputs into a short, filesystem-safe directory name."""
    identity = {
        "org_directory": _canonical_path(ORG_DIR),
        "core_file": _file_identity(CORE_FILE),
        "elisp_file": _file_identity(ELISP_FILE),
    }
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# Daemon mode: opt-in via ORG_GTD_CLI_DAEMON=1
DAEMON_ENABLED = os.environ.get("ORG_GTD_CLI_DAEMON") == "1"
_TMPDIR = os.environ.get("TMPDIR", "/tmp")
# Idle TTL for the daemon: seconds since last completed dispatch after which
# the daemon self-terminates. Default 7200 (two hours). `0` = immortal.
# Negative or non-integer values are errors for daemon-backed commands; they
# must not start or dispatch to a daemon. Parsed at call time so an already-
# running identity picks up a new value on its next dispatch.
_DEFAULT_DAEMON_TTL = 7200


def _parse_daemon_ttl(raw):
    """Return (ttl_seconds, error_message) for `ORG_GTD_CLI_DAEMON_TTL' RAW.

    Unset or empty → the documented default. `0` → immortal (no timer).
    Positive integers → seconds. Negative or non-integer → error message
    and no daemon start (per issue #26 spec)."""
    if raw is None or raw == "":
        return _DEFAULT_DAEMON_TTL, None
    if not re.fullmatch(r"-?\d+", raw):
        return None, (f"ORG_GTD_CLI_DAEMON_TTL={raw!r} is not a base-10 "
                      "integer; unset it, use 0 for immortal, or a positive "
                      "number of seconds")
    val = int(raw, 10)
    if val < 0:
        return None, (f"ORG_GTD_CLI_DAEMON_TTL={raw!r} must be non-negative "
                      "(0 = immortal, positive = seconds)")
    return val, None
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
_DAEMON_IDENTITY = _daemon_identity_hash()
# Socket dir needs 700 permissions (Emacs server security requirement). Scope
# it by daemon identity so org dirs and loaded code versions cannot alias.
_SOCKET_DIR = os.path.join(_DAEMON_BASE, _DAEMON_IDENTITY)
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
    """Ensure exactly one of SUBSTR/parent or --id addresses the task (non-batch)."""
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
    """Wrap EXPR to bind forced-id/forced-create-id for one call (daemon-safe let)."""
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
            # Emit the once-per-invocation text-mode sync-conflict warning
            # (issue #35) before the command body runs. The marker check and
            # all warning logic live in elisp (reading `org-directory`); this
            # is a pure plumbing hook so the text line fires exactly once per
            # batch-Emacs invocation regardless of which command `expr` runs
            # (command bodies end in `kill-emacs`, so a prefix form is the one
            # guaranteed once-per-process entry point). No-op in JSON mode.
            "--eval", "(org-gtd-cli/emit-text-sync-conflict-warning)",
            "--eval", expr,
        ]
        env = os.environ.copy()
        if json_mode:
            env["ORG_GTD_CLI_JSON"] = "1"
        if full_mode:
            env["ORG_GTD_CLI_FULL"] = "1"
        # Emacs --batch sends its own diagnostics to stderr; let them through
        result = subprocess.run(cmd, capture_output=False, env=env, check=False)
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


def _ensure_owned_private_dir(path: str) -> bool:
    """Create PATH as 0700, or report a foreign-owned existing directory."""
    try:
        st = os.stat(path)
        if st.st_uid != os.getuid():
            print(f"Error: daemon dir {path} is owned by another user; "
                  "refusing to reuse a foreign daemon", file=sys.stderr)
            return False
        os.chmod(path, 0o700)
        return True
    except FileNotFoundError:
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
        return True


def _ensure_daemon() -> None:
    """Start the Emacs daemon if it's not already running."""
    if _socket_is_ours():
        return
    if not _ensure_owned_private_dir(_DAEMON_BASE):
        return
    if not _ensure_owned_private_dir(_SOCKET_DIR):
        return
    user_emacs_dir = os.path.join(_SOCKET_DIR, "emacs.d")
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


def _run_daemon(
    expr: str,
    json_mode: bool = False,
    full_mode: bool = False,
    *,
    _retried: bool = False,
) -> int:
    """Run an elisp expression via emacsclient against the daemon."""
    ttl, ttl_err = _parse_daemon_ttl(os.environ.get("ORG_GTD_CLI_DAEMON_TTL"))
    if ttl_err:
        # Reject before spawning or dispatching: an invalid TTL must not
        # start a daemon (issue #26 spec).
        if json_mode:
            print(json.dumps({"error": ttl_err}))
        else:
            print(f"Error: {ttl_err}", file=sys.stderr)
        return 1

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
               f' "{escape_elisp(exit_file)}"'
               f' {ttl})')

    try:
        result = subprocess.run(
            [EMACSCLIENT_BIN, "--socket-name", SOCKET_PATH, "--eval", wrapped],
            capture_output=True, text=True, check=False,
        )

        if result.returncode != 0 and not _retried:
            # Stale socket or daemon died — clean up and retry once
            # (the retry allocates its own output dir)
            with contextlib.suppress(OSError):
                os.unlink(SOCKET_PATH)
            return _run_daemon(expr, json_mode, full_mode, _retried=True)

        if result.returncode != 0:
            print(
                f"Error: emacsclient failed: {result.stderr.strip()}",
                file=sys.stderr,
            )
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


# =============================================================================
# Daemon management: `org-gtd-cli daemon status | stop | gc`
# =============================================================================
#
# All three run regardless of `ORG_GTD_CLI_DAEMON', never call
# `_ensure_daemon()', and never create a daemon. They are also NOT available
# through homogeneous or mixed batch modes (see build_parser).
#
# Discovery inspects the current UID's own private identity directories
# beneath `_DAEMON_BASE'. Live daemons are probed by asking them to serialise
# `org-gtd-cli/daemon-info' via `emacsclient --eval'. emacsclient exits
# non-zero BOTH when nothing listens on the socket AND when the server is
# alive but the eval signals — e.g. a pre-#26 (upgrade-in-flight) daemon
# where `daemon-info' is void. The probe therefore falls back to
# `(emacs-pid)', which every Emacs answers: an answer means a live legacy
# daemon (shown in `daemon status' with a null TTL, kept by `daemon gc',
# stoppable by `daemon stop'); no answer means a stale socket (e.g. after
# SIGKILL), which `gc' cleans and `status' skips silently.
#
# Safety (spec §Files involved / §Acceptance criteria):
# - Never signal a PID we cannot confirm belongs to a current-UID Emacs
#   daemon owning the expected socket.
# - Recursive `shutil.rmtree` is allowed only for owned identity dirs whose
#   resolved path is strictly beneath `_DAEMON_BASE'.
# - Foreign-owned or malformed candidates are left alone and reported as
#   errors; commands exit 1 if any candidate cannot be safely handled.

_DAEMON_PROBE_ELISP = "(prin1-to-string (org-gtd-cli/daemon-info))"

# Liveness fallback: answered by every Emacs, including pre-#26 daemons
# that do not know `org-gtd-cli/daemon-info'.
_DAEMON_ALIVE_ELISP = "(emacs-pid)"

# Regex for a valid identity component (sha-256 slice from
# `_daemon_identity_hash'). Anything else is rejected as malformed.
_IDENTITY_HASH_RE = re.compile(r"\A[0-9a-f]{32}\Z")


def _current_uid() -> int:
    return os.getuid()


def _daemon_base_ok() -> bool:
    """Return True when `_DAEMON_BASE' either does not exist or is owned by us.

    A foreign-owned base directory is where cross-user squatting would land;
    the wrappers refuse to touch it and management commands should surface
    the situation rather than silently ignoring it."""
    try:
        st = os.stat(_DAEMON_BASE)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return st.st_uid == _current_uid()


def _list_identity_dirs():
    """Yield (identity_hash, dir_path) for every direct child of `_DAEMON_BASE'
    that looks like an owned identity directory.

    Foreign-owned or malformed entries are surfaced separately by the caller
    (they never yield successfully) — this helper returns only safe candidates
    plus a list of error descriptors."""
    candidates = []
    errors = []
    try:
        entries = os.listdir(_DAEMON_BASE)
    except FileNotFoundError:
        return candidates, errors
    except OSError as exc:
        errors.append({"path": _DAEMON_BASE,
                       "error": f"cannot list daemon base: {exc}"})
        return candidates, errors
    for name in sorted(entries):
        path = os.path.join(_DAEMON_BASE, name)
        try:
            st = os.lstat(path)
        except OSError as exc:
            errors.append({"path": path, "error": str(exc)})
            continue
        if not stat.S_ISDIR(st.st_mode):
            # Not a directory — daemon dirs are 0700 dirs. Ignore silently
            # (e.g. a stray file from an aborted spawn).
            continue
        if st.st_uid != _current_uid():
            errors.append({"path": path,
                           "error": (f"foreign-owned identity dir (uid "
                                     f"{st.st_uid}); refusing to inspect")})
            continue
        if not _IDENTITY_HASH_RE.match(name):
            errors.append({"path": path,
                           "error": (f"malformed identity component {name!r}; "
                                     "refusing to inspect")})
            continue
        candidates.append((name, path))
    return candidates, errors


def _socket_for_identity_dir(dir_path):
    return os.path.join(dir_path, "server")


def _probe_daemon(socket_path, timeout=3):
    """Ask a daemon for its `org-gtd-cli/daemon-info' plist.

    Returns (info_dict | None, error | None). A dead/stale socket, foreign
    server, wedged daemon, or unrecognised probe response counts as a
    non-live daemon and yields `(None, error_message)`. A LIVE daemon whose
    eval fails (a pre-#26 build where `daemon-info' is void) yields a
    synthetic `{"pid": ..., "ttl": None, "legacy": True}` — callers must
    treat it as running, never as a stale socket.
    """
    try:
        st = os.stat(socket_path)
    except FileNotFoundError:
        return None, "socket-missing"
    except OSError as exc:
        return None, f"socket-stat-failed: {exc}"
    if st.st_uid != _current_uid():
        return None, "foreign-socket"
    # Bounded probe: kill the emacsclient if the daemon does not answer.
    try:
        result = subprocess.run(
            [EMACSCLIENT_BIN, "--socket-name", socket_path,
             "--eval", _DAEMON_PROBE_ELISP],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "probe-timeout"
    except OSError as exc:
        return None, f"probe-failed: {exc}"
    if result.returncode != 0:
        # Ambiguous: emacsclient exits non-zero both when nothing listens
        # on the socket (connect failure) and when the server is alive but
        # the eval signalled (`*ERROR*: ... daemon-info ... void`). Only a
        # second probe with a form every Emacs answers can tell them apart.
        try:
            alive = subprocess.run(
                [EMACSCLIENT_BIN, "--socket-name", socket_path,
                 "--eval", _DAEMON_ALIVE_ELISP],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "probe-timeout"
        except OSError as exc:
            return None, f"probe-failed: {exc}"
        if alive.returncode != 0:
            # Nothing answers at all: a leftover socket with no server
            # behind it (unclean daemon death, e.g. SIGKILL).
            return None, "stale-socket"
        try:
            pid = int(alive.stdout.strip())
        except (TypeError, ValueError):
            pid = None
        return {"pid": pid, "ttl": None, "legacy": True}, None
    return _parse_daemon_info(result.stdout), None


def _unescape_prin1(s):
    """Undo `prin1' string escaping: `\\\"` -> `\"`, `\\\\` -> `\\`.

    `prin1' emits only those two escapes for this payload (multibyte
    strings print their non-ASCII characters raw). Notably NOT
    `unicode_escape`: that codec decodes the intermediate bytes as
    latin-1, so any non-ASCII path (e.g. a UTF-8 `ö`) round-trips to
    mojibake and `gc` would reap a daemon whose org directory exists."""
    return re.sub(r"\\(.)", r"\1", s)


def _parse_daemon_info(raw):
    """Turn the `prin1-to-string' output of `org-gtd-cli/daemon-info' into a
    Python dict. Returns None when the daemon is a pre-#26 build (no such
    function), or when parsing fails.

    We do not eval the elisp; we scan tokens because the output shape is
    small and fixed (`(:pid N :org "..." :socket "..." :ttl N|nil ...)`)."""
    if raw is None:
        return None
    # `emacsclient --eval` wraps the result in double quotes; strip and
    # unquote once.
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        # Peel the outer emacsclient quoting: it printed `prin1-to-string''s
        # output as an elisp string, which itself contains an elisp form.
        inner = _unescape_prin1(raw[1:-1])
    else:
        inner = raw
    inner = inner.strip()
    if not inner.startswith("(") or not inner.endswith(")"):
        return None
    inner = inner[1:-1]
    # Tokenise: keys are `:kw' symbols; values are integers, `nil', or
    # double-quoted strings. This matches exactly the shape of
    # `org-gtd-cli/daemon-info' and is the only expected input.
    tokens = []
    i = 0
    while i < len(inner):
        c = inner[i]
        if c.isspace():
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < len(inner):
                if inner[j] == "\\" and j + 1 < len(inner):
                    buf.append(inner[j:j + 2])
                    j += 2
                elif inner[j] == '"':
                    break
                else:
                    buf.append(inner[j])
                    j += 1
            if j >= len(inner):
                return None
            tokens.append(("str", _unescape_prin1("".join(buf))))
            i = j + 1
        else:
            j = i
            while j < len(inner) and not inner[j].isspace():
                j += 1
            tokens.append(("atom", inner[i:j]))
            i = j
    info = {}
    idx = 0
    while idx < len(tokens) - 1:
        ktype, key = tokens[idx]
        vtype, val = tokens[idx + 1]
        if ktype != "atom" or not key.startswith(":"):
            return None
        name = key[1:]
        if vtype == "str":
            info[name] = val
        elif val == "nil":
            info[name] = None
        else:
            try:
                info[name] = int(val)
            except ValueError:
                try:
                    info[name] = float(val)
                except ValueError:
                    info[name] = val
        idx += 2
    return info


def _stop_daemon_via_socket(socket_path, timeout=5):
    """Ask a live daemon to `kill-emacs' via its own socket, bounded.

    Returns (pid_before_or_None, ok, error). `ok` is True when the daemon
    was already gone or has exited within TIMEOUT."""
    info, probe_err = _probe_daemon(socket_path, timeout=min(3, timeout))
    if info is None and probe_err in ("socket-missing", "stale-socket"):
        return None, True, None
    if info is None:
        # Probe failed for some other reason (foreign, timeout). Do not
        # signal any PID — that could hit a reused PID.
        return None, False, probe_err or "unknown-probe-failure"
    pid = info.get("pid") if isinstance(info.get("pid"), int) else None
    try:
        result = subprocess.run(
            [EMACSCLIENT_BIN, "--socket-name", socket_path,
             "--eval", "(kill-emacs 0)"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return pid, False, "stop-timeout"
    # Wait for the socket to disappear (a good proxy for actual exit,
    # bounded so we can't hang here forever).
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.stat(socket_path)
        except FileNotFoundError:
            return pid, True, None
        except OSError:
            break
        time.sleep(0.05)
    # Emacs sometimes stays around briefly with the socket already unlinked;
    # if `result` succeeded, treat it as stopped.
    if result.returncode == 0:
        return pid, True, None
    return pid, False, f"stop-exit-{result.returncode}"


def _resolved_beneath(candidate, root):
    """True iff CANDIDATE resolves strictly beneath ROOT after realpath.
    Guards against symlink escape when removing an identity directory."""
    try:
        cand_real = os.path.realpath(candidate)
        root_real = os.path.realpath(root)
    except OSError:
        return False
    if not cand_real.startswith(root_real + os.sep):
        return False
    rel = os.path.relpath(cand_real, root_real)
    return rel not in (".", "") and not rel.startswith("..")


def _safe_remove_identity_dir(dir_path, identity):
    """Recursively remove an owned identity directory, with belt-and-braces.

    Rejects paths that escape `_DAEMON_BASE', foreign-owned directories, or
    an identity component that does not match the sha-256 hash pattern.
    Returns (ok, error_message)."""
    if not _IDENTITY_HASH_RE.match(identity):
        return False, f"malformed identity {identity!r}; refusing to remove"
    if not _resolved_beneath(dir_path, _DAEMON_BASE):
        return False, (f"resolved path {os.path.realpath(dir_path)!r} escapes "
                       f"{_DAEMON_BASE!r}; refusing to remove")
    try:
        st = os.lstat(dir_path)
    except FileNotFoundError:
        return True, None
    except OSError as exc:
        return False, f"stat failed: {exc}"
    if st.st_uid != _current_uid():
        return False, (f"foreign-owned dir (uid {st.st_uid}); refusing to "
                       "remove")
    # Spec: verify every DESCENDANT's owner too, not just the top-level
    # dir, before the recursive delete. `lstat` so a planted symlink is
    # judged by itself, never by its target.
    for root, dirs, files in os.walk(dir_path):
        for name in dirs + files:
            p = os.path.join(root, name)
            try:
                if os.lstat(p).st_uid != _current_uid():
                    return False, (f"foreign-owned entry {p!r}; refusing "
                                   "to remove")
            except FileNotFoundError:
                continue
            except OSError as exc:
                return False, f"stat failed: {exc}"
    try:
        shutil.rmtree(dir_path)
    except OSError as exc:
        return False, f"rmtree failed: {exc}"
    return True, None


def _relative_socket(socket_path):
    """Absolute socket path — spec requires this shape verbatim."""
    return socket_path


def _canonical_org_directory(path):
    """Return a canonical form of a reported ORG_DIRECTORY string.
    Trailing slashes are normalised; missing files are still fine (an
    identity's org dir may have been deleted — that's the gc target)."""
    if not path:
        return ""
    canonical = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    return canonical


def _age_seconds(info):
    """Compute a non-negative age from `daemon-info' plist fields."""
    now = info.get("now") if info else None
    ince = info.get("inception") if info else None
    if isinstance(now, (int, float)) and isinstance(ince, (int, float)):
        age = max(0, int(round(now - ince)))
        return age
    # Fallback for a pre-#26 daemon: age from the socket file's ctime
    # (created by _ensure_daemon; is close enough for observability).
    return None


def _make_daemon_record(identity, socket_path, info):
    return {
        "identity": identity,
        "socket": _relative_socket(socket_path),
        "org_directory": _canonical_org_directory(
            (info or {}).get("org", "")),
        "pid": (info or {}).get("pid"),
        "age_seconds": _age_seconds(info),
        "ttl": (info or {}).get("ttl"),
    }


def _emit_daemon_json(payload, json_mode, exit_code):
    payload["version"] = 1
    if json_mode:
        print(json.dumps(payload))
    return exit_code


def cmd_daemon_status(args):
    """Enumerate live daemons owned by this UID under `_DAEMON_BASE'."""
    if not _daemon_base_ok():
        msg = (f"daemon base {_DAEMON_BASE!r} is owned by another user; "
               "refusing to inspect")
        if args.json:
            print(json.dumps({"version": 1, "command": "daemon status",
                              "daemons": [], "errors": [{"error": msg}]}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1
    candidates, errors = _list_identity_dirs()
    daemons = []
    for identity, dir_path in candidates:
        socket_path = _socket_for_identity_dir(dir_path)
        info, probe_err = _probe_daemon(socket_path)
        if info is None:
            # A quiet dead identity (no live daemon) is not a status error —
            # it just shouldn't appear in the list. `gc` cleans these up.
            # A missing socket AND a leftover socket nobody listens on
            # (unclean death, e.g. SIGKILL) are both normal; probe-timeout /
            # foreign socket / other are surfaced as errors.
            if probe_err and probe_err not in ("socket-missing",
                                               "stale-socket"):
                errors.append({"identity": identity, "socket": socket_path,
                               "error": probe_err})
            continue
        daemons.append(_make_daemon_record(identity, socket_path, info))
    daemons.sort(key=lambda d: (d["identity"], d["socket"]))
    exit_code = 1 if errors else 0
    if args.json:
        print(json.dumps({"version": 1, "command": "daemon status",
                          "daemons": daemons, "errors": errors}))
    else:
        if not daemons and not errors:
            print("No live daemons for this UID.")
        for d in daemons:
            age = d["age_seconds"] if d["age_seconds"] is not None else "?"
            ttl = d["ttl"] if d["ttl"] is not None else "?"
            print(f'{d["identity"]}  pid={d["pid"]}  age={age}s  ttl={ttl}s'
                  f'  org={d["org_directory"]}  socket={d["socket"]}')
        for e in errors:
            key = e.get("identity") or e.get("path") or "?"
            print(f'error: {key}: {e["error"]}', file=sys.stderr)
    return exit_code


def cmd_daemon_stop(args):
    """Stop only the current identity's daemon. Idempotent."""
    identity = _DAEMON_IDENTITY
    socket_path = SOCKET_PATH
    if not _daemon_base_ok():
        msg = (f"daemon base {_DAEMON_BASE!r} is owned by another user; "
               "refusing to act")
        if args.json:
            print(json.dumps({"version": 1, "command": "daemon stop",
                              "identity": identity, "stopped": False,
                              "pid": None, "error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1
    pid, ok, err = _stop_daemon_via_socket(socket_path)
    stopped = ok and (pid is not None)
    # Remove the identity dir on any successful stop. If the daemon was
    # already absent (`pid is None and ok`), also remove any stale dir that
    # remains — being idempotent is spec-required.
    removed_err = None
    dir_path = _SOCKET_DIR
    if ok and os.path.isdir(dir_path):
        _, removed_err = _safe_remove_identity_dir(dir_path, identity)
    payload = {"version": 1, "command": "daemon stop",
               "identity": identity, "stopped": bool(stopped), "pid": pid}
    if err:
        payload["error"] = err
    if removed_err:
        payload["error"] = removed_err
    exit_code = 1 if (err or removed_err) else 0
    if args.json:
        print(json.dumps(payload))
    else:
        if err:
            print(f"Error: {err}", file=sys.stderr)
        elif stopped:
            print(f"Stopped daemon for identity {identity} (pid {pid}).")
        else:
            print(f"No daemon running for identity {identity}.")
    return exit_code


def cmd_daemon_gc(args):
    """Reap daemons whose ORG_DIRECTORY is gone and clean stale owned dirs."""
    if not _daemon_base_ok():
        msg = (f"daemon base {_DAEMON_BASE!r} is owned by another user; "
               "refusing to act")
        payload = {"version": 1, "command": "daemon gc",
                   "reaped": [], "kept": [], "stale_dirs_removed": [],
                   "errors": [{"error": msg}]}
        if args.json:
            print(json.dumps(payload))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1
    candidates, errors = _list_identity_dirs()
    reaped = []
    kept = []
    stale_dirs_removed = []
    for identity, dir_path in candidates:
        socket_path = _socket_for_identity_dir(dir_path)
        info, probe_err = _probe_daemon(socket_path)
        if info is None:
            # No live daemon here. If the socket file is entirely absent or
            # nothing answers behind it, this is a stale owned identity dir
            # — remove it. If the socket exists but the probe failed some
            # other way (foreign socket, wedged/timeout), report as an
            # error instead of stomping.
            if probe_err in ("socket-missing", "stale-socket"):
                ok, err = _safe_remove_identity_dir(dir_path, identity)
                if ok:
                    stale_dirs_removed.append(dir_path)
                else:
                    errors.append({"identity": identity, "path": dir_path,
                                   "error": err})
            else:
                errors.append({"identity": identity, "socket": socket_path,
                               "error": (probe_err or "unresponsive")})
            continue
        org = _canonical_org_directory(info.get("org", ""))
        record = _make_daemon_record(identity, socket_path, info)
        if info.get("legacy"):
            # Live pre-#26 daemon: it answered `(emacs-pid)' but has no
            # `daemon-info', so its ORG_DIRECTORY is unknown. We cannot
            # prove the org dir is gone — keep it running (spec: legacy
            # daemons stay visible and are never reaped by gc).
            kept.append(record)
            continue
        if org and os.path.isdir(org):
            kept.append(record)
            continue
        # Reap: ORG_DIRECTORY no longer exists.
        _, ok, stop_err = _stop_daemon_via_socket(socket_path)
        if not ok:
            errors.append({"identity": identity, "socket": socket_path,
                           "error": stop_err or "stop-failed"})
            continue
        remove_ok, remove_err = _safe_remove_identity_dir(dir_path, identity)
        if not remove_ok:
            errors.append({"identity": identity, "path": dir_path,
                           "error": remove_err})
            continue
        reaped.append(record)
    reaped.sort(key=lambda d: (d["identity"], d["socket"]))
    kept.sort(key=lambda d: (d["identity"], d["socket"]))
    stale_dirs_removed.sort()
    exit_code = 1 if errors else 0
    if args.json:
        print(json.dumps({"version": 1, "command": "daemon gc",
                          "reaped": reaped, "kept": kept,
                          "stale_dirs_removed": stale_dirs_removed,
                          "errors": errors}))
    else:
        if not reaped and not stale_dirs_removed and not errors:
            print("Nothing to reap.")
        for r in reaped:
            print(f'reaped: {r["identity"]} (pid {r["pid"]}, org gone: '
                  f'{r["org_directory"]})')
        for d in stale_dirs_removed:
            print(f"removed stale dir: {d}")
        for k in kept:
            print(f'kept: {k["identity"]}  org={k["org_directory"]}')
        for e in errors:
            key = e.get("identity") or e.get("path") or "?"
            print(f'error: {key}: {e["error"]}', file=sys.stderr)
    return exit_code


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
        # --json error objects go to stdout (the --json contract: stdout
        # carries exactly one JSON object, stderr only opaque diagnostics).
        print('{"error": "--json is not supported for org-timestamp"}')
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


def cmd_render_file(args):
    expr = f'(org-gtd-cli/render-file {to_elisp(args.path)})'
    return run_elisp(expr, json_mode=args.json)


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
    if args.time and not (args.schedule or args.deadline):
        print("Error: --time requires --schedule or --deadline", file=sys.stderr)
        return 1
    if args.time and args.schedule and args.deadline:
        print("Error: --time with both --schedule and --deadline is ambiguous; "
              "set the second timestamp's time via set-schedule or set-deadline.",
              file=sys.stderr)
        return 1
    raw_body = resolve_body_text(args.body, args.body_file)
    if args.body == "-" and raw_body is None:
        return 1  # resolve_body_text already printed error
    body = unescape_body_newlines(raw_body) if raw_body else raw_body
    expr = (f'(org-gtd-cli/add-task {to_elisp(title)} {to_elisp(body)} '
            f'{to_elisp(args.tags)} {to_elisp(args.schedule)} '
            f'{to_elisp(args.deadline)} {to_elisp(args.priority)} '
            f'{to_elisp(args.file)} {to_elisp(args.category)} '
            f'{to_elisp(args.state)} {to_elisp(args.time)})')
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
    # add-note writes only a structured skeleton (title + empty sections); it
    # has no body channel. Silently dropping a piped body loses data, so reject
    # it loudly instead of ignoring it. Only read when stdin is an actual
    # pipe/redirect (not a TTY); empty/closed stdin reads as "" and is fine.
    if not sys.stdin.isatty():
        piped = sys.stdin.read()
        if piped.strip():
            msg = ("add-note does not accept a body on stdin — it writes only "
                   "a title + section skeleton, so the piped content would be "
                   "lost. The note was NOT created. Remove the pipe, then edit "
                   "the created note file directly to add content.")
            if args.json:
                print(json.dumps({"error": msg}))
            else:
                print(f"Error: {msg}", file=sys.stderr)
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
    # --body (explicit flag) wins over the positional TEXT when both are given.
    body_text = args.body if args.body is not None else args.text
    text = resolve_body_text(body_text, args.body_file, auto_stdin=True)
    if body_text == "-" and text is None:
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
    # --body (explicit flag) wins over the positional TEXT when both are given.
    body_text = args.body if args.body is not None else args.text
    text = resolve_body_text(body_text, args.body_file, auto_stdin=True)
    if body_text == "-" and text is None:
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
            f'{to_elisp("t" if args.dry_run else None)} '
            f'{to_elisp(args.reason)})')
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
    if not validate_target(args):
        return 1
    expr = (f'(org-gtd-cli/set-cancelled {to_elisp(args.substr)} '
            f'{to_elisp(args.index)} '
            f'{to_elisp("t" if args.dry_run else None)})')
    expr = id_wrap(expr, args, mutation=True)
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


def cmd_remove_tags(args):
    if not validate_target(args):
        return 1
    if args.tags is None:
        print("Error: --tags is required", file=sys.stderr)
        return 1
    expr = (f'(org-gtd-cli/remove-tags {to_elisp(args.substr)} '
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

class _AddTaskParentAction(argparse.Action):
    """add-task has no --parent; catch the common mistake and point at add-subtask."""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error("add-task has no --parent; to add a child under an "
                     "existing task, use: add-subtask PARENT_SUBSTR TITLE")


def build_parser() -> argparse.ArgumentParser:
    epilog = """\
Querying:
  show              Show full task details
  search            Find tasks by heading substring
  agenda            List tasks with state/tag/date filters
  agenda-view       Run a pre-built agenda view
  subtasks          List children of a project
  categories        Show category tree for refile targets
  render-file       Render a view-only .org file (agent-notes) to HTML
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
  daemon            Manage the org-gtd-cli Emacs daemon
                    (subcommands: status, stop, gc)

Environment:
  ORG_DIRECTORY             Path to org files (default: ~/org/)
  ORG_GTD_CLI_DAEMON        Set to 1 to reuse a per-identity Emacs daemon
  ORG_GTD_CLI_DAEMON_TTL    Idle TTL in seconds (default 7200; 0=immortal)

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
                   help="Heading substring to match "
                        "(optional when --tag or --state is provided)")
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

    p = sub.add_parser(
        "render-file",
        help="Render a view-only .org file (agent-notes) to body-only HTML")
    p.add_argument("path", metavar="PATH",
                   help="Path to a .org file, relative to ORG_DIRECTORY "
                        "(absolute allowed only if it resolves inside it)")
    p.set_defaults(func=cmd_render_file)

    p = sub.add_parser("outline", help="Full nested outline of an org file")
    p.add_argument("--file", help="Target file (default: tasks.org)")
    p.add_argument("--full", action="store_true",
                   help="Include each node's raw org body (--json only)")
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
    p.add_argument("--time", help="Time for scheduled/deadline date (HH:MM)")
    p.add_argument("--priority", help="Priority: A, B, or C")
    p.add_argument("--file", help="Target file (relative to ORG_DIRECTORY)")
    p.add_argument("--category", help="Insert under this heading in tasks.org")
    p.add_argument("--state", help="Initial state (default: TODO)")
    p.add_argument("--parent", nargs="?", action=_AddTaskParentAction,
                   default=argparse.SUPPRESS, help=argparse.SUPPRESS)
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
    p.add_argument("--reason", help="Record a LOGBOOK state-change note")
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
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
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
    tag_group.add_argument(
        "--tags", help="Tags to set (comma-separated, empty string to clear)"
    )
    tag_group.add_argument("--add", help="Tags to add (comma-separated)")
    tag_group.add_argument("--remove", help="Tags to remove (comma-separated)")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_set_tags)

    p = sub.add_parser("add-tags", help="Append tags (no duplicates)")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring (optional with --batch)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument(
        "--tags", help="Tags to add (comma-separated, optional with --batch)"
    )
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_add_tags)

    p = sub.add_parser("remove-tags", help="Remove specific tags (no-op if absent)")
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring (optional with --batch)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument(
        "--tags", help="Tags to remove (comma-separated, optional with --batch)"
    )
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    p.set_defaults(func=cmd_remove_tags)

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

    # allow_abbrev=False so --body cannot silently prefix-match --body-file
    # (an old trap that turned `--body TEXT` into a bogus filename).
    p = sub.add_parser("append-body", help="Append text to task body",
                       allow_abbrev=False)
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("text", nargs="?", default=None, metavar="TEXT",
                   help="Text to append (optional when --body-file is used)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--body", default=None,
                   help="Body text (alternative to positional TEXT)")
    p.add_argument("--body-file", dest="body_file",
                   help="Read text from FILE (use - for stdin)")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_append_body)

    p = sub.add_parser("set-body", help="Replace task body",
                       allow_abbrev=False)
    p.add_argument("substr", nargs="?", default=None, metavar="SUBSTR",
                   help="Heading substring")
    p.add_argument("text", nargs="?", default=None, metavar="TEXT",
                   help="New body text (optional when --body-file is used)")
    p.add_argument("--id", dest="task_id", help="Resolve the task by its org :ID:")
    p.add_argument("--body", default=None,
                   help="Body text (alternative to positional TEXT)")
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

    p = sub.add_parser(
        "get-session-ids", help="Get agent session IDs from task LOGBOOK"
    )
    p.add_argument("substr", metavar="SUBSTR", help="Heading substring")
    p.add_argument("--index", help="Disambiguate with 1-based index")
    p.set_defaults(func=cmd_get_session_ids)

    # --- Daemon lifecycle management ---
    #
    # `daemon status/stop/gc' run regardless of ORG_GTD_CLI_DAEMON, never call
    # `_ensure_daemon' and never create a daemon. They are also intentionally
    # NOT exposed through homogeneous or mixed batch modes (per issue #26 spec).

    p = sub.add_parser(
        "daemon",
        help="Manage the org-gtd-cli Emacs daemon (status/stop/gc)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
Manage the org-gtd-cli Emacs daemon.

All subcommands run regardless of ORG_GTD_CLI_DAEMON and never spawn a
daemon; they only inspect existing ones for this UID. The default idle
TTL is 7200s and is configurable via ORG_GTD_CLI_DAEMON_TTL:
  ORG_GTD_CLI_DAEMON_TTL=<positive seconds>   idle TTL in seconds
  ORG_GTD_CLI_DAEMON_TTL=0                    immortal (no timer)
  ORG_GTD_CLI_DAEMON_TTL unset or empty       default 7200s
  negative / non-integer values                error; no daemon start

Subcommands:
  status  List live daemons owned by this UID with identity, socket,
          org directory, PID, age, and TTL.
  stop    Stop only the current identity's daemon (idempotent).
  gc      Stop daemons whose ORG_DIRECTORY no longer exists, remove
          owned stale identity directories that have no live daemon,
          and leave every daemon with a live org directory running.
""")
    daemon_sub = p.add_subparsers(dest="daemon_command")
    q = daemon_sub.add_parser("status", help="List live daemons for this UID")
    q.set_defaults(func=cmd_daemon_status)
    q = daemon_sub.add_parser("stop", help="Stop the current identity's daemon")
    q.set_defaults(func=cmd_daemon_stop)
    q = daemon_sub.add_parser(
        "gc",
        help="Reap daemons whose ORG_DIRECTORY is gone; clean owned stale dirs")
    q.set_defaults(func=cmd_daemon_gc)
    # Fallback: `daemon` with no subcommand prints help and exits 1.
    p.set_defaults(func=lambda args: (p.print_help(sys.stderr) or 1))

    # --- Batch ---

    p = sub.add_parser(
        "batch",
        help="Run many commands in one call (JSON array on stdin)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
Run several commands in one Emacs process.

stdin: JSON array of {"command": NAME, "args": {...}} objects.

Mutations:  add-task, add-subtask, add-event, add-session-id, set-done,
            set-state, set-next, set-cancelled, set-priority, rename,
            move, set-schedule, set-deadline, set-tags, add-tags,
            remove-tags, set-body, append-body, set-property, refile, delete
Reads:      show, agenda-view, outline, categories
            (render-file is not batch-covered: a path-taking read with
            no per-task item — call it standalone)

Args use the same field names as --batch items. Task-addressing commands
take `heading` (substring) OR `id` (org :ID:, matching each command's
--id flag; `id` wins when both are given):
  add-task        title (required), body, tags, schedule, deadline, time,
                  priority, file, category, state
  add-subtask     parent OR parent_id (required), title (required), body,
                  tags, schedule, deadline, priority, state
  add-event       title (required), date (required), time, tag, file,
                  end_date
  refile          heading|id (required), category OR to (required)
  set-done        heading|id (required)
  set-state       heading|id (required), state (required)
  set-next        heading|id (required)
  set-cancelled   heading|id (required)
  set-priority    heading|id (required), priority, clear
  rename          heading|id (required), title (required)
  move            heading|id (required), direction (required), sibling
  set-schedule    heading|id (required), date, time, clear
  set-deadline    heading|id (required), date, time, clear
  set-body        heading|id (required), text (required)
  append-body     heading|id (required), text (required)
  set-property    heading|id (required), key (required), value, clear
  set-tags        heading|id (required), tags
  add-tags        heading|id (required), tags (required)
  remove-tags     heading|id (required), tags (required)
  add-session-id  heading|id (required), session_id (required)
  delete          heading|id (required)
  show            heading|id (required)
  agenda-view     key, date
  outline         file, full
  categories      file

Output: JSON with one result per input item, in order, plus a summary
(same shape as --batch <subcommand>). A failing item does not abort
the rest. Exit 0 if at least one item succeeded, 1 otherwise.

Reads (agenda-view, outline, categories) are available only here, not in
the homogeneous `--batch <subcommand>` form, so one call can pair a
mutation with a recomputed view.

Example:
  echo '[{"command": "set-done", "args": {"id": "f95d…"}},
         {"command": "agenda-view", "args": {}}]' \\
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


# Commands allowed in homogeneous `--batch <subcommand>` mode. Task-addressing
# items may carry an `id` field (org :ID:) instead of `heading`, matching each
# command's `--id` flag. Read commands (agenda-view, outline, categories) are
# intentionally absent here — they have no per-task item, so they are exposed
# only through the heterogeneous `batch` subcommand.
BATCH_COMMANDS = {
    "add-event", "add-session-id", "add-subtask", "add-task", "delete",
    "refile", "set-done", "show", "set-tags", "add-tags", "remove-tags",
    "set-state", "set-next", "set-cancelled", "set-priority", "rename",
    "move", "set-schedule", "set-deadline", "set-property",
    "set-body", "append-body",
}


def batch_input_error(msg: str, json_mode: bool) -> int:
    """Print a batch input error.

    In --json mode the error object goes to STDOUT (the --json contract:
    stdout carries exactly one JSON object, stderr only opaque diagnostics);
    in text mode it goes to stderr.

    Returns 1 so callers can `return batch_input_error(...)`.
    """
    if json_mode:
        print(json.dumps({"error": msg}))
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

    def _has(it, *keys):
        return isinstance(it, dict) and any(it.get(k) for k in keys)

    # Shared args for commands that need them
    shared_arg = None
    if command == "add-subtask":
        shared_arg = getattr(args, 'parent', None)
        parent_id = getattr(args, 'task_id', None)
        # A shared --id addresses the parent by :ID: for every item; thread it
        # in as a per-item fallback so it reaches org-gtd-cli/batch-one (an
        # item's own parent_id still wins).
        if parent_id:
            for it in items:
                if isinstance(it, dict) and not _has(it, "parent_id", "parent-id"):
                    it["parent_id"] = parent_id
        if not shared_arg and not parent_id and not all(
                _has(it, "parent_id", "parent-id") for it in items):
            print(
                "Error: --batch add-subtask requires a parent SUBSTR "
                "positional, --id, or a parent_id on every item",
                file=sys.stderr,
            )
            return 1
    elif command == "refile":
        shared_arg = getattr(args, 'category', None)
        to_target = getattr(args, 'to', None)
        # A shared --to target applies to every item (per-item `to` wins).
        if to_target:
            for it in items:
                if isinstance(it, dict) and not _has(it, "to"):
                    it["to"] = to_target
        if not shared_arg and not to_target and not all(
                _has(it, "to") for it in items):
            print(
                "Error: --batch refile requires --category, --to, or a "
                "`to` target on every item",
                file=sys.stderr,
            )
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

    # `daemon status/gc` never spawn or dispatch to Emacs — they only inspect
    # existing sockets — so they run even when the elisp paths are not set.
    # `daemon stop` derives its identity from ORG_GTD_{CORE,ELISP}_FILE, so it
    # DOES need them (a missing path becomes a mismatched identity, i.e. a
    # different socket root, which would silently no-op).
    daemon_command = getattr(args, "daemon_command", None)
    is_daemon_readonly = (args.command == "daemon"
                          and daemon_command in ("status", "gc"))
    if not is_daemon_readonly and (not CORE_FILE or not ELISP_FILE):
        print("Error: ORG_GTD_CORE_FILE and ORG_GTD_ELISP_FILE must be set",
              file=sys.stderr)
        sys.exit(1)

    # Handle --batch mode (the `batch` subcommand dispatches normally;
    # a redundant --batch flag on it is ignored). `daemon` is intentionally
    # not exposed through homogeneous batch — its subcommands run once per
    # invocation and manage global state, not per-item work.
    if args.batch and args.command != "batch":
        rc = cmd_batch(args)
        sys.exit(rc)

    rc = args.func(args)
    sys.exit(rc)


if __name__ == "__main__":
    main()
