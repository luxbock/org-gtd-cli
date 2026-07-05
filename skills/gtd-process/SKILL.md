---
name: gtd-process
description: Process GTD inbox items and agent tasks. Use when the user wants to process their inbox, clarify and organize captured tasks, or work through agent-tagged tasks. Triggers on "process inbox", "process tasks", "work on agent tasks", or "gtd process".
argument-hint: [inbox | agent | all]
allowed-tools: Bash(org-gtd-cli *)
effort: high
---

# GTD Process

Process inbox items and/or agent tasks. This is the GTD "clarify and organize" step.

Before processing tags, read `gtd/references/tags.md` from the shared GTD skill if it is available in your skill loader.

## What to process

The argument determines scope:

- `gtd-process inbox` — process only inbox items
- `gtd-process agent` — work through `@agent` tasks
- `gtd-process` or `gtd-process all` — both

## Current state

At the start of the workflow, gather the current state yourself. These commands are intentionally shown as commands to run, not pre-expanded output:

```bash
# Inbox items
org-gtd-cli --json search --full "" --file inbox.org --state TODO,NEXT,WAITING,DEFER

# Agent tasks
org-gtd-cli --json search --full --tag @agent --state TODO,NEXT

# Projects (refile targets — task headings)
org-gtd-cli --json projects

# Categories (refile targets — non-task headings)
org-gtd-cli --json categories
```

Treat an empty result set as empty/none and continue.

---

## Processing inbox items

For each inbox item, go through the clarify steps below. You MUST present your recommendation to the user and get confirmation before making changes.

### Step 1: Show the item

The `--full` flag on the `search` output above already includes body text, so you can often skip a separate `show` call. If you need more detail (e.g. to resolve links in the body), use:

```bash
org-gtd-cli --json show "item heading"
```

### Step 2: Clarify

Ask these questions (you MAY answer some yourself if obvious from context):

1. **Is it actionable?**
   - No → consider `set-state CANCELLED` (trash) or `refile` to a reference category
   - Yes → continue

2. **What's the next action?** Ensure the title is a concrete action starting with a verb. `rename` if needed.

3. **Is it a single action or a project?** If it needs multiple steps, discuss breaking it into subtasks with `add-subtask`.

4. **Where does it belong?** Check the projects and categories lists. Categories can exist inside projects, so both lists may have relevant targets.
   - To refile under a **task or project heading**: use `refile --to` with the full path from `projects` output (e.g. `refile SUBSTR --to "Computers/Agents/org-gtd-cli tool"`).
   - To refile under a **category heading** (including those nested inside projects): use `refile --category "Category/Path"` with the full path from `categories` output.
   - Use the full slash-delimited path in both cases to avoid ambiguity.

5. **What context?** Add appropriate tags (`@computer`, `@home`, `@errand`, `@phone`, `@agent`). Use `set-tags --add`.

6. **When?** Add a schedule or deadline only if there's a real time constraint. Most tasks SHOULD NOT have dates.

7. **Priority?** Only set `[#A]` if genuinely urgent. Most tasks SHOULD NOT have a priority.

### Step 3: Refile

```bash
# Under a task/project heading (--to needs full path from `projects` output):
org-gtd-cli --json refile "item heading" --to "Computers/Agents/org-gtd-cli tool"
# Under a category heading (including those nested inside projects):
org-gtd-cli --json refile "item heading" --category "Computers/Agents"
org-gtd-cli --json refile "item heading" --category "Family/Ansel/Ansels Pet Ants"
```

The JSON response includes a `task` field with the full task state after refiling — no need to call `show` to verify.

This removes the item from inbox. After refiling, move to the next item.

**Bulk operations:** When processing multiple inbox items, use the `batch` subcommand to do them in one call. It reads a JSON array of `{"command", "args"}` objects from stdin (`heading` matches an existing task; `refile` items take `category`, `add-subtask` items take `parent`):

```bash
echo '[{"command":"refile","args":{"heading":"item 1","category":"Work"}},{"command":"set-done","args":{"heading":"item 2"}}]' | org-gtd-cli --json batch
```

When every item needs the same action, the homogeneous shortcut `--batch <subcommand>` also works:

```bash
echo '["item 1","item 2"]' | org-gtd-cli --json --batch refile --category "Work"
```

This is faster and reduces round-trips. Batch supports: `set-done`, `delete`, `refile`, `add-subtask`, `add-task`, `add-event`, `add-session-id`, `show`, `set-tags`, `add-tags`.

### Pacing

Process items one at a time. After each item, briefly state what you did and move to the next. If the inbox has many items, ask the user if they want to continue after every 5 items.

## Processing agent tasks

Agent tasks are tasks tagged `@agent` that can be completed autonomously by an AI agent.

### Step 1: Pick a task

Present the agent task list using the format below, then ask the user which to work on. You MUST NOT start working without user confirmation.

**Display format:**

