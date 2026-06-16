---
name: gtd-agenda
description: Show a curated agenda view filtered by time, context, or topic. Use when the user asks what to work on, what's scheduled, what's due, or wants a filtered view of their tasks — e.g. "what's on today", "show me errands", "nixos tasks", "what's overdue".
argument-hint: <time | context | topic | view>
allowed-tools: Bash(org-gtd-cli *), Bash(date *)
effort: low
---

# GTD Agenda

Show the user a curated, focused view of their tasks based on a flexible argument. The value over raw CLI output is your judgment — filter noise, highlight what matters, organize by relevance.

## Step 1: Interpret the argument

Parse the user's argument into one or more of these intent categories:

| Intent | Examples | Primary command |
|--------|----------|-----------------|
| **Time period** | "today", "tomorrow", "this week", "next week", "March" | `agenda --from DATE --to DATE` |
| **Context tag** | "@errand", "@computer", "@home", "@phone", "@agent" | `agenda --tag TAG` |
| **Category tag** | "nixos", "emacs", "travel", "finance", "buy", "family" | `agenda --tag TAG` |
| **State view** | "next actions", "waiting", "deferred" | `agenda --state STATE` |
| **Built-in view** | "projects", "stuck", "refile", "web", "archive" | `agenda-view KEY` |
| **Priority** | "important", "urgent", "priorities", "high priority" | `agenda` then filter `[#A]` |
| **Overdue** | "overdue", "past due", "slipped" | `agenda --to YESTERDAY` |
| **Open-ended** | "what should I do", "everything", no argument | Combine: overdue + today + NEXT |

If the argument combines intents (e.g. "@agent nixos"), combine the filters. If the argument is empty or vague ("what should I work on?"), use the **default view** (see below).

## Step 2: Gather data

### Date computation

`agenda --from/--to` requires ISO dates. Compute them:

```bash
date +%Y-%m-%d                          # today
date -d "+1 day" +%Y-%m-%d              # tomorrow
date -d "last monday" +%Y-%m-%d         # start of this week
date -d "next sunday" +%Y-%m-%d         # end of this week
date -d "next monday" +%Y-%m-%d         # start of next week
date -d "next monday +6 days" +%Y-%m-%d # end of next week
```

### Command reference

```bash
# Time-filtered (only tasks with schedule/deadline in range)
org-gtd-cli --json agenda --from 2026-03-19 --to 2026-03-19       # today
org-gtd-cli --json agenda --full --from 2026-03-19 --to 2026-03-25  # this week, with body text

# Tag-filtered (all active tasks with tag, regardless of dates)
org-gtd-cli --json agenda --tag @errand
org-gtd-cli --json agenda --tag nixos --state NEXT,TODO

# State-filtered
org-gtd-cli --json agenda --state NEXT                             # next actions
org-gtd-cli --json agenda --state WAITING                          # blocked tasks

# Overdue (scheduled/deadlined before today, still open)
org-gtd-cli --json agenda --to YESTERDAY_DATE

# Built-in views (human-formatted, --json NOT supported)
org-gtd-cli agenda-view S    # stuck projects
org-gtd-cli agenda-view p    # all projects
org-gtd-cli agenda-view w    # waiting
org-gtd-cli agenda-view r    # tasks to refile
```

Filters can be combined: `agenda --tag nixos --state NEXT --from DATE --to DATE`. Add `--full` to any `agenda` call to include body text in results, avoiding separate `show` calls.

**Single-file queries:** `agenda` aggregates across all org files by design — it has no `--file` flag. For single-file queries (e.g. "what's in inbox?"), use `search --file inbox.org` instead.

**Tag AND/OR filtering:**
- Repeated `--tag` flags are AND: `--tag @agent --tag nixos` = must have both
- Comma within `--tag` is OR: `--tag @errand,@phone` = must have either
- `+` within `--tag` is AND (equivalent to repeated): `--tag @agent+nixos`

### Default view (no argument or vague query)

When the user just wants to know "what should I focus on?", gather three things:

1. **Overdue**: `--json agenda --to YESTERDAY_DATE`
2. **Today**: `--json agenda --from TODAY --to TODAY`
3. **NEXT actions**: `--json agenda --state NEXT`

These three queries give you the full picture — what's slipped, what's time-bound today, and what's actively in progress.

## Step 3: Filter and curate

After gathering data, apply judgment:

- **Deduplicate** — a task scheduled today will appear in both the date query and the NEXT query. Deduplicate by `heading` field. Show it once.
- **Ignore conflicted copies** — filter out tasks where the `file` field contains `conflicted copy`. Only show items from `tasks.org`, `inbox.org`, `calendar.org`, or `family-calendar.org`.
- **Hide non-actionable project siblings** — if a NEXT task from a project is shown, hide TODO siblings from the same project. They aren't actionable until the NEXT task is done. Use the `parent` field from JSON to identify siblings sharing the same parent project. Only show these if the user specifically asks about a project.
- **Prioritize** — `priority: "A"` items and overdue items are most urgent. NEXT tasks are more actionable than TODO tasks.
- **Group meaningfully** — organize by urgency/actionability, not just by raw state. Suggested sections:
  - **Overdue** (if any) — these need attention first
  - **Scheduled today** (if time-based query)
  - **Next actions** — things actively in progress
  - **Other tasks** — remaining matches
- **Be concise** — show task title, state, and relevant metadata (tags, schedule/deadline). The `file` field is not needed in the display. Keep org markup like `=code=` intact.

## Step 4: Present

Format as a clean, scannable list using markdown. This is a quick status check, not a report.

For each section, show a heading and the tasks beneath it. Include a count. If a section is empty, omit it.

Each task line: `1. STATE Title :tags: S:<date>`

For NEXT tasks that belong to a project, show the parent project title on an unindented line above, then the NEXT task indented beneath it. This gives context for what the next action is driving toward. The `parent` field from JSON provides this directly — no need for a separate `show` call. Standalone tasks (no parent project, `parent` is null or a category heading) don't need this. Example:

```
**Next actions (2)**
Set up email on epiphyte
1. NEXT Add protonmail-bridge to epiphyte NixOS config
Make claude's access to the Wayland more secure
2. NEXT Research clipboard proxy approaches
```

Number tasks sequentially across all sections (don't restart numbering per section) so the user can reference them by number (e.g., "work on #3", "tell me more about 5"). Use ordered markdown lists (`1.`, `2.`, etc.) to keep numbering stable.

If the result set is large (>15 items), summarize or suggest a narrower filter. If the result set is empty, say so clearly and suggest what the user might try instead.

End with a brief one-line observation if something stands out — e.g. "You have 3 overdue items" or "All NEXT actions are computer tasks." Don't force an observation if nothing is noteworthy.
