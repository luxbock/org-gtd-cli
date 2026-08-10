# GTD semantics — normative specification

## 1. Status & authority

This document is the normative specification of the GTD semantics
implemented by org-gtd-cli and its shared core (`+gtd-core.el`, also
consumed by the Doom mirrors). It describes **current** semantics; the
ONGOING model arrives later as a versioned delta to this document.

Authority order: **this document > code > prose** (README, skills,
CLAUDE.md — all derive from here). A disagreement between the code and
this document is a bug — one of the two is wrong — and the discrepancy
is triaged to a ruling before either side changes.

*(Transitional: during the semantics overhaul, §7 lists the known
code-vs-document disagreements and the issue that closes each. A row
is deleted when its fix lands; when the table is empty, §7, every
inline divergence marker, and this paragraph are removed.)*

Declarative statements in this document are normative. The historical
ruling ledger lives in the decision note
(`nixos-config/notes/decisions/gtd-task-state-semantics.md`); this
document is its consolidated distillation for current semantics — a
conflict between the two is a transcription bug, resolved against the
recorded ruling. Sections change only by olli's confirmation.

Consumers: the test suite's reference model (#45) implements exactly
this document; the prose docs (#31/#36) restate it without adding
semantics.

## 2. Definitions

The state object is the forest of headings in the org files. **Document
order** = top-to-bottom textual order. A **sibling group** = all
headings sharing a parent heading (or a file's top level). A group is
**uniform** when every sibling carries a TODO keyword; a group with any
keyword-less sibling is **mixed**, and no operation ever reorders a
mixed group.

Heading kinds:

- **Category heading** — no TODO keyword. Pure structure; may appear
  anywhere, including inside tasks.
- **Task** — a heading with a TODO keyword.

**Task descent (severing).** Task structure follows *immediate* task
parentage: a task's **parent task** is its immediate parent heading iff
that heading is itself a task. A category heading **severs** the chain —
tasks below a category heading have no task ancestor above it, wherever
that heading sits. A category heading inside a task scopes that task's
*information*; tasks beneath it form an independent task world of their
own (bucket semantics — they are not subtasks, dependencies, or
descendants of the enclosing task, even if that information later grows
a task tree). **Task descendants** = the transitive closure of direct
task children; nothing below a category heading is ever a task
descendant of anything above it.

- **Project** — a task with at least one direct task child (its task
  descendants are the project's content). A **subproject** is a project
  whose immediate parent heading is a task.
- **Leaf task** — a task with no direct task children (a task whose
  only children are category headings is a leaf). A **project child**
  is a task whose immediate parent heading is a task; a **lone task**
  is a leaf task with no parent task (top-level, or directly under a
  category heading — wherever that category heading sits). A **bucket**
  is a category heading gathering lone tasks.

**Zones.** A uniform sibling group has three zones — the **completed
block** (DONE/CANCELLED) at the top; the **DEFER block** at the bottom;
the **active zone** (TODO/NEXT/WAITING) in between. Within the active
zone, **NEXT tasks always sit at the top** (a task entering NEXT takes
its place there; order among multiple NEXTs is user data). WAITING may
sit anywhere in the active zone. The relative order of TODO/WAITING
entries is user data — no operation may change it: inside a project it
is the intended execution order; inside a bucket it carries no meaning.

## 3. State spaces

Keywords: `TODO`, `NEXT`, `WAITING`, `DEFER` (open) · `DONE`,
`CANCELLED` (closed). One-line meanings:

- **TODO** — filed, actionable eventually, not the current front.
- **NEXT** — the project's active front (project-internal; see
  legality). Default one per project; parallel fronts are declared by
  hand, never minted by the machine.
- **WAITING** — blocked on a concrete external event (CLI entry only
  via `set-state`, reason or blocker link required — §4.6).
- **DEFER** — deliberately shelved; invisible to promotion; sinks to
  the bottom zone.
- **DONE** — completed **and accepted**.
- **CANCELLED** — decided against; keeps the record (vs `delete`, which
  leaves none).

Legality by structure:

| | TODO | NEXT | WAITING | DEFER | DONE | CANCELLED |
|---|---|---|---|---|---|---|
| Lone task | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ |
| Project child (leaf) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Project / subproject heading | ✓ | ✗ | ✗ | ✓ | ✓* | ✓* |
| Category heading | — no keyword ever — |

\*Closure of a project requires every descendant task already closed. A
project's activity and blocked-ness are read from its children, never
asserted on its heading.

**Priorities.** No cookie by default; `[#A]` is the only cookie,
meaning urgent AND important; relative importance within a project is
sibling order, never cookies.

## 4. Operations

### 4.0 Conventions common to all operations

Every mutating operation addresses its target by case-insensitive
substring match on heading text (or org `:ID:`). An ambiguous or failed
match mutates nothing (I12) and reports the candidates. **Scope
exception (the only one):** `set-done`/`set-cancelled` address **open**
tasks only — an already-closed match reports not-found; no other
command filters its addressing by state. **Destination addressing:**
`refile --to` resolves its destination by case-insensitive **exact**
heading match over headings of any kind (category headings included),
unique-or-error — I12 applies to destinations exactly as to targets.

Every state change is stamped in the task's LOGBOOK drawer (I10).
Mutations report the resulting task state, plus a `side_effects` list
naming every machine-made change beyond the addressed heading
(promotions, demotions, reopenings, unblocks, `blocker-link-removed`,
`project-needs-review`)
— every command emits the same vocabulary for the same repair, `refile`
included. Placement moves (§4.1) are **never** side effects: placement
is presentation, not a state change. `--dry-run`, where offered, must
predict the real outcome — including failure.

**Closure repair (the reopen cascade).** Any operation that places or
reveals an *open* task in the task descent of a *closed* ancestor
reopens that ancestor chain: each closed ancestor task becomes TODO,
each reported as a `state-change` side effect — I4 stays a hard
invariant, arrival makes the record live again. Triggers: `add-subtask`
under a closed heading, `set-state` reopening a task below closed
ancestors, `refile` of an open subtree into a closed destination, and
`set-next` on a closed leaf (§4.7).

**Keyword outgrown by structure (demotion repairs).** A leaf gaining
its first direct task child can invalidate its own keyword (§3: NEXT
and WAITING live on leaves/project children only). The repair runs in
the same mutation, whatever caused the growth (`add-subtask` or
`refile`): a NEXT parent demotes to TODO (state-change side effect); a
WAITING parent demotes to TODO (state-change side effect; the
demotion is a CLI exit from WAITING, so the §4.6 cleanup runs —
`:REASON:` removed and the `TRIGGER`/`BLOCKER` pair unwound, the
reason surviving only in the LOGBOOK record) **and** additionally emits
`project-needs-review` for itself — the demotion disarmed a blocked
marker, and whether the blocker now lives on a child is a human call.

### 4.1 The reorder primitive

After state-affecting operations the target's sibling group is
re-sorted: **minimal move** — the changed task moves to its zone
boundary (closed → bottom of the completed block, NEXT → top of the
active zone, DEFER → top of the DEFER block); nothing else moves. A
task **leaving NEXT** while remaining in the active zone (NEXT→TODO,
including the demotion repairs of §4.0, or NEXT→WAITING) moves
minimally to immediately **below the NEXT prefix**; a task entering
WAITING **from TODO** keeps its position. A task **reopening out of
the completed block** — or leaving the DEFER block — into the active
zone lands at the **end of its zone**, matching the arrival rule
below: a reopen to NEXT (`set-next` on a closed leaf, §4.7) at the
end of the NEXT prefix; a reopen to TODO/WAITING at the end of the
active zone. The closure-repair cascade (§4.0) applies this per
reopened ancestor, in that ancestor's own sibling group. Mixed groups:
never reordered.

**Arrivals.** A task arriving in a group — `add-task`, `add-subtask`,
`refile` — is placed by the same primitive, never blindly appended: it
enters at the **end of its zone** (a new NEXT: the end of the NEXT
prefix; a new TODO/WAITING: the end of the active zone; closed: the
bottom of the completed block; DEFER: the end of the DEFER block).
Nothing else moves.

### 4.2 add-task

Files a freestanding task: to inbox.org (default), a named file, or
under a category heading in tasks.org. Pre: target category resolves
uniquely; state ≠ NEXT (a freestanding task is never NEXT — I3;
rejected at entry); state ≠ WAITING (create never mints WAITING —
enter it via `set-state`, whose §4.6 guardrail applies; rejected at
entry). Post: task placed in the target group per §4.1's
arrival rule (end of its zone; in an all-open inbox this coincides
with append-last); body/tags/schedule/deadline/priority as given.
*Divergence: `--state WAITING` is accepted at create — row 5.*

### 4.3 add-subtask

Adds a child task under an existing task heading. Pre: parent is a
task (category headings never match); state ≠ WAITING (create never
mints WAITING — enter it via `set-state`, whose §4.6 guardrail
applies; rejected at entry). Post: child placed per §4.1's
arrival rule (end of its zone); the §4.0 repairs run as needed — a
closed parent chain reopens (closure repair, the child being an open
arrival), a NEXT or WAITING parent gaining its first task child is
demoted (keyword-outgrown repair; the WAITING case also emits
`project-needs-review`). `--state NEXT` here is a legal
*hand-declared* parallel front (§3): the machine never mints a second
front (I6), but the user may. (An accepted asymmetry: NEXT may be
minted at create, WAITING may not — WAITING's guardrail lives on its
`set-state` entry.) *Divergence: `--state WAITING` is accepted at
create — row 5.*

### 4.4 set-done / set-cancelled

Close a task. Pre: target is a task; if the target is a project
heading, every task descendant must already be closed (I4) — a blocked
closure is a reported error, dry-run predicts it, and nothing changes.
Tasks below the target's *category* headings are not task descendants
(§2 severing) and never block closure; when open ones exist, the close
succeeds and **observes** them with a `warnings` entry (an
environmental fact, not an effect of the mutation — they are left
untouched). Post: keyword set, CLOSED timestamp added, LOGBOOK
stamped, any priority cookie stripped (§3; the removal is visible in
the reported task state); the promotion rule (4.5) runs from the
closed task; the sibling group is reordered (closed task joins the
completed block); auto-unblock runs (below). These two commands are
the *only* triggers of the promotion rule (I9); auto-unblock never
invokes it — the conditional wake (§4.6) is its own mechanism.

**Auto-unblock.** After the close, the closed task's `TRIGGER` entries
(§4.6 blocker links) are read and applied natively — org-depend is not
loaded; ids resolve cross-file via the org-id machinery. For each
linked WAITING task the flip is AND-gated on that task's `BLOCKER:`
property: it fires only when **all** listed blocker ids are now
closed; while any remains open the task stays WAITING, untouched. A
firing flip wakes the task per §4.6's conditional wake and is
reported as an `unblocked` side effect naming the woken task and its
new state. A `TRIGGER` id that no longer resolves is dropped silently
at close time (defensive — it covers on-disk states created outside
the CLI, which the §4.12 delete guard cannot police). *Divergence: no
auto-unblock exists — row 5.*

*Divergences:
strip-on-close not yet implemented — #41; the closure guard pierces
category headings and the severed-task warning does not exist — row
13.*

### 4.5 The promotion rule

Runs only from a just-closed project child (never for lone tasks,
never from plain `set-state`; auto-unblock does not invoke this rule —
it has its own conditional wake, §4.6). In order:

1. **Guard**: if any sibling (any position) is NEXT or WAITING, stop —
   the project is in motion; promotion never mints a second front (I6).
2. **All closed**: if every sibling is now closed, emit
   `project-needs-review` for the (open) parent and stop. The parent is
   never auto-closed (I8).
3. **Scan**: choose the first open TODO sibling in document order
   (whole group). For each candidate:
   - **leaf task** → promote TODO→NEXT; stop.
   - **subproject with an active (NEXT/WAITING) descendant** → skip it;
     continue.
   - **stuck subproject** → drill exactly one level: promote its first
     TODO non-project child; stop. If nothing promotable at that level,
     continue past it. Deeper nesting gets no promotion; the stuck view
     is the safety net (I7, I11).
   - **all-done-but-open subproject** → emit `project-needs-review` for
     it and continue past it.

### 4.6 set-state

Changes a keyword and what the transition itself implies, nothing
more: no promotion, ever (I9); LOGBOOK stamped; optional `--reason`
recorded as a state-change note. A transition **into** DONE or
CANCELLED on a legal target runs the same §4.4 close post-conditions
as `set-done`/`set-cancelled` — CLOSED timestamp, cookie strip,
reorder into the completed block, auto-unblock — the promotion rule
alone excepted (I9): the close machinery keys on the state
transition, not the command name. Pre: the target/state pair is legal
per the §3 matrix — NEXT requires a project child (lone tasks and
project/subproject headings rejected); WAITING requires a leaf (project
headings rejected); DONE/CANCELLED on a project heading require all
descendants closed, and a blocked attempt is a *reported error*, never
a silent no-op. Post: keyword set; the group is reordered per §4.1 (a
NEXT-exit moves below the NEXT prefix; WAITING entered from TODO keeps
its position); reopening a task below closed ancestors runs the §4.0
closure repair (the ancestor chain reopens, with side effects).

**WAITING entry.** `set-state` is the only CLI entry into WAITING
(the create commands never mint it — §4.2/§4.3). Entry requires at
least one of a reason or a blocker link; a bare WAITING is rejected.
`--reason` writes a single-line `:REASON:` property.
`--blocked-by SUBSTR` / `--blocked-by-id ID` (combinable with
`--reason` and with each other) link the entering task to a blocking
task: `:ID:` is minted on both tasks as needed; the blocker gains a
`TRIGGER: <waiting-id>(TODO)` entry (multivalued) and the waiting task
a `BLOCKER: <blocker-id>` entry. The property syntax is
byte-compatible with org-depend, so the links stay readable and
editable in Emacs; the AND-gate, conditional wake, and exit cleanup
are CLI-side — org-depend's own TRIGGER application (if ever loaded)
does not reproduce them.
Multiple blocker links are AND: the wake fires only when **all**
listed blockers are closed (§4.4). Emacs-written WAITING without a
reason is tolerated everywhere — reads, views, and transitions out of
WAITING never error on a missing `:REASON:`.

**The conditional wake (ruling 2026-08-06).** A WAITING task whose
last open blocker closes (§4.4 auto-unblock) leaves WAITING. It wakes
as **NEXT** iff it is a leaf project child (a lone task always wakes
as TODO — I3; a task that has grown task children wakes as TODO —
I3/§3 matrix) and (a) no open sibling (NEXT/TODO/WAITING) precedes it in
document order within its sibling group, ignoring the completed and
DEFER blocks, and (b) the group contains no NEXT — exactly when the
WAITING task was standing in for its project's front; otherwise it
wakes as plain **TODO**. Under I5, (a) implies (b) but not
conversely; (b) is stated so the rule is explicit and robust, and the
(b)-without-(a) case is precisely the accepted residual below. Position
never changes in either case, and the §4.5 promotion rule does not
run (I9). Accepted residual: when the group has no NEXT and the woken
task is not first, the project is left with no front — deliberately
stuck (the CLI must not guess between the woken task and an earlier
open TODO); the stuck view (I11) catches it.

**WAITING exit cleanup.** Any CLI-driven exit from WAITING — the
wake, `set-state`, `set-next`, a close, the §4.0 keyword-outgrown
demotion — removes `:REASON:` and unwinds the `TRIGGER`/`BLOCKER`
pair (the LOGBOOK record stays, I10). The unwind is reported as a
`blocker-link-removed` side effect — the same name delete's
waiting-side unwind (§4.12) emits (§4.0: same vocabulary for the same
repair).

*Divergence: reopening below closed ancestors runs no closure repair —
row 10.* *Divergence (#39): none
of the WAITING mechanism above exists — entry requires no reason or
link, no blocker links, no wake, no exit cleanup — row 5.*

### 4.7 set-next

Convenience front-setter. On a leaf: same guard as `set-state NEXT`;
already-NEXT is an idempotent success; a **closed** leaf (project
child) is accepted — it reopens straight to NEXT via the §4.0 closure
repair, cascading up its closed ancestors (a closed *lone* task stays
rejected — I3). On a project heading: if a
direct child is already NEXT, report it and change nothing; otherwise
promote the project's first eligible TODO child — same candidate rule
as the promotion scan's drill (first TODO *non-project* direct child);
subproject headings are never promoted (I3). On a subproject heading
target: rejected. *Divergence: a closed leaf is rejected instead
of reopened — row 10.*

### 4.8 refile

Moves a subtree under a new parent (`--category`: category headings
only; `--to`: per §4.0 destination addressing — exact match, any
heading kind, unique-or-error, self-nesting excluded). Post: the
subtree enters the destination group per §4.1's arrival rule (end of
its zone), then invariants are repaired: a moved NEXT that would be
freestanding or would duplicate an existing NEXT sibling is demoted to
TODO; a NEXT or WAITING target parent gaining its first task child is
demoted (§4.0 keyword-outgrown repair, the WAITING case emitting
`project-needs-review`); an open subtree arriving under a closed
destination chain runs the §4.0 closure repair. Every repair is
reported as a side effect — refile uses the same vocabulary as the
primitive commands. *Divergences: `--to` resolves first-hit and the
repairs are unreported (row 12); the closure and WAITING-parent
repairs do not run (rows 10, 11).*

### 4.9 move

Explicit user reordering (`--up`/`--down`/`--before`/`--after` a named
sibling) within one sibling group. The order it writes is user data
(§2). No state logic runs. Pre: in a uniform group, the resulting
order must still satisfy the zone invariant (I5) — completed block on
top, DEFER block at the bottom, active zone between, NEXT entries at
the top of the active zone; a move that would cross a zone boundary is
rejected with a hint naming the boundary. The check is
**moved-entry-relative** (ruling 2026-08-07): only a violation
involving the moved entry rejects, so a group already in violation
neither blocks unrelated moves nor gets repaired — repair-by-move stays
possible. Moves within a zone are always legal. Mixed groups carry no
zones and are unrestricted.

### 4.10 Annotation operations

`rename`, `set-schedule`, `set-deadline`, `set-priority`, `set-tags`/
`add-tags`, `set-body`/`append-body`, `set-property`, `add-session-id`:
state-neutral. Pre: body text must contain no heading delimiters
(`* ` at line start); `set-priority` accepts only `A` or clear (§3 —
anything else is rejected with the sibling-order hint). Post: only the
addressed attribute changes — no keyword change, no reorder, no
promotion. *Divergence: set-priority validation not yet implemented —
#41.*

### 4.11 archive

Retires a finished record. Pre: the task is closed (DONE/CANCELLED),
was created over a month ago, AND carries no `TRIGGER` entry (§4.6)
resolving to an **open** task (below). Post: the subtree moves to the
archive file; the history is preserved there. Open tasks below the
subtree's category headings do not block archiving (§2 severing), but
the archive **observes** them with the same `warnings` entry as §4.4 —
open severed work is never silently relocated into the archive. Never
triggers promotion: it relocates a record, it doesn't close work.

**Blocker eligibility (ruling 2026-08-07).** The third criterion is
§4.12's blocker protection in eligibility form, not a rejection with
new machinery: the command already considers eligible tasks only, so
single-task `archive` on a held-back blocker fails the same way it
would fail the age check, and `archive --all` simply does not select
it. It covers the multi-blocker corner: blocker B1 closes while its
sibling blocker B2 stays open — the §4.4 AND-gate has not fired, and
B1's `TRIGGER` still points at the open waiting task. Archiving B1
would relocate it where its id may no longer resolve when B2 later
closes and the gate asks whether **all** blockers are closed. The
criterion holds B1 back exactly until the gate fires: the wake is a
WAITING exit, so the §4.6 cleanup unwinds the pair — B1 becomes
eligible with no special casing. A `TRIGGER` id that resolves to a
*closed* task, or does not resolve at all, never blocks eligibility —
that is the debris §4.4's close-time silent drop already tolerates.
*Divergence: the blocker eligibility criterion does not exist — row
5.*

### 4.12 delete

Permanently removes a record. Pre: exact full-heading match (stricter
than substring — a destructive op gets no fuzzy matching); the target
must have **no child heading at all** — task or category alike. The
guard is deliberately structural, not semantic: §2's severing never
widens what delete may destroy, and content the semantics ignore
(severed task worlds, information headings) is still content. Close or
empty the subtree first. Post: the subtree is gone
without trace — for work decided against, CANCELLED is the
record-keeping alternative. Never triggers promotion.

**Blocker guard (ruling 2026-08-07).** Delete is rejected when the
target carries a `TRIGGER` entry (§4.6 — it blocks WAITING tasks)
whose id resolves to an existing task: the error names the blocked
task(s) and nothing changes. Deletion is reserved for mis-added
records; a task others wait on has accrued semantic weight and gets
the careful path — `set-state CANCELLED`, a close op, so it feeds the
§4.4 AND-gate and, when it closes the last open blocker, fires the
conditional wake: the waiting task surfaces for re-triage. If the
blocker really was mis-added, first take the waiting task out of
WAITING (the §4.6 exit cleanup unwinds the `TRIGGER`/`BLOCKER` pair);
the now-linkless task then deletes normally. A `TRIGGER` id that does
**not** resolve never trips the guard — nothing actually waits, and
the dangling debris disappears with the deletion. Archive applies the
same protection as an eligibility criterion (§4.11).

The guard is role-based, not state-based — what it protects is being
waited **on** — and deliberately one-directional: a waiting task that
nothing waits on (it carries no resolving `TRIGGER` entry of its own)
stays deletable, so a mis-added WAITING task remains removable.
Deleting a task carrying `BLOCKER:` entries removes the deleted
task's id from each blocker's `TRIGGER:` entry, reported as a
`blocker-link-removed` side effect on the delete response
(unresolvable blocker ids are skipped silently). This waiting-side
unwind never touches the §4.4 gate — the deleted waiter simply stops
waiting — and the blocker-side path is gone (guarded above), so
delete never wakes anything. A task in **both** roles — a chained
dependency; §4.6 permits a WAITING task as a blocker — is protected
by the guard like any other blocker, with the same escape hatches:
take *its* waiter out of WAITING first (§4.6 exit cleanup), or
better, `set-state CANCELLED` — a close op, so its `TRIGGER` feeds
the §4.4 gate and can wake its waiter, and a WAITING exit, so the
§4.6 cleanup unwinds its own `BLOCKER`/`TRIGGER` pair.
*Divergence: neither the blocker guard nor the waiting-side unwind
exists — row 5.*

## 5. Derived views

### 5.1 The read model

Every view is a pure function of the file forest: a read leaves the
files byte-identical, and no view computes hidden state — everything a
view shows is derivable from §2 structure and §3 keywords. Views
render what is on disk: a normatively-illegal state (e.g. an
Emacs-written WAITING on a project heading) is neither repaired nor
rejected — the same predicates and block rules apply to it as to any
legal state. Rows carry
the task's org `:ID:` when present, so reads correlate stably across
commands (#29).

### 5.2 Derived predicates

For any task `t` and project heading `p` (per §2):

- **deferred(t)** — `t` is DEFER, or some task ancestor of `t` is
  DEFER (§2 descent — category headings sever the walk). Inherited
  DEFER shelves the whole task descent, not only the marked heading.
- **active(p)** — some descendant task is NEXT or WAITING. An active
  project is in motion; promotion never enters it (§4.5).
- **stuck(p)** — `p` is open, not deferred(p), and not active.
  Deliberately includes an open project whose descendants are all
  closed: stuckness
  is the *persistent* surface where the closure decision (I8) waits
  (I11), complementing the *transient* `project-needs-review` side
  effect (§4.5).
- **progress(p)** — closed direct children / all direct children
  (CANCELLED counts as closed: progress measures settledness, not
  success).

*Divergences: two view predicates still read legacy tags instead
of states — the stuck screen treats a `:WAITING:`-tagged NEXT as
blocking, and the deferred screen reads DEFER-ness from a tag rather
than the project's own (or an ancestor's) DEFER state (#40); the
predicates pierce category headings, counting severed tasks as
descendants — row 13.*

### 5.3 agenda — the flat query

Structure-blind row list over all org files: every heading with a
keyword is a candidate row, project headings and children alike.
Membership = state filter (default: open states) ∧ tag filter (AND/OR
combinations) ∧ date window (on schedule or deadline; when a window is
given, dateless tasks are excluded — they cannot be in range). No
zone, project, or promotion interpretation whatsoever.

### 5.4 agenda-view — the curated dashboard

Block membership, defined on §2/§3 + 5.2 (the state-semantic blocks):

- **Calendar** — date-anchored entries for the day/span.
- **Next Tasks** — NEXT project children only: project headings and
  lone tasks never appear (I3 makes both unrepresentable); dated
  entries appear in Calendar instead, not here. A task with a DEFER,
  WAITING, CANCELLED, or DONE task ancestor (§2 descent) is excluded —
  the ancestor is closed or shelved and its state hides the subtree,
  whether or not §3 permits that state on the ancestor (§5.1 renders
  what is on disk; the exclusion is view membership, not legality).
- **Tasks** — open, unblocked loose ends: TODO tasks that are neither
  project-internal-and-surfaced-elsewhere nor WAITING/DEFER; the same
  DEFER/WAITING/CANCELLED/DONE ancestor-state exclusion as Next Tasks
  applies.
- **Waiting** — WAITING tasks; the same DEFER/WAITING/CANCELLED/DONE
  ancestor-state exclusion as Next Tasks applies — shelved WAITING
  work is represented by the DEFER ancestor's row, a subtree under a
  closed (DONE/CANCELLED) ancestor is hidden outright;
  future-scheduled ones hidden until due; rows carry
  `waiting_reason`/`blocked_by` (§5.5).
- **Stuck Projects** — exactly {p : stuck(p)} (subprojects included:
  stuckness is evaluated per project heading).
- **Projects** — all open, non-deferred (§5.2 — own or ancestor
  DEFER) projects.
- **Deferred** — own-state DEFER tasks only: a task under a DEFER
  ancestor is represented by that ancestor's row, never listed
  individually; future-dated hidden until due; stuck projects
  excluded (they belong to their own block).
- **Tasks to Archive** — tasks meeting §4.11's archive precondition.

Blocks keyed on workflow tags (`refile` capture triage, `url`/`note`
reading lists) are configuration riding the same query machinery, not
state semantics; this document does not constrain them.

### 5.5 Identity and structure reads

`search` (heading substring, default TODO/NEXT), `show` (one task in
full), `subtasks` (direct children + progress(p)), `outline` (the
forest skeleton), `categories` (category-heading paths), `render-file`
(HTML projection, ORG_DIRECTORY-scoped). All are projections of the
same state object; none applies GTD interpretation beyond §2's
structural definitions.

**WAITING surfacing.** `show`, `search --full`, `agenda` (§5.3), and
`agenda-view` (§5.4) rows carry `waiting_reason` and `blocked_by` for
WAITING tasks — null-safe (absent → null; reason-less WAITING never
errors, §4.6). When `:REASON:` is absent but blocker links exist, the
displayed reason derives from the blocker task's heading and current
state. *Divergence: no `waiting_reason`/`blocked_by` surfacing
exists — row 5.*

## 6. Invariants

- **I1** Category headings never carry a TODO keyword.
- **I2** The keyword set is exactly the six above; closed = {DONE,
  CANCELLED}.
- **I3** NEXT appears only inside a project — never on a lone task,
  never on a project/subproject heading.
- **I4** A project heading is closed only if every descendant task is
  closed.
- **I5** Zone invariant: completed block on top, DEFER block at bottom,
  active zone between; **NEXT entries form the top of the active
  zone**; the rest of the active zone's relative order is never
  machine-changed. Mixed groups: no machine movement at all.
- **I6** Promotion never mints a second front: if any sibling is NEXT
  or WAITING (any position), no promotion occurs.
- **I7** Promotion is always TODO→NEXT, on a leaf task, chosen as the
  first open TODO in document order, drilling exactly **one
  level** into a stuck subproject candidate; deeper nesting yields no
  promotion and is surfaced by the stuck view (I11).
- **I8** Completing the last open sibling never auto-closes the parent;
  it emits `project-needs-review`. Closure is always an explicit
  human-driven act.
- **I9** Only `set-done`/`set-cancelled` trigger the promotion rule
  (§4.5); parking (→WAITING/→DEFER) and plain `set-state` never do.
  The auto-unblock wake (§4.6) never runs §4.5 — it may mint NEXT
  only on a leaf project child, under its own conditional rule.
- **I10** Every state change leaves a LOGBOOK record.
- **I11** Stuck = an open, non-deferred (own or ancestor DEFER —
  §5.2) project with no NEXT and no WAITING anywhere in its task
  descent (§2 — tasks below category
  headings do not count) — deliberately including open projects whose
  children are all closed (the persistent surface for the closure
  decision).
- **I12** A failed or ambiguous match mutates nothing.

## 7. Known divergences

**This section is transitional scaffolding**: each row is deleted when
its issue lands, and the whole section — plus every inline
`*Divergence…*` marker in §§4–5 and §1's transitional paragraph — is
removed once the table is empty. This document stays forward-looking;
resolved history lives in git and the forge.

Each row: where the code currently disagrees with this document, and
the issue that closes the gap (strictly doc → tests → implementation).

| # | Current behavior (divergent) | Normative | Issue |
|---|---|---|---|
| 1 | *Retired 2026-08-07: the reorder primitive is the §4.1 minimal move — arrivals, NEXT-exit, and the WAITING no-move rule included; the full stable state-sort, the append-last arrivals, and the skip-sort mitigation's I5 leak are gone (#34).* | — | — |
| 2 | *Retired 2026-08-01: not reproducible on master — refile placement already conforms to §4.1's arrival rule; pinned by a plain regression test (PR #55). #34's scope is row 1 only.* | — | — |
| 3 | *Retired 2026-08-08: the promotion scan walks the whole sibling group in document order — the first open TODO is promoted wherever it sits relative to the closed task, so a project whose only open TODO precedes the task being driven is no longer stranded (#38).* | — | — |
| 4 | *Retired 2026-08-08: promotion reports every all-done-but-open subproject it passes as `project-needs-review` and continues past it — advisory only, nothing is closed (#38, olli ruling E5).* | — | — |
| 5 | WAITING entry requires no reason/blocker, and `add-task`/`add-subtask` accept `--state WAITING`; no blocker links, no auto-unblock (conditional wake), no cleanup on exit, no blocker-side delete guard, no waiting-side delete unwind, no archive blocker-eligibility criterion, no `waiting_reason`/`blocked_by` surfacing | §4.2, §4.3, §4.4, §4.6, §4.11, §4.12, §5.5 | #39 |
| 6 | View predicates read legacy state-mirror tags: stuck screen honors a `:WAITING:` tag on a NEXT child; deferred screen reads DEFER-ness from a tag, not deferred(p); the Next Tasks/Tasks ancestor exclusion rides tag inheritance rather than ancestor state; and the Waiting block has no DEFER-ancestor exclusion at all (its matcher's tag part cannot use the inherited `:WAITING:` tag, which the row itself carries) | §5.2, §5.4 | #40 |
| 7 | `set-priority` accepts any cookie; `set-done`/`set-cancelled` leave priority cookies in place | §3, §4.4, §4.10 | #41 |
| 8 | *Retired 2026-08-10: `set-state` rejects NEXT on a project/subproject heading and WAITING on a project heading (§3 matrix), and a blocked DONE/CANCELLED is the same structured rejection `set-done`/`set-cancelled` produce — a close driven through `set-state` is a genuine close running the §4.4 post-conditions, the promotion rule alone excepted (I9); `set-next`'s project path promotes the first TODO non-project direct child (§4.7) (#46).* | — | — |
| 9 | *Retired 2026-08-07: `move` guards the full §4.9 zone invariant — completed block, NEXT prefix, and DEFER block — rejecting any reordering that would put the moved entry on the wrong side of a boundary (#37 interim, #47 full).* | — | — |
| 10 | No closure repair: `add-subtask`, `set-state` (reopening), and `refile` place an open task below a closed heading with no reopen cascade; `set-next` rejects closed leaves outright | §4.0 (closure repair), §4.7 | #56 |
| 11 | A WAITING leaf gaining its first task child keeps WAITING on what is now a subproject heading; no demotion, no `project-needs-review` | §4.0 (keyword-outgrown repair), §4.3, §4.8 | #56 |
| 12 | `refile --to` resolves by first match in document order (silent on duplicates); refile reports none of its repairs as side effects | §4.0, §4.8 | #57 |
| 13 | Task traversal pierces category headings: the closure guard (I4), activity/stuckness predicates, and project detection treat tasks below category headings as task descendants; closing or archiving over open severed tasks emits no warning | §2 (severing), §4.4, §4.11, §5.2 | #58 |
| 14 | *Retired 2026-08-07: the level-1 guard is gone — a uniform top-level group places like an implicit category bucket, per the 2026-08-02 ruling (#34).* | — | — |
