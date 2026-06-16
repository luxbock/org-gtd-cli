---
name: gtd-mine
description: Audit a past agent session for friction with the GTD skills or org-gtd-cli, and file improvement tasks into the GTD system. Use when the user wants to review a session for issues with the GTD tooling, or asks to mine a session for GTD-related fixes. Conservative — finding nothing is a valid result.
allowed-tools: Bash(cass *), Bash(org-gtd-cli *), Bash(grep *), Bash(jq *), Read, Skill
user-invocable: true
argument-hint: <session-id | session description> [-- <known issues>]
effort: high
---

# gtd-mine — Mine GTD friction from a past session

Read a past agent session (Claude Code, pi, codex, …), look for genuine friction with the GTD skill set or `org-gtd-cli`, and (with confirmation) file improvement tasks into the user's own GTD system.

This is the GTD analogue of `cm-mine`, but it does **not** use `cm` — proposed improvements land in `org-gtd-cli` directly, where the user processes them like any other inbox item.

## Conservatism is the prime directive

Many sessions just work. The user runs this skill *just in case* on sessions that didn't obviously fail, so the failure mode to avoid is fabricating issues to look useful.

- A session with smooth tool use and no retries is a **legitimate "no findings"** result. Say so plainly.
- Friction must be **observable in the transcript** — visible errors, retries, the user correcting the agent, the agent flailing across multiple commands, or a tool behaving in a way the SKILL didn't predict.
- Do **not** propose stylistic tweaks ("the SKILL could mention X again"), generic improvements ("more examples would help"), or speculative bugs that the transcript doesn't actually show.
- If the user described specific issues in the arguments, anchor on those first; only branch out if you find clearly related friction nearby in the transcript.
- Aim for **zero or a few high-quality findings**. Three findings from a session is already a lot. If you have more than five, you are over-fitting — drop the weakest.

## Inputs

The argument is one of:

- A **session ID** (UUID-shaped), or a `cass` `source_path` to a `.jsonl` transcript.
- A **free-form description** of a recent session (e.g. "the one yesterday where I tried to refile the dental research task"). Use `cass` to locate it.
- Optionally followed by `-- <known issues>` describing what the user already noticed. Treat this as a hint, not the full scope.

If neither is supplied, ask for one. Do **not** auto-pick a recent session — this skill should always run against a session the user named.

## What you are auditing

