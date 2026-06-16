---
name: gtd-capture
description: Capture a GTD task from free-form description. Use when the user describes something they need to do, want to remember, or want to file as a task — even mid-conversation. Extracts the relevant information from context and files it using org-gtd-cli.
argument-hint: [free-form task description]
allowed-tools: Bash(org-gtd-cli *)
effort: low
---

# GTD Capture

The user wants to capture a task. They may describe it casually, in the middle of an unrelated conversation, or with full detail. Your job is to extract the right information and file it.

Before choosing tags, read `gtd/references/tags.md` from the shared GTD skill if it is available in your skill loader. If not, use the tag guidance below and avoid inventing new dashed tags.

## What to extract

From the user's description and the current conversation context, determine:

- **Title** — concise, actionable, starts with a verb when possible. Max ~80 chars.
- **Body** — any relevant context, links, or details that aren't in the title. SHOULD be a sentence or two at most. Omit if the title is self-explanatory. If the user gave a lot of context, distill it — lengthy or structured content MUST go into a linked note (`add-note --link-task`), not the body.
- **Category** — where it belongs in the task hierarchy. Run `org-gtd-cli --json categories` to find the right path. If unsure, omit (defaults to inbox).
- **Tags** — context tags (`@errand`, `@computer`, `@phone`, `@home`, `@agent`) and functional tags (`buy`, `email`, `url`). Only add tags that clearly apply.
- **Priority** — only set `[#A]` if the user indicates urgency. Most tasks don't need a priority.
- **Schedule/deadline** — only if the user mentions a specific date or timeframe.
- **State** — default is TODO. Use NEXT only if the user says they want to work on it right away.

## How to infer from context

- If the conversation is about a specific project or domain, use that to pick the category. Working on NixOS config? File under `Computers/NixOS`. Discussing a trip? File under `Travel`.
- If the user mentions a person they need to contact, consider `@phone` or `email` tags.
- If the user says "buy" or "order", add the `buy` tag.
- If the task involves going somewhere, consider `@errand`.
- `@agent` MUST only be used if the task can be completed autonomously by an AI agent without human involvement. A task *about* agents or *involving* agent tooling is not `@agent`. `@agent` MUST NOT be placed on parent headings — only on leaf tasks.
- If a task is `@agent` AND its difficulty is obvious at capture, you MAY set its effort tier: `org-gtd-cli set-property "<title>" --key AGENT_EFFORT --value light|standard|deep` (a task-complexity hint; `deep` = hard/ambiguous, `light` = mechanical). Otherwise leave it unset — it defaults to `standard` and can be assigned later during planning/processing. Don't slow down a quick capture to decide this.
- "Remind me" or "don't forget" = just a task, no special treatment.
- "This weekend" / "next week" / "by Friday" = set a schedule or deadline.

## Before filing

Show the user what you'll file in a compact format:

```
Task: [title]
Category: [path or inbox]
Tags: [tags]
Schedule: [date, if any]
Body: [body, if any]
```

Then ask for confirmation. You MUST NOT file without user confirmation.

## Filing

Before creating a new task, you SHOULD check whether it belongs to an existing project using `org-gtd-cli --json search`. If it does, use `add-subtask` instead of `add-task`. Note: `add-subtask`'s parent MUST be a task heading (one with a TODO keyword). To file under a category heading (a plain heading like `Computers/NixOS`), use `add-task --category "Full/Path"` instead — paths come from `categories` output.

Rules:
- Body text MUST NOT contain `* ` (asterisk-space) at line start — this corrupts the org file
- Body text MUST use org syntax, not markdown
- `--category` MUST use slash-delimited paths from `categories` output
- You MUST NOT use `--category inbox` — omit `--category` to file to inbox
- For longer body text, `set-body` and `append-body` read from stdin when no TEXT argument is provided — pipe text in instead of passing it as a shell argument:
  ```bash
  echo "Multi-line body text here" | org-gtd-cli --json set-body "task heading"
  ```
- The JSON response from `add-task` and `add-subtask` includes a `task` field with the full created task — no need to call `show` to verify

## If unsure

When the category is ambiguous, file to inbox and say so — the user can refile later. You SHOULD NOT guess on priorities or dates unless the user was clear about them.
