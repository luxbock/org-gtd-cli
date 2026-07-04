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
| `outline` | Full nested outline of a file as JSON; nodes are typed (`is_category` / `is_event` / `is_project`) and calendar events carry their `timestamp`; `--full` adds each node's raw org `body` |

Run `org-gtd-cli <command> -h` for per-command options, or `org-gtd-cli -h`
for the full list.

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
becomes a per-item error without aborting the batch.

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

## License

MIT
