---
name: gtd
description: Manage GTD tasks, projects, and notes using org-gtd-cli — a CLI for org-mode GTD task management. Use when the user mentions GTD, org-mode tasks, org agenda, or org-gtd-cli. Requires org-gtd-cli on PATH.
allowed-tools: Bash(org-gtd-cli *)
effort: low
---

# org-gtd-cli

CLI for managing org-mode GTD files. Wraps Emacs in batch mode — no running Emacs instance needed.

`ORG_DIRECTORY` points to the users org files (default: `~/org/`). Can be changed to something else for testing purposes.

## Body text constraints

Body text MUST NOT contain `* ` (asterisk-space) at the start of a line. In org-mode this is a heading delimiter — it will split the task into orphaned nodes, corrupting the file. `*bold*` at line start is fine; only `* ` followed by text is dangerous.

Body text SHOULD be short — a sentence or two, a link, a brief status update. If you need more than a short paragraph, or your content needs sections or structure, you MUST use `add-note --link-task` instead. Notes are full org files where headings and structure are safe.

Body text MUST use org-mode syntax, not markdown. See the syntax table below.

## Org syntax reference

Task bodies and notes MUST use org-mode syntax, not markdown.

| What         | Markdown (wrong)       | Org (correct)                          |
|--------------|------------------------|----------------------------------------|
| File link    | `[text](path)`         | `[[file:path/to/file.org][text]]`      |
| URL link     | `[text](https://...)`  | `[[https://...][text]]`                |
| Inline code  | `` `code` ``           | `=code=` or `~verbatim~`               |
| Bold         | `**bold**`             | `*bold*`                               |
| Italic       | `*italic*`             | `/italic/`                             |
| Code block   | ` ``` `                | `#+begin_src lang ... #+end_src`       |
| Example      | ` ``` `                | `#+begin_example ... #+end_example`    |

## Resolving org links in task bodies

Task bodies may contain org-mode internal links. When you encounter these, resolve them before continuing — they point to context you need.

| Link syntax | What it means | How to resolve |
|---|---|---|
| `[[*Heading Text]]` | Same-file heading | `grep -n '^\*.*Heading Text' <file>` to find the heading, then Read that region |
| `[[file:path/to/file.org]]` | Another file | Read the file at that path (relative to `ORG_DIRECTORY`) |
| `[[file:path/to/file.org::*Heading]]` | Heading in another file | Read the file, search for the heading |
| `[[id:xxx-xxx]]` | Org ID (any file) | `grep -rn 'xxx-xxx' *.org .orgids` to locate the heading |
| `[[#custom-id]]` | CUSTOM_ID in same file | `grep -n ':CUSTOM_ID: custom-id' <file>` |
| `[[https://...][label]]` | External URL | Use WebFetch if the content is needed |

If a link target can't be found (heading renamed, file moved), note that in your response rather than silently ignoring it.

## JSON output mode

**Always use `--json` for all CLI calls.** Place it before the command name:

```bash
org-gtd-cli --json show "task heading"
org-gtd-cli --json agenda --state NEXT
org-gtd-cli --json set-done "task heading"
```

JSON returns structured data with a `version` field and command-specific fields. Benefits:
- Headings, tags, states, and parents are discrete fields — no text parsing needed
- Use the `heading` field directly as SUBSTR input to follow-up commands
- Modifying commands return confirmation with old/new values and `side_effects` (e.g., auto-progression after `set-done`)

**Exceptions:** `agenda-view` and `org-timestamp` do not support `--json`.

## Substring matching

Most commands take a `SUBSTR` argument that matches task headings case-insensitively.

**This is the most common source of errors.** When multiple tasks match, the command fails with exit code 2 and lists matches with numbered indices. Always handle this:

```
$ org-gtd-cli show "research"
Multiple matches:
[1] DONE Research approaches to automate browsing (tasks.org)
[2] TODO Research dental options (tasks.org)

Use --index N to select one.
```

Fix by using `--index N` (1-based) or a more specific substring:

