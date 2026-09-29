# Org-mode GTD Conventions

Reference for agents working outside the org repository where CLAUDE.md is not available.

## TODO states

- **Active track:** TODO → NEXT → DONE
- **Holding states:** WAITING, DEFER
- **Terminals:** DONE, CANCELLED

- **TODO** — task to be done
- **NEXT** — actively working on this (the concrete next physical action)
- **WAITING** — blocked on a concrete external blocker (SEMANTICS.md §3)
- **DEFER** — intentionally postponed; surfaced during periodic reviews of the task system. Shelved work is invisible to promotion and sinks to the DEFER block; DEFER on a project heading shelves the whole project (SEMANTICS.md §3, §5.2)
- **DONE** — completed
- **CANCELLED** — abandoned

Which states are legal where depends on structure — see [Projects vs. lone tasks](#projects-vs-lone-tasks).

### WAITING transitions

WAITING means a concrete external blocker (SEMANTICS.md §4.6). The typical entry is NEXT → WAITING. Exits:

- **→ NEXT** — blocker cleared, resume (project children only; a lone task is never NEXT)
- **→ DONE** — clearing the blocker completed the task
- **→ CANCELLED** — the blocker is insurmountable
- **→ TODO** — unblocked, but no longer the current priority

TODO → WAITING is valid for lone tasks; inside a project it is human-only. When the last open blocker linked with `--blocked-by`/`--blocked-by-id` closes, the task wakes automatically: as NEXT when it was standing in for its project's front, otherwise as TODO (a lone task always wakes as TODO) (SEMANTICS.md §4.4, §4.6).

Agent guardrails:

- Agents MUST NOT set WAITING without a concrete, *named* external blocker — a blocker link, or a `--reason` that names who or what is awaited. A placeholder reason ("waiting", "blocked", "later") does not satisfy this.
- Inside a project, agents MUST NOT move a task TODO → WAITING without explicit instruction; that transition is human-only. WAITING is never a way to park work for later — sibling position already encodes "later".

## Tags

See [tags.md](tags.md) for the full tag reference (context tags, functional tags, category tags).

## Priorities

No cookie by default. `[#A]` is the only cookie and means urgent AND important; relative importance within a project is sibling order, never cookies (SEMANTICS.md §3). Placed between TODO keyword and title:

```
* TODO [#A] Urgent task
```

`[#B]` and `[#C]` are retired: `set-priority` and `--priority` reject anything but `A` (SEMANTICS.md §4.10). Every close strips the cookie (SEMANTICS.md §4.4). Agents MUST NOT set or change priorities except on explicit instruction.

## Timestamps

- **Inactive** `[2026-03-10 Tue 16:21]` — creation timestamp, does NOT appear in agenda. Goes at the end of the node body.
- **Active** `<2026-03-10 Tue>` or `<2026-03-10 Tue 14:00>` — appears in agenda.
- **Range** `<2026-03-08 Sun 16:00-17:00>` — event with duration.
- **SCHEDULED:** `SCHEDULED: <2026-03-15 Sun>` — task scheduled for a date.
- **DEADLINE:** `DEADLINE: <2026-03-20 Fri>` — task has a deadline.

Day names must match the date. org-gtd-cli handles this automatically.

## File structure

- `tasks.org` — main task file organized by life area with nested subcategories
- `inbox.org` — capture inbox (default target for new tasks)
- `calendar.org` — personal calendar events
- `family-calendar.org` — family calendar events
- `ideas.org` — idea capture
- `notes/` — personal knowledge base (reference notes)
- `agent-notes/` — AI agent research and working notes

## Projects

A project is any TODO task with sub-TODO headings. The TODO keyword stays on the parent heading. Every project should have at least one NEXT subtask to avoid being "stuck."

A project is **stuck** when it is open, not deferred (neither it nor a task ancestor is DEFER), and has no NEXT and no WAITING anywhere in its task descent — tasks below a category heading don't count. This deliberately includes an open project whose tasks are all closed: stuckness is where the closure decision waits (SEMANTICS.md §5.2, I11).

## Projects vs. lone tasks

Structure decides which rules apply (SEMANTICS.md §2, §3, I3):

- **Project** — a task with at least one direct task child. It has a completion condition.
- **Category heading** — a heading with no TODO keyword; pure structure.
- **Lone task** — a leaf task with no parent task: top level, or directly under a category heading. Lone tasks are mutually independent, even when grouped under the same heading (a bucket).
- **Severing** — a category heading severs task descent, including one inside a task: tasks beneath it are not subtasks of the enclosing task, and a task whose only children are category headings is a leaf.

Execution semantics are project-only: execution order, promotion on completion, the stuck notion, and NEXT apply only inside projects. NEXT is project-internal: it is legal only on a leaf project child, a lone task is never NEXT, and there is no per-bucket front. Lone tasks have no priority ordering and never auto-progress; TODO → WAITING is legitimate for them (see [WAITING transitions](#waiting-transitions)).

A project heading is TODO while open; DEFER is legal and shelves the whole project; DONE/CANCELLED only once every descendant task is closed. It is never NEXT or WAITING — `set-state` rejects both. A project's activity and blocked status are read from its children (SEMANTICS.md §3, §4.6).

The layout zones below apply everywhere, buckets included — a readability contract, not an ordering statement.

## Sibling order and zones

Per SEMANTICS.md §2 (Zones), §4.1, §4.9, I5:

- Within a project, sibling order is the intended execution order. Within a bucket, order carries no meaning.
- Multiple NEXT children in one project are legal as hand-declared parallel fronts; the machine never mints a second one (SEMANTICS.md §4.3, I6).
- Every all-task sibling group, buckets included, has three zones: the **completed block** (DONE/CANCELLED) at the top, the **active zone** (TODO/NEXT/WAITING) with NEXT entries at its top, and the **DEFER block** at the bottom. A group with any keyword-less sibling is mixed and is never reordered.
- A state change moves only the changed task, to its zone boundary: closed → bottom of the completed block, NEXT → top of the active zone, DEFER → top of the DEFER block. A task leaving NEXT for TODO or WAITING moves to just below the NEXT entries. Nothing else moves.
- The relative order of TODO and WAITING entries is never machine-changed; a task entering WAITING from TODO keeps its position. WAITING is never a parking mechanism.
- Arrivals (`add-task`, `add-subtask`, `refile`) enter at the end of their zone; a task reopening out of the completed or DEFER block lands at the end of its zone.
- `move` rejects a reorder that would carry the moved task across a zone boundary; moves within a zone are always legal, and mixed groups are unrestricted.

## Agent tasks

Tasks tagged `:@agent:` may include an `AGENT:` instruction in the body describing what kind of help is needed. For longer research output, create a file in `agent-notes/` and link to it from the task.

## What org-gtd-cli manages automatically

Do NOT add these manually — they are generated by Emacs:

- `:PROPERTIES:` blocks and `:ID:` fields
- `:LOGBOOK:` drawers (state change history)
- `CLOSED:` timestamps (added when marking DONE)
- Priority cookie removal on every close
- Sibling auto-promotion via `set-done` (the first actionable sibling in document order becomes NEXT; when all children are done the parent is left open and a `project-needs-review` side effect is reported)
- Sibling placement after state changes and arrivals — the minimal move described in [Sibling order and zones](#sibling-order-and-zones)
- Waking a linked WAITING task when its last open blocker closes, and removing `:REASON:` and the blocker links on any exit from WAITING (SEMANTICS.md §4.4, §4.6)

## CLI efficiency features

- **`--full` flag** on `search`, `subtasks`, `agenda`, `outline` — includes raw org body text in results, eliminating separate `show` calls
- **`--batch` flag** — reads JSON array from stdin for bulk operations (`set-done`, `delete`, `refile`, `add-subtask`, `add-task`, `add-event`, `add-session-id`, `show`, `set-tags`, `add-tags`); items behave exactly like single calls (side effects, validation, full JSON fields)
- **Auto-stdin** — `set-body` and `append-body` read from stdin when no TEXT argument is provided
- **`task` field in responses** — mutation commands return full task state in JSON, no need to `show` after mutating
- **Error hints** — JSON errors include a `hint` field with recovery suggestions
