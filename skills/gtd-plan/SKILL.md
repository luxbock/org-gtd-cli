---
name: gtd-plan
description: Plan and enrich an existing GTD task — add actionable context, research, links, or break it into subtasks. Use when the user wants to flesh out a task, plan its execution, or turn a vague task into something concrete and actionable. Does NOT complete the task.
argument-hint: <task description>
allowed-tools: Bash(org-gtd-cli *), Read, WebFetch, WebSearch, Agent
effort: high
---

# GTD Plan

The user wants to take an existing task and make it actionable — by enriching it with context, research, and concrete details, or by breaking it into a structured project with subtasks. This is the bridge between "captured but vague" and "ready to execute."

**The golden rule: plan the task, don't complete it.** The output is an enriched task or a project with clear next actions — not the finished work itself.

The goal of this skill is to end up with a plan that stands out on its own. Don't rely on previous chat context being available in the session that will execute the plan.

## Step 1: Find and understand the task

Search for the task and show its full details. After finding the task, use the **exact heading text** from the CLI output for all subsequent commands — do not paraphrase or abbreviate.

```bash
org-gtd-cli --json search --full "description" --state all
org-gtd-cli --json subtasks --full "exact heading"  # if it might be a project already
```

The `--full` flag includes body text in results, so you often don't need a separate `show` call. Use `show` only if you need to resolve links or see properties not in the search/subtasks output.

Read any linked notes or references in the task body. Understand the current state before changing anything.

## Step 2: Clarify scope

Before planning, determine what the task actually requires. Consider:

- **Is the task clear enough to plan?** If the title or body is ambiguous, ask the user clarifying questions. Short task titles like "Look into X" often need clarification — what outcome does the user want? What constraints exist?
- **Is this a single action or a multi-step effort?** A task like "Buy birthday present for Ansel" is one action that needs enrichment. A task like "Set up home backup system" needs multiple steps.
- **Does it need research?** Many tasks benefit from concrete information — prices, links, options, steps from documentation, etc.

If anything is unclear, ask the user before proceeding. It's better to ask one good clarifying question than to plan in the wrong direction.

## Step 3: Plan

### Path A: Enrich a single-action task

For tasks that are one action but need more context to be actionable, add information directly to the task body using `append-body` (or `set-body` if replacing existing content).

Good enrichment makes a task executable without further research:

- **Purchase tasks:** Find specific products, compare options, include links and prices.
- **Decision tasks:** Summarize options with pros/cons.
- **Contact tasks:** Find the right phone number, email, or office hours.
- **Technical tasks:** Look up the relevant documentation, config snippets, or commands needed.
- **Location tasks:** Find addresses, opening hours, directions.

```bash
# Add actionable context to the task body
org-gtd-cli --json append-body "task heading" "Found 3 options: ..."

# For longer research output, create a linked note instead
org-gtd-cli --json add-note --title "Research Title" --link-task "task heading" --sections "Options,Recommendation"
```

Body text constraints apply: keep it short (a sentence or two) and use org syntax, not markdown. Command names and inline code use `=verbatim=`, longer single-line copy-paste commands use a literal-example line beginning `: `, and multi-line code uses `#+begin_src <lang>` / `#+end_src`. If the enrichment needs structure or more than a short paragraph, use `add-note --link-task` instead.

You MAY also improve the task title to be more specific and actionable:

```bash
org-gtd-cli --json rename "vague task name" "Specific actionable task name"
```

### Path B: Break into a project

For tasks that require multiple steps, create subtasks to form a project. `add-subtask`'s parent MUST be a task heading (one with a TODO keyword) — to add a task under a category heading (a plain heading like `Computers/NixOS`), use `add-task --category "Full/Path"` with a path from `categories` output instead:

```bash
# Create subtasks in execution order — use batch for multiple at once
# (shared parent on the command line, one args object per subtask):
echo '[
  {"title":"First concrete step","state":"NEXT"},
  {"title":"Second step"},
  {"title":"Third step"}
]' | org-gtd-cli --json --batch add-subtask "task heading"

# Mixed-command alternative (parent goes in each item's args):
echo '[
  {"command":"add-subtask","args":{"parent":"task heading","title":"First concrete step","state":"NEXT"}},
  {"command":"add-subtask","args":{"parent":"task heading","title":"Second step"}}
]' | org-gtd-cli --json batch

# Or individually:
org-gtd-cli --json add-subtask "task heading" "First concrete step" --state NEXT
org-gtd-cli --json add-subtask "task heading" "Second step"
org-gtd-cli --json add-subtask "task heading" "Third step"
```

Each response (batch or individual) reports the created subtask's `heading`, `state`, `file`, and `parent`.

Guidelines for good subtask breakdown:

- **Each subtask should be a single concrete action** — verb-first, clear completion criteria.
- **Order matters** — put them in execution sequence. The first subtask should be `NEXT`.
- **Consider dependencies** — if a step requires information, a decision, or materials from the user, make that a separate subtask. Phrase human tasks as questions when appropriate (e.g., "Decide between option A and B").
- **Tag appropriately** — add `@agent` only to subtasks an AI agent can complete autonomously. Add context tags (`@computer`, `@errand`, `@phone`, etc.) where they help.
- **Set an effort tier on `@agent` subtasks** — for each `@agent` leaf, set `AGENT_EFFORT` to reflect its difficulty so the dispatcher can pick an appropriate model later: `org-gtd-cli set-property "subtask heading" --key AGENT_EFFORT --value light|standard|deep`. `light` = mechanical/well-specified, `standard` = ordinary judgement (the default — you may omit it), `deep` = hard/ambiguous/high-stakes. It's a task-complexity hint only; the GTD system never names a model. Planning is the right moment to assign this thoughtfully.
- **Don't over-decompose** — 3-7 subtasks is usually right. If you need more, consider whether some steps can be combined or if there are natural subprojects.
- **Remove `@agent` from parent** if the original task had it and the project is now mixed (agent + human subtasks).

```bash
# If parent was @agent but project is now mixed:
org-gtd-cli --json set-tags "task heading" --remove "@agent"
```

### Enriching subtasks too

For Path B, you can also enrich individual subtasks with context — especially the NEXT one, so it's immediately actionable. Use `append-body` on subtasks the same way as Path A.

## Step 4: Improve title and metadata

After planning, consider whether the task's metadata should be updated:

- **Title** — rename if the original is vague or doesn't start with a verb. Make it scannable in an agenda view.
- **Tags** — add context tags if they're now obvious (e.g., you discovered the task requires a phone call → add `@phone`).
- **Schedule/deadline** — only add if there's a real time constraint that emerged during planning.

Do NOT add priorities unless the user indicates urgency.

## Step 5: Summarize

Tell the user what you did:

- What information you added or what subtasks you created
- Any decisions you made and why
- What the next action is (the NEXT subtask, or what the user should do next)
- Anything you couldn't resolve that the user needs to decide
