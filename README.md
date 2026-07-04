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
| `set-tags` / `add-tags` / `refile` / `move` / `rename` | Organize and edit |
| `projects` / `subtasks` / `categories` / `list-tags` | Inspect structure |
| `outline` / `render-file` | Full nested outline; render a view-only `.org` doc to HTML |

Run `org-gtd-cli <command> -h` for per-command options, or `org-gtd-cli -h`
for the full list.

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
  `rename`, `move`, `set-body`, `append-body`, `set-property`, `refile`,
  `delete`, `add-task`, `add-subtask`, `add-event`, `add-session-id`,
  `set-done`) plus `show`.
- `org-gtd-cli batch` — heterogeneous: each item is `{"command": ..., "args":
  {...}}`. Supports every command above **and** the read commands
  `agenda-view`, `outline`, and `categories`, so one call can pair a mutation
  with a recomputed view.

Each item addresses its task by `heading` (substring) or by `id` (org `:ID:`,
matching each command's `--id` flag); `id` takes precedence. A failing item
becomes a per-item error without aborting the batch. (`render-file` is *not*
batch-covered — it takes a caller-supplied path, not a per-task item.)

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
python3 -m pytest test_org_gtd_cli.py -q -n 4
```

The `render-file` src-highlighting test asserts `htmlize`'s `org-*` CSS face
classes, so it needs an Emacs with `htmlize` on its load-path (the Nix package
and `passthru.tests` provision this automatically). Running the suite against a
plain `emacs` (no htmlize) is fine — that one assertion self-skips. To run
directly with htmlize present:

```sh
nix shell --impure --expr \
  'with import <nixpkgs> {}; [
     (python3.withPackages (ps: [ps.pytest ps.pytest-xdist]))
     (emacs-nox.pkgs.withPackages (e: [e.htmlize]))
   ]' \
  --command env ORG_GTD_CLI_DAEMON=0 \
  python3 -m pytest test_org_gtd_cli.py -q -n 4
```

## License

MIT
