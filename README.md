# org-gtd-cli

A command-line interface for managing an [org-mode](https://orgmode.org/)
GTD ("Getting Things Done") system. Designed to be driven by humans and
coding agents alike: every command has a `--json` mode for scripting.

It's a thin Python dispatch layer (`org-gtd-cli.py`) over Emacs running in
batch mode — all org logic lives in Emacs Lisp (`org-gtd-cli.el`), sharing
its core (TODO keywords, state machine, project detection) with an
interactive Doom Emacs config via `+gtd-core.el`.

## Requirements

- Emacs (with org-mode) on `PATH`
- Python 3
- An org directory (default `~/org/`, override with `ORG_DIRECTORY`)
  containing `inbox.org`, `tasks.org`, and `calendar.org`.

## Install

With Nix (flake provided):

```sh
nix run github:luxbock/nixos-config?dir=pkgs/org-gtd-cli -- agenda
# or add to a devShell:
#   org-gtd-cli.packages.${system}.default
```

Or run the script directly, pointing it at the elisp:

```sh
ORG_GTD_CORE_FILE=+gtd-core.el ORG_GTD_ELISP_FILE=org-gtd-cli.el \
  ./org-gtd-cli.py agenda
```

## Workflow

Tasks move through a two-track state machine:

```
TODO → NEXT → DONE
WAITING → DEFER → CANCELLED
```

## Usage

```sh
org-gtd-cli [--json] <command> [options]
```

Common commands:

| Command | What it does |
|---------|--------------|
| `agenda` / `agenda-view` | List tasks by state/tag/date, or run a pre-built view |
| `search` / `show` | Find tasks by heading substring; show full detail |
| `add-task` / `add-subtask` / `add-event` | Capture into inbox / under a parent / calendar |
| `set-state` / `set-next` / `set-done` / `set-cancelled` | Move a task through the state machine |
| `set-schedule` / `set-deadline` / `set-priority` | Set timestamps and priority |
| `set-tags` / `add-tags` / `remove-tags` / `refile` / `move` / `rename` | Organize and edit |
| `projects` / `subtasks` / `categories` / `list-tags` | Inspect structure |
| `outline` | Full nested outline of a file as JSON; nodes are typed (`is_category` / `is_event` / `is_project`), calendar events carry their `timestamp`, and every node exposes its planning line as `scheduled` / `deadline` (raw org timestamp strings, `null` when absent — same representation as `agenda-view` task rows); `--full` adds each node's raw org `body` |
| `render-file` | Render a view-only `.org` doc to body-only HTML (see below) |

`add-task` has no `--parent` flag — to add a child under an existing task or
project, use `add-subtask PARENT_SUBSTR TITLE`.

Run `org-gtd-cli <command> -h` for per-command options, or `org-gtd-cli -h`
for the full list.

### Category headings are first-class in `show` and `subtasks`

Plain (no-TODO) organizational headings — the ones `categories` lists — are
directly addressable by `show` and `subtasks`. If the argument exactly matches
one such leaf name (case-insensitive; `Parent/Leaf` disambiguates between
namesakes like `Computers/Tools` and `Research/Tools`), the command returns
the category's metadata and its direct children. Otherwise resolution falls
through to the normal substring lookup over TODO-keyword entries, so existing
task addressing and ambiguity handling are unchanged.

Under `--json` the two shapes are distinguished by a `kind` field:

- `kind: "task"` — the existing task envelope for TODO/NEXT/WAITING/DEFER/DONE/
  CANCELLED entries (`show`: full body + sessions + subtasks; `subtasks`: the
  parent's state + direct children).
- `kind: "category"` — a subtasks-shaped envelope: `heading`, `path`,
  `parent`, `file`, `id`, `tags`, `progress` (`{done, total}` over children
  carrying a TODO keyword — `null` when no children have one), and `subtasks`
  (direct children, each with `heading`/`state`/`priority`/`tags`/`id`/
  `scheduled`/`deadline`/`is_project`). `subtasks --full <category>` also
  emits each child's `body`.

Multiple category leaves sharing a name (e.g. `Tools` under both `Computers`
and `Research`) yield a deterministic multi-match error listing the paths;
retry with `Parent/Leaf` to select one.

### Stable read identity (`read_id`) — joining `outline` and `agenda-view`

Both `outline` (per node) and `agenda-view` (per task row) emit a `read_id`
and a `read_id_kind`, a **non-mutating** join key so a consumer (e.g. a
dashboard) can correlate the two reads for the same source heading — including
id-less, duplicate calendar headings that carry no Org `:ID:`. Reads never
write identities into files. The value is chosen from the first tier that
applies:

| `read_id_kind` | `read_id` value | Stability |
|----------------|-----------------|-----------|
| `org-id`   | the heading's own Org `:ID:` (equals the `id` field) | authoritative; stable across any edit/reordering |
| `entry-id` | the org-gcal `:entry-id:` (a Google Calendar event id) | stable across any edit/reordering |
| `locator`  | `loc:<digest>` over `(file, outline-path, occurrence-index)` | stable across repeated reads and edits that do not rename the heading, move it under a different parent, or add/remove an earlier same-path duplicate |

`read_id` is always present (never null). Duplicate id-less headings receive
distinct `locator` identities via their occurrence index, and the same source
heading produces the same `read_id` from both commands. Prefer the `org-id`
and `entry-id` tiers; treat `locator` as best-effort and consult
`read_id_kind` before relying on cross-edit stability. When two or more
headings fall into this `locator` tier and share the same bare heading text in
one file, `outline`/`agenda-view` flag them via the `warnings` array below, so
you can add an `:ID:`/`:entry-id:` or dedupe.

### `warnings` — the universal environmental-warning channel

`warnings` is a **universal** top-level envelope field: it is allowed on **every**
`--json` command — reads *and* mutations alike (the moment "your view may be
stale" matters most is right before a mutation lands). It carries typed,
exit-neutral facts about **pre-existing or environmental state observed while
running** — never state changes *caused* by a mutation (those are
`side_effects`). Entries are typed objects `{"type": "<kebab-slug>", ...}`; the
`type` string is stable contract surface. In text mode each entry is mirrored as
one `Warning: ...` line on **stderr**. The array is **absent/empty when there is
nothing to warn about**, so the clean path is byte-identical to a CLI without
this channel. Multiple sources compose into **one** `warnings` array on a given
envelope (e.g. an `outline` over a file with id-less duplicates *and* a pending
sync conflict carries both entries — the duplicate entries first, the
sync-conflict entry last).

Current vocabulary:

- `duplicate-idless-heading` (on `outline` / `agenda-view`) — see below.
- `sync-conflict` (on **all** commands) — see below.

#### `duplicate-idless-heading` — id-less duplicates on `outline` / `agenda-view`

`outline` and `agenda-view` emit a
top-level `warnings` array in their `--json` envelope. It surfaces id-less
headings that are *duplicated*: two or more headings sharing the same bare
heading text within one file, where each resolves to the `locator` read-id
tier (i.e. carries **neither** an Org `:ID:` **nor** an org-gcal `:entry-id:` —
either property suppresses the warning). Such headings are disambiguated only
by position, so a consumer joining on `(file, heading)` cannot tell them apart;
the warning tells the user to add an id or dedupe.

Each entry is a typed object:

```json
{"type": "duplicate-idless-heading", "file": "family-calendar.org", "heading": "Recurring Chore", "count": 2}
```

- **One entry per duplicated `(file, heading)` group, not per occurrence** —
  `count` is the number of colliding id-less headings in that group, always ≥ 2.
- **Grouped by bare heading text** (`org-get-heading`, the same value emitted as
  the node/row `heading` field) — *not* by the full outline path that the
  `locator` digest uses. Two same-named category headings under different
  parents (e.g. `Computers/Tools` and `Research/Tools`) therefore warn.
- `agenda-view` computes over the set of files that contributed task rows to the
  view (deduped by file); each entry's `file` names the file the duplicate lives
  in.
- **Always present** as a key, and equal to `[]` when there is nothing to
  report — no warning noise on the clean path.
- **Warn-only and read-only.** A duplicate never changes the exit code (a clean
  read still exits 0 with warnings present), and the diagnostic never creates an
  `:ID:`/`:entry-id:` — the source file stays byte-identical.

In **text mode**, each duplicated group is mirrored as one `Warning: ...` line
on **stderr**; stdout carries only the normal output (the indented outline tree
/ the agenda listing), with no `Warning:` line and no JSON.

The `type` string is stable contract surface. This warning is computed per
command and is confined to the two read commands, but it shares the universal
`warnings` array with any other applicable warning (e.g. `sync-conflict`).

#### `sync-conflict` — pending org-sync conflict (all commands)

`~/org` is synced across machines by an external git-based sync unit
(nixos-config `home/common/services/org-sync.nix`). When a 3-way merge genuinely
conflicts, that unit parks the local line on a `<host>-<user>-conflict` branch
and drops a marker file **`${ORG_DIRECTORY}/.sync-conflict`** at the org-dir
root (latched cross-host while any `*-conflict` branch exists on the forge). It
means the local clone may have diverged from the other machines' view.

Every `org-gtd-cli` invocation checks for that marker by a **plain file-existence
test** (no git, no parsing) and, when present, surfaces a warning:

```json
{"type": "sync-conflict", "detail": "org sync conflict pending — local view may be stale"}
```

- **Checked once per invocation, freshly** — in both batch and daemon mode. In
  daemon mode the marker is re-read on every dispatch (`org-directory` is reset
  per call), so a marker that appears while a long-lived daemon is running is
  seen by the very next command; it is never cached at daemon start.
- **On every command that reaches Emacs** — reads, mutations, and error objects
  alike: a JSON error response (an `{"error": ...}` object) carries the
  `warnings` array too, so the staleness signal survives exactly when a lookup
  fails on a possibly stale view. The one exception: argument/stdin validation
  errors rejected by the Python dispatch layer before Emacs starts (e.g.
  malformed batch stdin) carry no warning — they never touch the org directory.
  A mutation with the marker present still executes and persists normally; the
  warning never changes an exit code and never aborts a command (warn-only).
- **The CLI is a pure consumer.** It never creates, clears, moves, or otherwise
  writes `.sync-conflict` — the external sync units own that marker entirely.
- In **text mode**, the marker surfaces as one line on **stderr** for the whole
  invocation (once, not per result item):
  `Warning: org sync conflict pending — local view may be stale/diverged`.
- **Marker absent → output is byte-identical to before**: no `sync-conflict`
  entry, no stderr line, and no `warnings` key added on its account.

### render-file — server-side org→HTML for view-only docs

```sh
org-gtd-cli --json render-file agent-notes/some-doc.org
```

Renders a rich, *view-only* `.org` file (linked `agent-notes/`, org-roam
`notes/`, …) to **body-only HTML** using Emacs's org exporter — the one correct
org renderer. Task bodies are *not* rendered this way (clients render those from
raw org); `render-file` is for the linked docs that carry tables and source
blocks. Source blocks are syntax-highlighted with `org-*` CSS face classes
(`htmlize` — see Development), so a client ships one org-face stylesheet and
needs no client-side highlighter; without htmlize it degrades to a plain `<pre>`.

**Path containment.** `render-file` is the only command that takes a
caller-supplied path. `<path>` resolves relative to `ORG_DIRECTORY` (absolute
paths are allowed only if they canonicalize inside it). After expanding and
resolving symlinks (`file-truename`) on both the path and `ORG_DIRECTORY`, it
rejects — with a structured `{error, hint}` and exit code `1`, emitting no HTML —
any path that (a) escapes `ORG_DIRECTORY`, (b) does not end in `.org`, or (c)
does not exist.

**Output** (`--json`):

```json
{ "version": 1, "command": "render-file",
  "file": "agent-notes/some-doc.org",
  "body_html": "…",
  "links": [ { "index": 0, "type": "file",
               "raw": "file:other.org::*Heading", "text": null }, … ],
  "content_hash": "sha256-…" }
```

- `body_html` — the body-only export (TOC and section numbers off).
- `content_hash` — `sha256-<hex>` over the raw source bytes, for hash-caching a
  rendered doc client-side (re-render only when the source changes).
- `links` — the link contract. `ox-html` mangles hrefs (`file:x.org` →
  `x.html`), so **a client must never route off `href`.** Every exported `<a>`
  is stamped with `data-org-link-index`, `data-org-link-type`
  (`file`/`id`/`https`/`fuzzy`/…) and `data-org-link-raw` (the *original* org
  target, with any `::*Heading` search suffix preserved). The `links` array
  enumerates the same links in document order; each entry's `index` maps to the
  matching anchor's `data-org-link-index`, so the client recovers every link's
  original org target without parsing `href`. Text mode prints the HTML only.

Whole-file rendering only (no subtree selectors). `render-file` is intentionally
**not** exposed through `batch` (it is a path-taking read with no per-task item).

### Batch mode

Run many operations in one Emacs process (avoiding per-call startup and
`emacsclient` round-trips, and executing without another writer interleaving).
Two forms, both reading a JSON array on stdin:

- `org-gtd-cli --batch <command>` — homogeneous: every item runs the same
  command. Covers all mutations (`set-state`, `set-next`, `set-cancelled`,
  `set-priority`, `set-schedule`, `set-deadline`, `set-tags`, `add-tags`,
  `remove-tags`, `rename`, `move`, `set-body`, `append-body`, `set-property`,
  `refile`, `delete`, `add-task`, `add-subtask`, `add-event`, `add-session-id`,
  `set-done`) plus `show`.
- `org-gtd-cli batch` — heterogeneous: each item is `{"command": ..., "args":
  {...}}`. Supports every command above **and** the read commands
  `agenda-view`, `outline`, and `categories`, so one call can pair a mutation
  with a recomputed view.

Each item addresses its task by `heading` (substring) or by `id` (org `:ID:`,
matching each command's `--id` flag); `id` takes precedence. A failing item
becomes a per-item error, carrying the same `hint` field single commands
return, without aborting the batch. (`render-file` is *not* batch-covered — it
takes a caller-supplied path, not a per-task item.)

A few items take fields beyond `heading`/`id`, mirroring the single commands:
`outline` accepts `full` (emit each node's raw org `body`); `refile` accepts a
`to` exact-heading target as an alternative to `category`; and `add-subtask`
accepts `parent_id` (address the parent by `:ID:`) as an alternative to
`parent`.

```sh
# A mutation plus a recomputed dashboard, atomically:
echo '[{"command":"set-done","args":{"id":"f95d…"}},
       {"command":"agenda-view","args":{}}]' | org-gtd-cli --json batch
```

## Performance

Each invocation starts Emacs. For latency-sensitive use, set
`ORG_GTD_CLI_DAEMON=1` to reuse a per-user Emacs daemon.

### Daemon saves are optimistically conflict-checked

Daemon mode holds `.org` files (and their `.org_archive` destinations) in
long-lived buffers between calls. If another writer — a batch-mode CLI
(`ORG_GTD_CLI_DAEMON=0`), a second daemon identity visiting the same file, an
editor, or an auto-commit — touches the file between this dispatch's
start-of-call revert and its save, the CLI does **not** overwrite the external
bytes. Instead every daemon save is preflighted against the on-disk state of
every tracked buffer, modified or not; on a mismatch the dispatch aborts
noninteractively with exit code `1`, discards its own unsaved changes, reloads
the affected buffers from disk, and leaves the conflicting file byte-for-byte
intact. Multi-file mutations save the *added* copy before the *removing* save
(refile writes its destination first, archive writes the archive file first),
so an abort between the two saves leaves the task duplicated — recoverable —
never deleted from both files.

`--json` conflict output is exactly one object on stdout:

```json
{ "error": "File tasks.org changed during daemon dispatch. …",
  "hint":  "Concurrent external write detected. …",
  "file":  "tasks.org",
  "exit_code": 1,
  "partial": true,
  "saved_files": ["inbox.org"] }
```

`partial=true` with a non-empty `saved_files` reports an earlier save in a
multi-file mutation (refile/archive) or a homogeneous/mixed batch that
completed before the conflict — those bytes are on disk and are **not** rolled
back. Archive destinations count: a completed `.org_archive` write appears in
`saved_files` like any `.org` save. Text mode writes the same facts to stderr. No supersession prompt or
minibuffer read is opened. The daemon stays responsive: the next call sees the
refreshed on-disk state and succeeds when no further writer races it. No
automatic retry or merge is attempted; inspect the file and retry after the
other writer stops.

### Daemon lifecycle

Opt-in daemon mode is bounded by an **idle TTL** and three management
subcommands. Daemon state is disposable and the next ordinary call
transparently recreates whatever it needs.

- **`ORG_GTD_CLI_DAEMON_TTL`** (seconds, base-10)
  - unset or empty → default `7200` (two hours)
  - `0` → immortal (no idle timer)
  - positive → self-terminate after that many idle seconds
  - negative or non-integer → error; no daemon is started
  - the timer is cancelled at every dispatch start and re-armed at the end
    (including error exits), so it can never terminate Emacs mid-command;
    each dispatch applies the caller's current TTL value, so changing the
    environment affects an already-running identity on its next call
- **`org-gtd-cli daemon status`** — list live daemons owned by this UID
  under the active socket root, each with identity hash, socket path,
  canonical `ORG_DIRECTORY`, PID, non-negative age (seconds), and current
  TTL. Deterministically ordered by identity/socket. No live daemons →
  empty successful result. `--json` returns
  `{"version":1,"command":"daemon status","daemons":[…],"errors":[…]}`.
- **`org-gtd-cli daemon stop`** — stop only the current identity's daemon
  and remove its owned identity directory. Idempotent: absent daemon
  returns `stopped=false` with exit 0. Works even when the current
  `ORG_DIRECTORY` no longer exists. `--json` returns
  `{"version":1,"command":"daemon stop","identity":…,"stopped":true|false,"pid":…}`.
- **`org-gtd-cli daemon gc`** — stop every live daemon whose reported
  `ORG_DIRECTORY` is missing, remove owned stale identity dirs with no
  live daemon, and leave every daemon with an existing org directory
  running. Works even when the caller's own `ORG_DIRECTORY` is missing.
  `--json` returns
  `{"version":1,"command":"daemon gc","reaped":[…],"kept":[…],"stale_dirs_removed":[…],"errors":[…]}`.

All three commands run regardless of `ORG_GTD_CLI_DAEMON`, never call
`_ensure_daemon()`, never create a daemon, and are not available through
homogeneous or mixed batch modes. Foreign-owned, malformed, or
path-escaping candidates are left untouched and reported as errors
(non-zero exit); safe live results still appear in `status` /
`gc.reaped` / `gc.kept`. Every `emacsclient` probe and stop is time-bounded
so a wedged daemon cannot hang the command indefinitely.

A live **pre-upgrade daemon** (started before these commands existed, so it
cannot answer the info probe) is still recognized as running via a
`(emacs-pid)` fallback probe: `status` lists it with `ttl: null` and an
empty `org_directory`, and `gc` keeps it (its org directory cannot be
proven gone). Such a daemon necessarily loaded older elisp and therefore
lives under a *different* identity hash, so `daemon stop` — which targets
only the current identity — will not reach it; it also has no idle TTL, so
nothing reaps it automatically. Stop it via the PID that `status` reports.
A socket file left behind by an uncleanly killed daemon (nothing
listening) is a quiet dead identity: `status` skips it with exit 0 and
`gc` removes its directory.

## Development

```sh
nix flake check          # runs the pytest suite
# or directly:
nix develop --command python3 -m pytest test_org_gtd_cli.py -q -n 4
```

The `render-file` src-highlighting test asserts `htmlize`'s `org-*` CSS face
classes, so it needs an Emacs with `htmlize` on its load-path (the Nix package
and `passthru.tests` provision this automatically). The default dev shell uses
the same test inputs, including Python, pytest, pytest-xdist, procps, and Emacs
with `htmlize`, so direct test runs do not need an ad-hoc `nix shell`.
Running the suite against a plain `emacs` (no htmlize) is fine — that one
assertion self-skips.

The default development shell sets `ORG_GTD_CLI_DAEMON=0`, overriding an
interactive shell that has opted into daemon mode. The pytest `run_cli()` helper
also defaults each subprocess to batch mode before applying explicit per-test
environment overrides. Daemon-specific tests opt back in with
`ORG_GTD_CLI_DAEMON=1`, give each daemon an isolated socket root, and stop it in
a `finally` block. A bounded session cleanup scoped to each xdist worker's
temporary org directories is only a last-resort safety net; it does not target
unrelated or pre-existing daemons.

### Testing an uncommitted working copy

When validating a change you have **not** yet committed, two things will
silently test the *wrong* code if you let them:

- **`nix flake check` builds committed sources only.** The flake sees
  git-tracked, committed files, so it goes green while ignoring your working-tree
  edits to `org-gtd-cli.py` / `org-gtd-cli.el` / `+gtd-core.el`. To exercise
  uncommitted changes, run `pytest` through `nix develop` (as above): the suite
  points `ORG_GTD_CORE_FILE` / `ORG_GTD_ELISP_FILE` at the checkout's own `.el`
  files.

- **Direct test runs default to `ORG_GTD_CLI_DAEMON=0`.** Daemon mode now scopes
  sockets by resolved `ORG_DIRECTORY` and loaded core/elisp file identity, so
  editing or pointing at different `.el` files selects a fresh daemon instead of
  silently reusing stale elisp. Batch mode avoids long-lived background Emacs
  state while you iterate. If you intentionally test daemon mode, use an
  isolated socket root and immediate `finally` teardown, and ensure the relevant
  identity inputs (org directory plus core/elisp paths and contents) are the
  ones you mean to exercise.

For elisp changes, byte-compile in dependency order to catch warnings the plain
source-load path misses (the `.elc` outputs are git-ignored):

```sh
emacs --batch -l org -f batch-byte-compile +gtd-core.el
emacs --batch -l ./+gtd-core.elc -f batch-byte-compile org-gtd-cli.el
```

## License

MIT