- Omit DEFER tasks entirely.
- Use a single global numbering sequence across all sections.
- Each numbered line has the format: `N. STATE Task title` — the task title must not have anything appended after it, so it is easy to copy/paste.
- **Surface effort**: if a task's `properties.AGENT_EFFORT` (from the search JSON above) is `deep` or `light`, insert it as a marker between STATE and the title: `N. STATE [deep] Task title`. Omit the marker for `standard` or unset (the default) to keep the list quiet. The title still trails unadorned, so copy/paste is unaffected. This is advisory — it signals which tasks may warrant a stronger model or more reasoning effort when you dispatch them; the GTD system never picks a model.
- Do NOT use markdown bold or other formatting on task lines. Plain text only.

**Section 1 — "Projects":** Use a `## Projects` header. Group NEXT tasks by their parent project. Show the project name followed by a colon as a plain text subheading (not numbered), with the NEXT tasks listed beneath it. Only show NEXT tasks here; skip TODO tasks that belong to projects.

**Section 2 — "Standalone TODOs":** Separate from Projects with a `---` horizontal rule. Use a `## Standalone TODOs` header. List TODO tasks that are NOT part of any project. End with a `---` horizontal rule.

Example:

```
## Projects

Give the agent its own email:
1. NEXT Add protonmail-bridge to epiphyte NixOS config

org-gtd-cli:
2. NEXT [deep] Implement the delete command
3. NEXT Implement projects command in org-gtd-cli

---

## Standalone TODOs
4. TODO [light] Audit past agent sessions for GTD skill and org-gtd-cli friction points

---
```

### Step 2: Assess scope

Use the **exact heading text** from the CLI output — do not paraphrase or abbreviate.

```bash
org-gtd-cli --json show "task heading"
```

The `--full` search results above already include body text, so you may already have the detail you need. Use `show` only if you need to resolve links or see properties not in the search output.

Determine if this is a single-step task or needs breaking down. Use the JSON `heading` field for subsequent commands, `is_project` to check if it already has subtasks, and `parent` for project context.

- **Single-step:** Do the work, then `set-done`.
- **Multi-step:** Break into subtasks using the progressive handoff pattern:

```bash
# Create subtasks — agent work first, human review after
org-gtd-cli --json add-subtask "task" "Research X" --state NEXT --tags "@agent"
org-gtd-cli --json add-subtask "task" "Draft Y" --tags "@agent"
org-gtd-cli --json add-subtask "task" "Review and decide on approach"
# Remove @agent from parent
org-gtd-cli --json set-tags "task" --remove "@agent"
# Optionally set an effort tier per @agent subtask (task-complexity hint):
org-gtd-cli set-property "Research X" --key AGENT_EFFORT --value deep
```

`@agent` MUST only go on subtasks the agent can complete autonomously. Human review/decision tasks MUST NOT be tagged `@agent`. When a subtask's difficulty is non-obvious or notable, set its `AGENT_EFFORT` (`light`/`standard`/`deep`) so it surfaces in the list above — `standard` is the default and may be omitted.

### Step 2.5: Record session ID

If your harness exposes a session/thread ID, attach it to the task for audit trail:

```bash
if [ -n "${CLAUDE_SESSION_ID:-}" ]; then
  org-gtd-cli add-session-id "<task heading>" "claude_code:$CLAUDE_SESSION_ID"
fi

# CODEX_THREAD_ID is observed in Codex runtime, but is not currently listed
# in Codex's stable public environment-variable documentation.
if [ -n "${CODEX_THREAD_ID:-}" ]; then
  org-gtd-cli add-session-id "<task heading>" "codex:$CODEX_THREAD_ID"
fi

# Hermes exports HERMES_SESSION_ID into the environment of its terminal tool.
if [ -n "${HERMES_SESSION_ID:-}" ]; then
  org-gtd-cli add-session-id "<task heading>" "hermes:$HERMES_SESSION_ID"
fi
```

This links the task to this conversation in `cass` session history.

### Step 3: Do the work

Note the task's effort tier (`properties.AGENT_EFFORT`, default `standard`). It's advisory: a `deep` task may warrant dispatching with a stronger model or higher reasoning effort, a `light` one a cheaper/faster setup. The model choice is yours at dispatch — the GTD system never picks one. If you're already running and the tier suggests a mismatch (e.g. a `deep` task in a light session), flag it to the user rather than silently proceeding.

Follow the `AGENT:` instructions in the task body. If none exist, ask the user what kind of help is needed before proceeding.

- For research output longer than a paragraph, create a note with `add-note --link-task`.
- Body text MUST follow the constraints in the `gtd` skill (no `* ` at line start, keep it short, use org syntax). In org content, command names and inline code use `=verbatim=`, longer single-line copy-paste commands use a literal-example line beginning `: `, and multi-line code uses `#+begin_src <lang>` / `#+end_src`.

### Step 4: Complete

```bash
org-gtd-cli --json set-done "task heading"
```

`set-done` handles project progression automatically:
- Promotes the next actionable sibling to NEXT (skipping subprojects that already have active children, drilling into stuck subprojects)
- If all siblings are now done, leaves the parent project open and reports a `project-needs-review` side effect (in JSON: `{"action": "project-needs-review", ...}`)

**Caution:** Completing the final subtask does NOT auto-complete the parent project. When you see `project-needs-review`, check whether the project is genuinely finished: if yes, close it with an explicit `set-done` on the project heading; if more work remains, add those subtasks instead.

After completing a task, report what was done and move to the next agent task if the user wants to continue.