```bash
org-gtd-cli show "research dental"
org-gtd-cli show "research" --index 2
```

**Best practices for SUBSTR:**
- **Use the exact heading text** from previous CLI output for subsequent commands — do not paraphrase, abbreviate, or reword headings
- Use distinctive words from the task heading, not generic terms
- After an ambiguous match, prefer narrowing the substring over using `--index` (more readable, won't break if task order changes)
- When writing scripts or chained commands, always check the exit code
- File references like `(tasks.org)` in output indicate which file the task is in — they MUST NOT be used as SUBSTR arguments
- Do not include priority cookies like `[#A]`, org markup, or link syntax in SUBSTR — these are stripped before matching

## Querying

### search — find tasks by heading

```bash
org-gtd-cli --json search "org-gtd-cli"                    # TODO,NEXT by default
org-gtd-cli --json search --full "org-gtd-cli"              # include body text in results
org-gtd-cli --json search "org-gtd-cli" --state all         # include all states
org-gtd-cli --json search "dentist" --state WAITING          # specific state
org-gtd-cli --json search "research" --tag @agent            # with tag filter
org-gtd-cli --json search "buy" --tag buy,@errand            # OR: either tag
org-gtd-cli --json search "setup" --tag @agent --tag nixos   # AND: both tags
org-gtd-cli --json search "buy" --file inbox.org             # restrict to one file
```

Defaults to `TODO,NEXT` (active tasks only). Use `--state all` to include WAITING, DEFER, DONE, CANCELLED. Returns an indexed list:

```
[1] NEXT Fix timestamps in org-gtd-cli (tasks.org)
[2] TODO org-gtd-cli: add search command (tasks.org)
```

Always exits 0 — multiple matches are expected, not an error. Prints "No matches." if nothing found.

This is the go-to command for "find tasks related to X." Prefer it over `agenda | grep`.

**`search --file` vs `agenda`:** `agenda` aggregates across all org files by design. For single-file queries (e.g. "what's in inbox.org?"), use `search --file inbox.org` instead of `agenda` — agenda has no `--file` flag.

### agenda — filtered task list

Add `--full` to include body text in results (avoids separate `show` calls).

```bash
org-gtd-cli --json agenda                              # all open tasks
org-gtd-cli --json agenda --state TODO,NEXT            # filter by state
org-gtd-cli --json agenda --tag @agent                 # filter by tag
org-gtd-cli --json agenda --state NEXT --tag @agent    # combine filters
org-gtd-cli --json agenda --tag @agent --tag nixos     # AND: must have both tags
org-gtd-cli --json agenda --tag @errand,@phone         # OR: must have either tag
org-gtd-cli --json agenda --tag @agent+nixos           # AND (+ syntax, same as repeated --tag)
org-gtd-cli --json agenda --from 2026-03-15 --to 2026-03-20  # date range
```

Output: one line per task with state, heading, tags, file location, and schedule/deadline if present.

```
NEXT Book a rental car :travel: (tasks.org) S:<2026-03-16 Mon>
TODO Buy groceries :buy:@errand: (tasks.org) D:<2026-03-20 Fri>
```

`S:` = scheduled, `D:` = deadline.

### show — full task details

```bash
org-gtd-cli --json show "book rental car"      # task heading, body, subtasks
```

Shows the full heading with nesting depth, body text, properties, and immediate subtasks.

### subtasks — project overview

```bash
org-gtd-cli --json subtasks "improve agent workflow"
org-gtd-cli --json subtasks --full "improve agent workflow"  # include body text for each subtask
```

Shows the project heading and all subtask headings with their states. Includes a progress summary. Use `--full` to get body text without separate `show` calls.

### categories — file structure

```bash
org-gtd-cli --json categories
```

Shows all non-task headings (category headings) as slash-delimited paths, including those nested inside tasks and projects:

```
Work (tasks.org)
Computers (tasks.org)
Computers/Agents (tasks.org)
Computers/Emacs (tasks.org)
Computers/NixOS (tasks.org)
Computers/NixOS/epiphyte (tasks.org)
Family/Ansel/Ansels Pet Ants (tasks.org)
```

Paths may traverse through task/project headings to reach deeper category headings (e.g., `Family/Ansel/Ansels Pet Ants` passes through the "Ansel" task heading). These paths are used as targets for `--category` (on `add-task`) and `refile --category`. Always run `categories` first to find the exact path before using these flags.

## Creating

### add-task — new task

```bash
org-gtd-cli --json add-task "Buy milk" --tags "buy,@errand"
org-gtd-cli --json add-task "Review PR" --category "Work" --priority A
org-gtd-cli --json add-task "Call dentist" --schedule 2026-03-20 --tags "@phone"
org-gtd-cli --json add-task "Pay rent" --deadline 2026-04-01 --category "Finance"
org-gtd-cli --json add-task "Set up monitoring" --state NEXT --category "Computers/NixOS/epiphyte"
```

TITLE MUST be the first argument, before any flags. Without `--category`, the task goes to `inbox.org` — you MUST NOT use `--category inbox`, as "inbox" is not a category heading and will match incorrectly. With `--category`, the task is placed under that heading in `tasks.org`. Use the slash-delimited path from `categories` output to target nested headings.

### add-subtask — add to existing task/project

```bash
org-gtd-cli --json add-subtask "improve agent" "Write unit tests" --state NEXT
org-gtd-cli --json add-subtask "Finland trip" "Book flights" --schedule 2026-04-01
```

The first argument is a SUBSTR matching the parent task — it MUST be a task heading (one with a TODO keyword). Category headings (plain headings like `Computers` or `NixOS`) never match; to add a task under one of those, use `add-task --category "Full/Path"` instead. **After adding a subtask, consider whether the project has at least one NEXT subtask** — projects without a NEXT action are "stuck."

### add-event — calendar event

```bash
org-gtd-cli --json add-event "Team lunch" --date 2026-03-20 --time 12:00
org-gtd-cli --json add-event "School play" --date 2026-03-25 --time 18:00 --file family-calendar.org
```

Default file is `calendar.org`. Use `--file family-calendar.org` for family events. Files with a file-level `#+PROPERTY: calendar-id <id>` (like `family-calendar.org`) get org-gcal sync drawers; the JSON output's `calendar_id` field shows the id used (`null` = plain, non-syncing event).

### add-note — reference note with optional task link

```bash
org-gtd-cli --json add-note --title "Chiang Mai Dentists" --link-task "dental" --tags "health"
org-gtd-cli --json add-note --title "Meeting Notes" --sections "Summary,Action Items,Decisions"
```

Creates a file in `agent-notes/` (when `--link-task` is used) or `notes/`. The `--sections` flag pre-populates headings. The `--link-task` flag adds a link from the matched task to the note file.

When notes relate to a task, you MUST use `--link-task`. Do not create note files independently and reference them in the task body — the link won't be bidirectional. The correct workflow is: create the task first, then `add-note --link-task "task heading"` to create the note.

## Heading types

The org tree has three kinds of headings:

- **Task** — a heading with a TODO keyword (`TODO`, `NEXT`, `WAITING`, `DEFER`, `DONE`, `CANCELLED`). Represents a single action.
- **Project** — a task heading that has child task headings. No special keyword — the nesting makes it a project. The parent keeps its TODO keyword.
- **Category** — a plain heading with no TODO keyword (e.g., `* Work`, `** Emacs`, `*** Tools`). Used to organize the tree. Categories can appear at any level, including inside tasks and projects.

The distinction matters for `refile` and `categories`:
- `categories` lists all category headings as slash-delimited paths, even those nested inside tasks/projects.
- `refile --category` targets category headings only. When a category is nested inside a project, use the full path (e.g., `refile SUBSTR --category "Computers/Agents/Virtual Assistant/Research Leads"`) to avoid ambiguity.
- `refile --to` targets any heading including tasks. Use this when refiling under a task or project heading directly.

## Projects

A project is any task that has sub-tasks (child TODO headings). There is no special keyword — the nesting itself makes it a project. The parent keeps its TODO keyword.

Every project should have at least one `NEXT` subtask — this is the concrete next physical action. A project with only `TODO` subtasks and no `NEXT` is considered "stuck" (nothing is actively being worked on).

`set-done` enforces this automatically with project-aware promotion:
- When you complete a subtask, the next TODO sibling is promoted to NEXT
- If the next sibling is itself a subproject with no active child (stuck), `set-done` drills in and promotes the first actionable task inside it
- If the next sibling is a subproject that already has an active child (NEXT or WAITING), it is skipped
- **If all siblings are now done, the parent project is left open for review** — it is *not* auto-completed. The CLI emits an advisory ("All subtasks done — project left open for review: ..."), which in JSON surfaces as a side effect `{"action": "project-needs-review", ...}`. Closing the project is an explicit, separate `set-done` on the project heading — do that only when the project is genuinely complete, not just because the side effect appeared.

`set-state` does not do any of this — it only changes the keyword.

## Modifying

### State changes

```bash
org-gtd-cli --json set-done "buy milk"                 # mark DONE + auto-progress project
org-gtd-cli --json set-state "call dentist" WAITING    # any other state change
org-gtd-cli --json set-state "old task" CANCELLED
```

Valid states: `TODO`, `NEXT`, `DONE`, `WAITING`, `DEFER`, `CANCELLED`.

`set-done` is the only state-specific command. All other state changes use `set-state`. `set-done` does more than just changing the keyword — it adds a CLOSED timestamp, reorders the completed task, and auto-promotes the next actionable sibling to NEXT (project-aware: skips subprojects with active children, drills into stuck subprojects). If all siblings are done, the parent project is left open and a `project-needs-review` side effect is reported; closing the project requires an explicit `set-done` on the project heading. You SHOULD use `set-done` rather than `set-state DONE` when completing tasks. `set-next` is a convenience alias for `set-state SUBSTR NEXT`.

State changes automatically log timestamps in the task's LOGBOOK drawer. Do not add these manually.

### Scheduling and deadlines

```bash
org-gtd-cli --json set-schedule "buy milk" 2026-03-20
org-gtd-cli --json set-schedule "buy milk" 2026-03-20 --time 14:00
org-gtd-cli --json set-schedule "buy milk" --clear     # remove schedule
org-gtd-cli --json set-deadline "pay rent" 2026-04-01
```

### Tags

```bash
org-gtd-cli --json set-tags "buy milk" --tags "groceries,@home"   # replace all tags
org-gtd-cli --json set-tags "buy milk" --add "groceries"           # append tags
org-gtd-cli --json set-tags "buy milk" --remove "@errand"          # remove tags
org-gtd-cli --json add-tags "buy milk" --tags "groceries"          # append (standalone command)
```

For tag definitions, read `references/tags.md` from this skill directory before choosing or changing tags.

### Body text

```bash
org-gtd-cli --json append-body "dental research" "Found a good clinic on Nimmanhaemin."
org-gtd-cli --json set-body "dental research" "Complete rewrite of body text."
```

`append-body` adds to existing body. `set-body` replaces the entire body. Both preserve properties, logbook, and the trailing creation timestamp. Content MUST follow the body text constraints above.

Both commands read from stdin when no TEXT argument is provided — useful for multi-line or long content:

```bash
echo "Multi-line content here" | org-gtd-cli --json set-body "task heading"
cat notes.txt | org-gtd-cli --json append-body "task heading"
```

### Rename, refile, reorder

```bash
org-gtd-cli --json rename "buy mlk" "Buy milk"
org-gtd-cli --json refile "buy milk" --category "Shopping"
org-gtd-cli --json refile "set up monitoring" --category "Computers/NixOS/epiphyte"
org-gtd-cli --json refile "buy milk" --to "Shopping"
org-gtd-cli --json move "write tests" --up
org-gtd-cli --json move "write tests" --before "deploy app"
```

`refile` moves a task under a different heading. It has two mutually exclusive targeting modes:

- **`--category CAT`** — substring match against category (non-task) headings, including those nested inside tasks/projects. Exits 2 on ambiguous match. Use the full slash-delimited path from `categories` output for nested targets to avoid ambiguity. **Prefer this for most refiling.**
- **`--to TARGET`** — exact match (case-insensitive) on any heading text, including tasks. Use when you need to refile under a task or project heading directly.

### Delete

```bash
org-gtd-cli --json delete "Accidental duplicate task"        # permanently remove
org-gtd-cli --json delete "Accidental duplicate task" --dry-run  # preview first
```

Permanently removes a task from the file. Unlike `archive` or `set-state CANCELLED`, this leaves no trace. HEADING must match the full task heading exactly (case-insensitive). Cannot delete projects (tasks with subtasks) — remove subtasks first or use `set-state CANCELLED` instead.

Use `delete` for tasks created by mistake or duplicates. For tasks you decided not to do, prefer `set-state CANCELLED` (keeps a record). For completed tasks, use `archive`.

### Archive and cleanup

```bash
org-gtd-cli --json archive "old project"    # archive one task
org-gtd-cli --json archive --all            # archive all eligible DONE/CANCELLED
```

Tasks are only archived if they are finished and added more than a month ago.

Use `--dry-run` on any modifying command that supports it to preview changes.

## Batch operations

Two forms execute multiple operations in one call (one Emacs process).

- **Mutations** (both forms): `add-task`, `add-subtask`, `add-event`, `add-session-id`, `set-done`, `set-state`, `set-next`, `set-cancelled`, `set-priority`, `rename`, `move`, `set-schedule`, `set-deadline`, `set-tags`, `add-tags`, `set-body`, `append-body`, `set-property`, `refile`, `delete`.
- **Reads**: `show` (both forms), plus `agenda-view`, `outline`, `categories` (mixed `batch` only — so one call can pair a mutation with a recomputed view).

Task-addressing items take `heading` (substring) **or** `id` (org `:ID:`, matching each command's `--id` flag; `id` wins when both are given). New-item commands (`add-task`/`add-event`) take `title`; `add-subtask` takes `parent` + `title`.

**Mixed commands** — the `batch` subcommand reads a JSON array of `{"command", "args"}` objects from stdin:

```bash
echo '[
  {"command":"set-done","args":{"id":"f95d…"}},
  {"command":"add-task","args":{"title":"New task","tags":"work","schedule":"2026-06-15"}},
  {"command":"set-priority","args":{"heading":"task 3","priority":"A"}},
  {"command":"agenda-view","args":{}}
]' | org-gtd-cli --json batch
```

**Same command for every item** — `--batch <subcommand>` reads a JSON array of arg objects (or bare heading strings for heading-only commands). Shared args come from the command line (`add-subtask` parent positional, `refile --category`):

```bash
echo '["task 1","task 2"]' | org-gtd-cli --json --batch set-done
echo '[{"heading":"task 1","state":"WAITING"},{"id":"f95d…","state":"DEFER"}]' | org-gtd-cli --json --batch set-state
echo '[{"title":"Step 1","state":"NEXT"},{"title":"Step 2"}]' | org-gtd-cli --json --batch add-subtask "parent task"
echo '["item 1","item 2"]' | org-gtd-cli --json --batch refile --category "Work"
```

Both forms return a JSON object with a `results` array (one result per input item, in order, each with `success` and either the command's normal fields or an `error`) and a `summary` with counts. A failing item does not abort the rest. Exit 0 if at least one item succeeded, 1 if all failed. Use batch when performing several operations to reduce round-trips.

## Session tracking

Record which agent session worked on a task. Session IDs are stored in the task's LOGBOOK drawer.

```bash
# Add a session ID (idempotent — duplicate is a no-op), when your harness exposes one.
if [ -n "${CLAUDE_SESSION_ID:-}" ]; then
  org-gtd-cli add-session-id "<heading>" "claude_code:$CLAUDE_SESSION_ID"
fi

# CODEX_THREAD_ID is observed in Codex runtime, but is not currently listed
# in Codex's stable public environment-variable documentation.
if [ -n "${CODEX_THREAD_ID:-}" ]; then
  org-gtd-cli add-session-id "<heading>" "codex:$CODEX_THREAD_ID"
fi

# Hermes exports HERMES_SESSION_ID into the environment of its terminal tool.
if [ -n "${HERMES_SESSION_ID:-}" ]; then
  org-gtd-cli add-session-id "<heading>" "hermes:$HERMES_SESSION_ID"
fi

# Get all session IDs for a task
org-gtd-cli --json get-session-ids "<heading>"
# Returns: {"sessions": [{"agent": "claude_code", "session_id": "...", "timestamp": "..."}]}
```

Session IDs also appear in the `sessions` field of `show --json` output.

Format: `<agent>:<session-id>` where agent maps to `cass` connector slugs (e.g. `claude_code`, `codex`, `hermes`, `pi_agent`). If no harness session/thread ID is available, skip this step rather than inventing one. Do not assume `CODEX_THREAD_ID` is stable across future Codex releases; use it only when present.

## Mutation responses

All mutation commands (`set-done`, `set-state`, `refile`, `add-task`, `add-subtask`, `rename`, `set-tags`, etc.) include a `task` field in their JSON response with the full task state after the operation. This eliminates the need to call `show` after a mutation to verify the result.

## Error hints

JSON error responses include a `hint` field with recovery suggestions (e.g. how to disambiguate a multiple-match error). Check this field before retrying.

## Timestamps

Use `org-timestamp` to generate correctly formatted org timestamps:

```bash
org-gtd-cli org-timestamp 2026-03-20               # [2026-03-20 Fri]
org-gtd-cli org-timestamp 2026-03-20 14:00         # [2026-03-20 Fri 14:00]
org-gtd-cli org-timestamp 2026-03-20 --inactive    # [2026-03-20 Fri]
```

You generally don't need this directly — `add-task`, `set-schedule`, etc. handle timestamps internally.

## Common workflows

### Add a quick task to inbox

```bash
org-gtd-cli --json add-task "Task description"
```

### Check what to work on

```bash
org-gtd-cli --json agenda --state NEXT
```

### Process agent tasks

```bash
org-gtd-cli --json search --tag @agent --state TODO,NEXT
# Pick a task, read details:
org-gtd-cli --json show "task heading"
# Work on it, then mark done:
org-gtd-cli --json set-done "task heading"
```

### Break down a multi-step agent task

```bash
# 1. Assess the task
org-gtd-cli --json show "original task"
# 2. Create subtasks — agent work first, human review after
org-gtd-cli --json add-subtask "original task" "Research options" --state NEXT --tags "@agent"
org-gtd-cli --json add-subtask "original task" "Draft implementation" --tags "@agent"
org-gtd-cli --json add-subtask "original task" "Review and approve the draft"
org-gtd-cli --json add-subtask "original task" "Iterate based on feedback" --tags "@agent"
# 3. Remove @agent from parent (now a mixed project)
org-gtd-cli --json set-tags "original task" --remove "@agent"
# 4. Work through subtasks, set-done handles auto-progression
org-gtd-cli --json set-done "Research options"
# "Draft implementation" auto-promotes to NEXT — check side_effects in JSON response
```

### Start a research task with notes

```bash
org-gtd-cli --json add-note --title "Research Topic" --link-task "parent task" --sections "Findings,Sources"
# Edit the generated file, then mark done:
org-gtd-cli --json set-done "parent task"
```

### Review stuck projects

```bash
org-gtd-cli --json subtasks "project name"
# If no NEXT subtask exists, promote one:
org-gtd-cli --json set-next "subtask heading"
```

## Additional reference

For org-mode conventions (valid TODO states, tag taxonomy, timestamp formats, file structure), see [references/conventions.md](references/conventions.md).
