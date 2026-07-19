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
| `outline` | Full nested outline of a file as JSON; nodes are typed (`is_category` / `is_event` / `is_project`) and calendar events carry their `timestamp`; `--full` adds each node's raw org `body` |
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
`read_id_kind` before relying on cross-edit stability.

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