The GTD skill family (look up these SKILLs via your harness's skill loader):

| Skill | Purpose | File |
|---|---|---|
| `gtd` | Reference for `org-gtd-cli` itself — syntax, body constraints, JSON mode, all subcommands. Loaded by all other GTD skills. | `gtd/SKILL.md` |
| `gtd-capture` | Capture a free-form description into a task; infers title/category/tags. | `gtd-capture/SKILL.md` |
| `gtd-find` | Look up a specific task and explain what it requires before working on it. | `gtd-find/SKILL.md` |
| `gtd-plan` | Enrich a vague task with research / break it into a project. Does NOT complete it. | `gtd-plan/SKILL.md` |
| `gtd-process` | Clarify-and-organize step: process inbox + `@agent` tasks. | `gtd-process/SKILL.md` |
| `gtd-agenda` | Curated agenda views (today, contexts, projects, etc.). | `gtd-agenda/SKILL.md` |
| `gtd/references/conventions.md` | TODO states, tag taxonomy, file structure. | — |
| `gtd/references/tags.md` | Tag definitions inlined into capture/find/process. | — |

The CLI itself is `org-gtd-cli` — a Python wrapper around batch/daemon Emacs that operates on `~/Nextcloud/org/*.org`. Full reference: `notes/reference/org-gtd-cli.md` in this repo.

For org-mode markup itself — link syntax, timestamps, drawers, properties, etc. — the **source of truth is the official org manual**: <https://orgmode.org/manual/>. If a SKILL claim about org syntax conflicts with the manual, the manual wins.

## Step 1 — locate the session

```bash
# If the argument looks like a UUID or absolute path:
cass sessions --current --json                                           # current session
cass view <source_path> -n 1 --json                                      # peek at a known transcript

# If the argument is a description:
cass search "<description>" --robot --limit 5 --workspace "$(pwd)"
# Then narrow with --today / --week / --since as needed.
```

If multiple plausible sessions come back, present the top 3 with title + date and ask the user which one. Do **not** silently pick.

Once you have a `source_path`, you can `Read` it directly — it's a JSONL transcript.

## Step 2 — read with intent

Don't read the entire transcript line-by-line. Skim with intent:

1. Grep the transcript for the obvious friction signals first:
   ```bash
   grep -nE 'org-gtd-cli|Multiple matches|exit code 2|--index|conflicted copy|inbox\.org|set-done|append-body|refile|categories|@agent|SUBSTR|ambiguous' <source_path> | head -100
   ```
2. Around each hit, read ~20 lines of context to see what the agent was actually doing.
3. Cross-reference with what the user supplied as known issues. If they said "the agent kept getting ambiguous matches", that's where to focus.

Concrete signals that justify a finding:

- **Tool errors with a recovery loop** — exit 2 from `org-gtd-cli`, multiple-match disambiguation that took more than one retry, a hint the agent ignored, repeated `show` calls on the wrong heading.
- **The agent did something the SKILL told it not to do** — e.g. used `--category inbox`, paraphrased a heading, broke a heading into keywords before trying `show`, wrote markdown into a body, batched `set-done` at the end of a project instead of progressively.
- **The user corrected the agent** — direct "no, do X instead" or "you should have…" messages.
- **The CLI behaved in a way the SKILL didn't cover** — a flag that didn't exist, a JSON field the agent expected and didn't get, a body that ended up corrupted.
- **An action the SKILL doesn't describe at all** — the agent invented a workflow because the SKILL was silent on a real situation.

Signals that do **not** justify a finding (drop them):

- Generic LLM mistakes unrelated to GTD (prose phrasing, off-topic tangents).
- Single retries that the SKILL's existing guidance already covers — the SKILL is fine, that agent just didn't read it carefully.
- "The SKILL could be shorter / clearer / restructured" without a concrete failure to point at.
- Issues already fixed: check `git log -- skills/ org-gtd-cli.py org-gtd-cli.el +gtd-core.el` if a finding might be stale.

## Step 3 — categorize each finding

For every finding that survives Step 2, decide which bucket it belongs in. Some findings span both — note that explicitly and split them.

### Bucket A — `org-gtd-cli` improvements

The CLI itself needs a change: a missing flag, a confusing error message, a JSON field that should be added, a command that doesn't handle some edge case, a hint that should be more specific, a behavior that surprises agents who read the SKILL. Code lives at the org-gtd-cli repo root (`org-gtd-cli.py` Python wrapper + `org-gtd-cli.el` / `+gtd-core.el` Elisp engine).

→ Files under **`Computers/Agents/Agentic GTD System/org-gtd-cli tool`**.

### Bucket B — SKILL wording adjustments

The CLI is fine but the SKILL didn't steer the agent correctly: a missing warning, an example that's wrong, a constraint not stated, a workflow not documented, ambiguous wording that produced predictable misuse. The fix is editing the relevant `SKILL.md` (or `references/*.md`).

→ Files under **`Computers/Agents/Agentic GTD System/GTD SKILL's`** (note the apostrophe — verify exact heading via `org-gtd-cli --json categories | grep -i "GTD SKILL"`).

### Joint findings

If a CLI gap and a SKILL gap go together (e.g. the CLI should expose a flag *and* the SKILL should document it), file two related tasks — one in each bucket — and reference each other in the bodies.

## Step 4 — present findings to the user

Present every finding before filing anything. Use this format:

```
Findings from session <short id or path>:

 1. [SKILL: gtd-find] Agent searched with keywords instead of trying `show`
    with the user's full input first.
    Evidence: lines 412–451 — three failed `search` calls before falling back
    to `show "<input>"` which matched on the first try.
    Fix: Strengthen the "Do NOT skip show" callout in gtd-find/SKILL.md, or
    add a concrete bad-example block.

 2. [org-gtd-cli] `refile --category` returned ambiguous match without
    listing which categories matched.
    Evidence: lines 880–905 — agent retried 4× with longer substrings before
    falling back to `categories` to pick the full slash path.
    Fix: Include the matching candidates in the JSON `hint` field on
    ambiguous-match errors, like `show` already does.

No findings for: gtd-capture, gtd-process, gtd-agenda — used cleanly.
```

If you have **no findings**, say so:

```
No actionable friction found in this session. The agent used the GTD tooling
as documented; <brief, honest one-liner about what it did>. Nothing to file.
```

Do not pad. Do not propose "preventative" rules. End the skill there.

## Step 5 — ask: fix now, or leave for later?

After presenting findings, ask the user **per finding** (or in one batch if they're tightly related) what to do with it:

> Want me to fix this directly, or leave it for you to file via `/gtd-capture` later?

The point of this skill is the audit, not the bookkeeping. Filing every finding into GTD adds overhead the user often doesn't want — small SKILL wording tweaks and obvious CLI fixes are usually faster to just do.

- **"Fix it"** — go ahead and edit the SKILL or the CLI code. For SKILL fixes that's `skills/gtd*/SKILL.md` (and `references/`). For CLI fixes that's `org-gtd-cli.py` / `org-gtd-cli.el` / `+gtd-core.el` at the repo root. Make a focused change, then stop and let the user review.
- **"File it"** — do **not** invoke `gtd-capture` yourself. Tell the user the finding is ready to file and let them invoke `/gtd-capture` explicitly. They prefer to drive the capture themselves so the body / category / tags reflect their judgment. If it helps, restate the finding in a form that's easy to paste into `/gtd-capture`, and remind them which bucket it belongs in:
  - `Computers/Agents/Agentic GTD System/org-gtd-cli tool` for Bucket A
  - `Computers/Agents/Agentic GTD System/GTD SKILL's` for Bucket B
- **"Skip"** — drop the finding without filing or fixing.

If the user picks "fix it" for several findings, work through them one at a time (or in obviously-coupled groups), not all at once — easier to review.

## What this skill MUST NOT do

- Do not call `cm` — this skill is intentionally separate from procedural memory.
- Do not auto-pick sessions when the argument is missing. Ask.
- Do not invoke `/gtd-capture` on the user's behalf. They want to drive captures themselves.
- Do not edit code or SKILLs without an explicit "fix it" from the user — the audit comes first, fixes only after they've seen the findings and chosen.
- Do not invent findings to make the session "useful". Empty findings is a real outcome.
