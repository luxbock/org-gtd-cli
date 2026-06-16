---
name: gtd-find
description: Find a GTD task and explain what needs to be done. Use when the user asks to find, look up, or pull up a specific task by description — especially before starting work on it.
argument-hint: <task description>
allowed-tools: Bash(org-gtd-cli *)
effort: low
---

# GTD Find

The user wants to find a specific task and understand what it requires. This is typically done before starting work, to bring the task into context and confirm understanding.

## Steps

### Step 1: Show the task

**Always start with `show`.** Pass the user's input directly as the substring — do NOT extract keywords from it. When calling any command after initial lookup, use the **exact heading text** from the CLI output, not a paraphrase or abbreviation.

```bash
org-gtd-cli --json show "<user's input verbatim>"
```

Handle the three possible outcomes:

- **Single match** → success, proceed to Step 2. Use the `heading` field from the JSON response for all subsequent commands.
- **Multiple matches** → exits with code 2 and returns a JSON error with matches. Pick the best match and re-run with `--index N`:
  ```bash
  org-gtd-cli --json show "<same input>" --index 1
  ```
- **No match** → fall back to `search` with shorter keywords, then `show` the result:
  ```bash
  org-gtd-cli --json search "<shorter keywords>" --state all
  org-gtd-cli --json show "<exact heading from search result>"
  ```

> **Do NOT skip `show` and go straight to `search`.** The most common mistake is breaking the user's input into keywords and searching instead of just trying `show`. Always try `show` first with the full input.

> **Use exact headings.** After getting output from any CLI command, always use the exact heading text it returns for subsequent commands. Never paraphrase, abbreviate, or add decorations like `(tasks.org)` to headings.

### Step 2: Check for subtasks (if it looks like a project)

```bash
org-gtd-cli --json subtasks --full "task heading"
```

The `--full` flag includes body text for each subtask, so you don't need separate `show` calls to read subtask details.

### Step 3: Resolve links

Follow `[[file:...]]` links, `[[*Heading]]` references, or linked notes in `agent-notes/` to gather full context.

### Step 4: Explain what needs to be done
   - Summarize the task's goal in your own words
   - Note any `AGENT:` instructions in the body
   - If it's a project, summarize the current state (what's done, what's next, what's blocked)
   - Flag anything unclear or missing — ask the user rather than guessing

## Working on tasks

### Before starting

**Surface useful context with `cass`**, before you start doing non-trivial work, call the cm_context MCP tool with a short description of your task. This retrieves relevant playbook rules, anti-patterns, and history snippets.

**When you start working on a task**, attach the current harness session/thread ID as metadata if one is available:

```bash
if [ -n "${CLAUDE_SESSION_ID:-}" ]; then
  org-gtd-cli add-session-id "<TASK>" "claude_code:$CLAUDE_SESSION_ID"
fi

# CODEX_THREAD_ID is observed in Codex runtime, but is not currently listed
# in Codex's stable public environment-variable documentation.
if [ -n "${CODEX_THREAD_ID:-}" ]; then
  org-gtd-cli add-session-id "<TASK>" "codex:$CODEX_THREAD_ID"
fi
```

If no harness session/thread ID is available, skip this step rather than inventing one.

### Working through project subtasks

When the task is a project with subtasks, include these guidelines in your explanation:

**Mark subtasks done progressively.** As you complete each subtask, run `org-gtd-cli set-done "<subtask heading>"` immediately — don't batch completions at the end. This creates an audit trail, keeps the project state accurate mid-session, and ensures progress is saved if the session is interrupted.

**Closing the parent project is a separate, explicit step.** Marking the last open subtask as DONE leaves the parent project open and reports a `project-needs-review` side effect. When that happens, ask the user:
- Is the project actually done, or are there remaining steps that haven't been added yet?
- If the user confirms it's complete, close it with an explicit `set-done` on the project heading.
- If the project needs more work, add the missing subtasks instead (with `org-gtd-cli add-subtask`).

For tag definitions, read `gtd/references/tags.md` from the shared GTD skill if it is available in your skill loader.

### Upon completing a task

**You should commit your work before you present it to the user**. Use good judgement on the appropriate commit size per logical task grouping.
 
