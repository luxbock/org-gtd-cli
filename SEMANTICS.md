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
  anywhere, including inside projects.
- **Task** — a heading with a TODO keyword.
- **Project** — a task with at least one task among its descendants. A
  **subproject** is a project that itself has a task ancestor.
- **Leaf task** — a task with no task descendants. A **project child**
  is a task whose nearest ancestor task exists; a **lone task** is a
  leaf task with no task ancestor (top-level or under category headings
  only). A **bucket** is a category heading gathering lone tasks.

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
- **WAITING** — blocked on a concrete external event (reason or blocker
  link required at entry, per #39).
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
match mutates nothing (I12) and reports the candidates. Every state
change is stamped in the task's LOGBOOK drawer (I10). Mutations report
the resulting task state, plus a `side_effects` list naming every
machine-made change beyond the addressed heading (promotions,
demotions, `project-needs-review`). `--dry-run`, where offered, must
predict the real outcome — including failure.

### 4.1 The reorder primitive

After state-affecting operations the target's sibling group is
re-sorted: **minimal move** — the changed task moves to its zone
boundary (closed → bottom of the completed block, NEXT → top of the
active zone, DEFER → top of the DEFER block); nothing else moves; a
task entering WAITING keeps its position. Mixed groups: never
reordered. *Divergence: today the primitive is a full stable sort by
rank closed(0) < NEXT(1) < TODO(2) < WAITING(3) < DEFER(4), which
clusters WAITING below TODO, destroying user interleaving whenever it
runs — #34. One mitigation is in place: `set-state` skips the sort when
a task enters WAITING from TODO/NEXT.*

### 4.2 add-task

Files a freestanding task: to inbox.org (default), a named file, or
under a category heading in tasks.org. Pre: target category resolves
uniquely; state ≠ NEXT (a freestanding task is never NEXT — I3;
rejected at entry). Post: task appended as last child of the target
(end of file, or end of the category's subtree); body/tags/schedule/
deadline/priority as given.

### 4.3 add-subtask

Adds a child task under an existing task heading. Pre: parent is a
task (category headings never match). Post: child appended as last
direct child; if the parent was NEXT it is demoted to TODO with a
`state-change` side effect (a project heading is never NEXT — I3); if
the created state ranks above TODO (NEXT or closed) the new group is
reordered. `--state NEXT` here is a legal *hand-declared* parallel
front (§3): the machine never mints a second front (I6), but the user
may.

### 4.4 set-done / set-cancelled

Close a task. Pre: target is a task; if the target is a project
heading, every descendant task must already be closed (I4) — a blocked
closure is a reported error, dry-run predicts it, and nothing changes.
Post: keyword set, CLOSED timestamp added, LOGBOOK stamped, any
priority cookie stripped (§3; the removal is visible in the reported
task state); the promotion rule (4.5) runs from the closed task; the
sibling group is reordered (closed task joins the completed block).
These two commands (plus #39 auto-unblock, when it lands) are the
*only* promotion triggers (I9). *Divergence: strip-on-close not yet
implemented — #41.*

### 4.5 The promotion rule

Runs only from a just-closed project child (never for lone tasks,
never from plain `set-state`). In order:

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

*Divergences: today the scan starts at the closed task and walks
forward only (not the whole group) — #38; the per-subproject
`project-needs-review` of step 3 is not yet emitted (the pass is
silent) — #38. Step 3's skip/drill behavior and steps 1–2 match
today's code.*

### 4.6 set-state

Changes a keyword, nothing more: no promotion (I9), no CLOSED-side
bookkeeping beyond org's own, LOGBOOK stamped, optional `--reason`
recorded as a state-change note. Pre: the target/state pair is legal
per the §3 matrix — NEXT requires a project child (lone tasks and
project/subproject headings rejected); WAITING requires a leaf (project
headings rejected); DONE/CANCELLED on a project heading require all
descendants closed, and a blocked attempt is a *reported error*, never
a silent no-op. Post: keyword set; the group is reordered unless the
task entered WAITING from TODO/NEXT (it keeps its position — §2 zones).
*Divergences (#46): today the NEXT guard admits subproject headings,
WAITING is accepted on project headings, and a blocked DONE/CANCELLED
silently no-ops while reporting success.* *(#39 will additionally
require a reason or blocker link at WAITING entry, and add
auto-unblock: WAITING→TODO + a promotion-rule run.)*

### 4.7 set-next

Convenience front-setter. On a leaf: same guard as `set-state NEXT`;
already-NEXT is an idempotent success. On a project heading: if a
direct child is already NEXT, report it and change nothing; otherwise
promote the project's first eligible TODO child — same candidate rule
as the promotion scan's drill (first TODO *non-project* direct child);
subproject headings are never promoted (I3). On a subproject heading
target: rejected. *Divergence (#46): today the project path promotes
the first TODO direct child even when that child is a subproject
heading — minting NEXT on a subproject.*

### 4.8 refile

Moves a subtree under a new parent (`--category`: category headings
only; `--to`: exact heading match, self-nesting excluded). Post: the
subtree is appended as the target's last child, then invariants are
repaired at the destination: a moved NEXT that would be freestanding
or would duplicate an existing NEXT sibling is demoted to TODO (with
side effect); a NEXT target parent that just became a project is
demoted to TODO; the destination group is reordered (a surviving NEXT
takes the top of the active zone). *Divergence: the NEXT-at-top
placement on refile is not yet enforced — #34.*

### 4.9 move

Explicit user reordering (`--up`/`--down`/`--before`/`--after` a named
sibling) within one sibling group. The order it writes is user data
(§2). No state logic runs. Pre: in a uniform group, the resulting
order must still satisfy the zone invariant (I5) — completed block on
top, DEFER block at the bottom, active zone between, NEXT entries at
the top of the active zone; a move that would cross a zone boundary is
rejected with a hint. Moves within a zone are always legal. Mixed
groups carry no zones and are unrestricted. *Divergence: today move
performs the reorder unguarded — #37 (completed-block boundary,
interim) then #47 (full zone guard).*

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

Retires a finished record. Pre: the task is closed (DONE/CANCELLED)
AND was created over a month ago. Post: the subtree moves to the
archive file; the history is preserved there. Never triggers
promotion: it relocates a record, it doesn't close work.

### 4.12 delete

Permanently removes a record. Pre: exact full-heading match (stricter
than substring — a destructive op gets no fuzzy matching); projects
rejected (close or empty them first). Post: the subtree is gone
without trace — for work decided against, CANCELLED is the
record-keeping alternative. Never triggers promotion.

## 5. Derived views

### 5.1 The read model

Every view is a pure function of the file forest: a read leaves the
files byte-identical, and no view computes hidden state — everything a
view shows is derivable from §2 structure and §3 keywords. Rows carry
the task's org `:ID:` when present, so reads correlate stably across
commands (#29).

### 5.2 Derived predicates

For a project heading `p` (per §2):

- **active(p)** — some descendant task is NEXT or WAITING. An active
  project is in motion; promotion never enters it (§4.5).
- **stuck(p)** — `p` is open, not DEFER, and not active. Deliberately
  includes an open project whose descendants are all closed: stuckness
  is the *persistent* surface where the closure decision (I8) waits
  (I11), complementing the *transient* `project-needs-review` side
  effect (§4.5).
- **progress(p)** — closed direct children / all direct children
  (CANCELLED counts as closed: progress measures settledness, not
  success).

*Divergence (#40): two view predicates still read legacy tags instead
of states — the stuck screen treats a `:WAITING:`-tagged NEXT as
blocking, and the deferred screen reads DEFER-ness from a tag rather
than the project's own (or an ancestor's) DEFER state.*

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
  entries appear in Calendar instead, not here.
- **Tasks** — open, unblocked loose ends: TODO tasks that are neither
  project-internal-and-surfaced-elsewhere nor WAITING/DEFER.
- **Waiting** — WAITING tasks; future-scheduled ones hidden until due.
- **Stuck Projects** — exactly {p : stuck(p)} (subprojects included:
  stuckness is evaluated per project heading).
- **Projects** — all open, non-DEFER projects.
- **Deferred** — DEFER tasks; future-dated hidden until due; stuck
  projects excluded (they belong to their own block).
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
  machine-changed. Mixed groups: no machine movement at all. *(Known
  divergence: refile of a NEXT task into an existing project — #34.)*
- **I6** Promotion never mints a second front: if any sibling is NEXT
  or WAITING (any position), no promotion occurs.
- **I7** Promotion is always TODO→NEXT, on a leaf task, chosen as the
  first open TODO in document order (#38), drilling exactly **one
  level** into a stuck subproject candidate; deeper nesting yields no
  promotion and is surfaced by the stuck view (I11).
- **I8** Completing the last open sibling never auto-closes the parent;
  it emits `project-needs-review`. Closure is always an explicit
  human-driven act.
- **I9** Only `set-done`/`set-cancelled` (and #39 auto-unblock) trigger
  promotion; parking (→WAITING/→DEFER) and plain `set-state` never do.
- **I10** Every state change leaves a LOGBOOK record.
- **I11** Stuck = an open, non-DEFER project with no NEXT and no
  WAITING anywhere in its subtree — deliberately including open
  projects whose children are all closed (the persistent surface for
  the closure decision).
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
| 1 | Reorder is a full stable state-sort; clusters WAITING below TODO, destroying active-zone interleaving whenever it runs | §2, §4.1 (minimal move) | #34 |
| 2 | A NEXT refiled into a project is not placed at the top of the active zone | §2, §4.8 | #34 |
| 3 | Promotion scans forward from the closed task only, not the whole group in document order | §4.5 | #38 |
| 4 | Promotion passes an all-done-but-open subproject silently — no per-subproject `project-needs-review` | §4.5 | #38 |
| 5 | WAITING entry requires no reason/blocker; no auto-unblock (WAITING→TODO + promotion run) exists | §4.6 | #39 |
| 6 | View predicates read legacy tags: stuck screen honors a `:WAITING:` tag on a NEXT child; deferred screen reads DEFER-ness from a tag; legacy tag stamps inherited from the old trigger machinery | §5.2, §5.4 | #40 |
| 7 | `set-priority` accepts any cookie; `set-done`/`set-cancelled` leave priority cookies in place | §3, §4.4, §4.10 | #41 |
| 8 | `set-state NEXT` admits subproject headings; `set-state WAITING` admits project headings; blocked DONE/CANCELLED silently no-ops reporting success; `set-next`'s project path can promote a subproject heading | §3 matrix, §4.6, §4.7 | #46 |
| 9 | `move` performs cross-zone reorders unguarded | §4.9 | #37 (completed-block boundary, interim) then #47 (full zone guard) |
