"""Pure-Python reference model of SEMANTICS.md (issue #45, stage 2b).

This module implements exactly the semantics specified in SEMANTICS.md
§§2-6: the state object (§2), the state spaces and legality matrix (§3),
the mutating operations with their pre/postconditions (§4) — including
the reorder primitive (§4.1) and the promotion rule (§4.5) — and the
derived predicates (§5.2). Naming is traceable to the document: section
references (§n.m) and invariant numbers (I1-I12) appear at each rule's
implementation site.

Divergence modes (§7). SEMANTICS.md is forward-looking: §7 tabulates the
places where today's CLI deliberately disagrees with the document, each
tied to the issue that closes the gap. The model therefore runs in one of
two modes:

- ``Divergences.normative()`` — the document's semantics, exactly.
- ``Divergences.current()`` — reproduces today's CLI behavior by enabling
  one named flag per applicable §7 row (``d7_no_priority_rules`` = §7
  row 7, and so on). The tier-2 conformance harness runs the model in this
  mode and expects an exact match against the real CLI; per-row
  expected-failure tests run the normative mode and flip green as each
  stage-2c fix lands (2026-07-31 ruling on #45).

The model is Emacs-free and I/O-free except for the org-text projection
helpers at the bottom (``to_org_text`` / ``parse_org_text``), which the
tier-2 harness uses to hand states to the real CLI and read them back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

# §3 — the keyword set is exactly these six (I2).
OPEN_STATES = ("TODO", "NEXT", "WAITING", "DEFER")
CLOSED_STATES = ("DONE", "CANCELLED")  # closed = {DONE, CANCELLED} (I2)
KEYWORDS = OPEN_STATES + CLOSED_STATES

# §2 zones of a uniform sibling group: completed block on top, DEFER
# block at the bottom, active zone between (I5).
ZONE_COMPLETED, ZONE_ACTIVE, ZONE_DEFER = 0, 1, 2


def zone_of(keyword):
    """§2: the zone a keyword belongs to within a uniform sibling group."""
    if keyword in CLOSED_STATES:
        return ZONE_COMPLETED
    if keyword == "DEFER":
        return ZONE_DEFER
    return ZONE_ACTIVE  # TODO / NEXT / WAITING


@dataclass(eq=False)
class Node:
    """One heading (§2). ``keyword is None`` = category heading (I1).

    ``eq=False``: nodes have identity, not structural equality — list
    membership and removal must never confuse two same-shaped siblings
    (skeletons are compared as tuples instead)."""

    heading: str
    keyword: str | None = None
    priority: str | None = None  # §3: '[#A]' is the only legal cookie
    tags: tuple = ()
    waiting_reason: str | None = None  # §3/§4.6: required at WAITING entry
    # §4.6 blocker links, org-depend byte-compatible on disk
    # (``BLOCKER: <id>`` on the waiter, ``TRIGGER: <id>(TODO)`` on the
    # blocker). The model has no ids, so both sides address by heading —
    # headings are globally unique in every generated and hand-written
    # state, which is the same uniqueness §4.0 addressing already needs.
    blockers: tuple = ()   # headings this task waits on
    triggers: tuple = ()   # headings waiting on this task
    logbook: int = 0  # I10: count of state-change records
    children: list = field(default_factory=list)

    def clone(self):
        return replace(
            self, tags=tuple(self.tags),
            blockers=tuple(self.blockers), triggers=tuple(self.triggers),
            children=[c.clone() for c in self.children])


@dataclass
class SideEffect:
    """§4.0: one machine-made change beyond the addressed heading."""

    action: str  # "state-change" | "project-needs-review"
    #              | "unblocked" | "blocker-link-removed"  (§4.0, #39)
    heading: str
    old_state: str | None = None
    new_state: str | None = None


@dataclass
class Result:
    """§4.0: what a mutation reports."""

    ok: bool
    error: str | None = None
    old_state: str | None = None
    new_state: str | None = None
    side_effects: list = field(default_factory=list)


@dataclass
class Divergences:
    """§7 rows as named flags; True = reproduce today's divergent behavior.

    Row 6 (view predicates reading legacy tags) has no flag yet: part 1
    does not model the derived views it affects.
    """

    # §7 rows 1 and 14 (#34, retired 2026-08-07 with the minimal-move
    # primitive): ``d1_full_sort`` and ``d14_no_toplevel_reorder`` are
    # gone — the CLI now implements §4.1 exactly, in every sibling
    # group including the top level.
    # §7 row 2 (#34) has no flag: the recorded divergence (refile not
    # placing arrivals in the destination's zones) does NOT reproduce on
    # 2026-07-31 master — the destination reorder runs unconditionally.
    # Row flagged to olli for re-examination when #34 lands.
    # §7 rows 3 and 4 (#38, retired 2026-08-08 with the whole-group
    # promotion scan): ``d3_scan_from_closed`` and
    # ``d4_no_subproject_review`` are gone — the CLI now scans the whole
    # sibling group in document order and emits a per-subproject
    # ``project-needs-review`` when it passes an all-done-but-open
    # subproject (olli ruling E5, 2026-07-28).
    # §7 row 5 (#39, retired 2026-08-10 with the WAITING mechanism):
    # ``d5_no_waiting_reason`` is gone — WAITING entry now requires a
    # reason or a blocker link, ``add-task``/``add-subtask`` reject
    # ``WAITING``, and the blocker links, the AND-gated auto-unblock with
    # its conditional wake, and the exit cleanup all exist.
    d7_no_priority_rules: bool = False  # §7 row 7 → #41
    # §7 row 8 (#46, retired 2026-08-10 with the set-state/set-next
    # legality guards and close-path parity): ``d8_lax_state_guards`` is
    # gone — the CLI now rejects NEXT on a project/subproject heading and
    # WAITING on a project heading, rejects a blocked DONE/CANCELLED
    # through ``set-state`` instead of reporting a false success, and
    # ``set-next``'s project path promotes the first TODO *non-project*
    # direct child (§4.7).
    # §7 row 9 (#47, retired 2026-08-07): ``d9_completed_block_only`` is
    # gone — move now guards the full §4.9 zone invariant (completed
    # block, NEXT prefix, DEFER block), so current and normative agree.
    # Observed divergence with no §7 row yet (PARKED for ruling
    # 2026-07-31): set-next on a *closed* leaf is rejected by the CLI,
    # while §4.7 ("same guard as set-state NEXT") implies acceptance.
    dx_setnext_rejects_closed_leaf: bool = False

    @classmethod
    def normative(cls):
        return cls()

    @classmethod
    def current(cls):
        return cls(d7_no_priority_rules=True,
                   dx_setnext_rejects_closed_leaf=True)


class Model:
    """The state object: a forest of headings in one file (§2)."""

    def __init__(self, roots=None, divergences=None):
        self.roots = roots if roots is not None else []
        self.div = divergences or Divergences.normative()

    def clone(self):
        return Model([r.clone() for r in self.roots],
                     replace(self.div))

    # ── §2 structure predicates ───────────────────────────────────────

    def parent_of(self, node):
        """The parent Node, or None for a root."""
        def walk(parent, children):
            for child in children:
                if child is node:
                    return parent
                found = walk(child, child.children)
                if found is not None:
                    return found
            return None
        return walk(None, self.roots)

    def sibling_group(self, node):
        """§2: all headings sharing the node's parent (or the top level)."""
        parent = self.parent_of(node)
        return self.roots if parent is None else parent.children

    def all_nodes(self):
        out = []
        def walk(children):
            for child in children:
                out.append(child)
                walk(child.children)
        walk(self.roots)
        return out

    def is_task(self, node):
        """§2: a heading with a TODO keyword."""
        return node.keyword is not None

    def task_descendants(self, node):
        return [n for n in self._descendants(node) if self.is_task(n)]

    def _descendants(self, node):
        out = []
        def walk(children):
            for child in children:
                out.append(child)
                walk(child.children)
        walk(node.children)
        return out

    def is_project(self, node):
        """§2: a task with at least one task among its descendants."""
        return self.is_task(node) and bool(self.task_descendants(node))

    def task_ancestors(self, node):
        out = []
        current = self.parent_of(node)
        while current is not None:
            if self.is_task(current):
                out.append(current)
            current = self.parent_of(current)
        return out

    def is_subproject(self, node):
        """§2: a project that itself has a task ancestor."""
        return self.is_project(node) and bool(self.task_ancestors(node))

    def is_leaf_task(self, node):
        """§2: a task with no task descendants."""
        return self.is_task(node) and not self.task_descendants(node)

    def is_project_child(self, node):
        """§2: a task whose nearest ancestor task exists."""
        return self.is_task(node) and bool(self.task_ancestors(node))

    def is_lone_task(self, node):
        """§2: a leaf task with no task ancestor."""
        return self.is_leaf_task(node) and not self.task_ancestors(node)

    @staticmethod
    def is_uniform(group):
        """§2: uniform = every sibling carries a TODO keyword."""
        return all(n.keyword is not None for n in group)

    # ── §5.2 derived predicates ───────────────────────────────────────

    def active(self, project):
        """§5.2: some descendant task is NEXT or WAITING."""
        return any(n.keyword in ("NEXT", "WAITING")
                   for n in self.task_descendants(project))

    def stuck(self, project):
        """§5.2 / I11: open, not DEFER, not active — including all-closed
        descendants (the persistent surface for the closure decision)."""
        return (self.is_project(project)
                and project.keyword in ("TODO", "NEXT", "WAITING")
                and project.keyword not in CLOSED_STATES
                and project.keyword != "DEFER"
                and not self.active(project))

    def progress(self, project):
        """§5.2: closed direct children / all direct task children."""
        tasks = [c for c in project.children if self.is_task(c)]
        if not tasks:
            return None
        closed = [c for c in tasks if c.keyword in CLOSED_STATES]
        return (len(closed), len(tasks))

    # ── addressing (§4.0) ─────────────────────────────────────────────

    def find(self, substring):
        """§4.0: case-insensitive substring match on heading text.

        Task-scoped: category headings never match a mutation's target
        (the CLI's find-task walks keyworded headings only). Returns
        (node, None) on a unique match, (None, error) otherwise; an
        ambiguous or failed match mutates nothing (I12).
        """
        needle = substring.lower()
        hits = [n for n in self.all_nodes()
                if n.keyword is not None and needle in n.heading.lower()]
        if not hits:
            return None, f"No task found matching: {substring}"
        if len(hits) > 1:
            return None, f"Ambiguous match: {substring}"
        return hits[0], None

    def find_category(self, substring):
        """Category-heading lookup (add-task --category, refile
        --category): keyword-less headings only."""
        needle = substring.lower()
        hits = [n for n in self.all_nodes()
                if n.keyword is None and needle in n.heading.lower()]
        if not hits:
            return None, f"No category found matching: {substring}"
        if len(hits) > 1:
            return None, f"Ambiguous category: {substring}"
        return hits[0], None

    # ── §4.1 the reorder primitive ────────────────────────────────────

    @staticmethod
    def _boundary_class(keyword):
        """The §4.1 boundary a keyword belongs to: closed block, NEXT
        prefix, DEFER block, or the free interior of the active zone."""
        if keyword in CLOSED_STATES:
            return "closed"
        if keyword in ("NEXT", "DEFER"):
            return keyword
        return "active-rest"  # TODO / WAITING — no boundary of their own

    def _reorder(self, group, changed, old_keyword=None):
        """§4.1: place the changed task within its sibling group.

        Minimal move — the changed task moves to its zone boundary and
        nothing else moves; a task whose boundary class did not change
        keeps its position (§2: sibling order is user data). OLD_KEYWORD
        is the keyword before the change; None means the task newly
        arrived in this group (add/refile) and enters at the end of its
        zone (§4.1 arrival rule). Mixed groups: never reordered (§2).
        Top-level groups place like any other — a uniform top-level
        group is an implicit category bucket (ruling 2026-08-02,
        ex-§7 row 14).
        """
        if not self.is_uniform(group):
            return
        if (old_keyword is not None
                and self._boundary_class(old_keyword)
                == self._boundary_class(changed.keyword)):
            # No boundary crossed (TODO↔WAITING, NEXT→NEXT, no-op
            # re-set, DONE↔CANCELLED): nothing moves — the relative
            # order is user data (§2, I5).
            return
        # Minimal move: remove, then reinsert at the zone boundary.
        group.remove(changed)
        completed_run = 0
        while (completed_run < len(group)
               and zone_of(group[completed_run].keyword) == ZONE_COMPLETED):
            completed_run += 1
        next_prefix_end = completed_run
        while (next_prefix_end < len(group)
               and group[next_prefix_end].keyword == "NEXT"):
            next_prefix_end += 1
        if zone_of(changed.keyword) == ZONE_COMPLETED:
            # Closed → bottom of the completed block (all paths).
            index = completed_run
        elif changed.keyword == "NEXT":
            # Entering NEXT from within the active zone → top of the
            # active zone (§2: NEXT always at the top); arrivals,
            # reopens out of the completed block, and releases out of
            # the DEFER block → the end of the NEXT prefix (§4.1
            # arrival/reopen rules).
            if (old_keyword is not None
                    and zone_of(old_keyword) == ZONE_ACTIVE):
                index = completed_run
            else:
                index = next_prefix_end
        elif changed.keyword == "DEFER":
            # DEFER → top of the DEFER block; arrivals → the end of it.
            if old_keyword is None:
                index = len(group)
            else:
                index = 0
                while (index < len(group)
                       and zone_of(group[index].keyword) != ZONE_DEFER):
                    index += 1
        else:
            # TODO/WAITING: leaving NEXT while staying in the active
            # zone (demotions included, §4.0) → immediately below the
            # remaining NEXT prefix — in place unless a NEXT sibling
            # would end up below it; arrivals, reopens out of the
            # completed block, and DEFER releases → the end of the
            # active zone.
            if old_keyword == "NEXT":
                index = next_prefix_end
            else:
                index = self._active_zone_end(group)
        group.insert(index, changed)

    @staticmethod
    def _active_zone_end(group):
        """Index just past the active zone (= start of the DEFER block)."""
        index = len(group)
        while index > 0 and zone_of(group[index - 1].keyword) == ZONE_DEFER:
            index -= 1
        return index

    # ── §4.5 the promotion rule ───────────────────────────────────────

    def _promotion_rule(self, closed_child):
        """§4.5: runs only from a just-closed project child.

        Returns the side-effect list. Steps 1-3 exactly as specified —
        the two scan divergences (ex-§7 rows 3-4) were retired with #38,
        so there is nothing left to switch here.
        """
        effects = []
        if not self.is_project_child(closed_child):
            return effects  # never for lone tasks
        group = self.sibling_group(closed_child)
        siblings_tasks = [n for n in group if self.is_task(n)]
        # Step 1 — guard (I6): any NEXT/WAITING sibling → the project is
        # in motion; promotion never mints a second front.
        if any(n.keyword in ("NEXT", "WAITING") for n in siblings_tasks):
            return effects
        # Step 2 — all closed (I8): emit project-needs-review for the
        # (open) parent and stop; the parent is never auto-closed.
        parent = self.parent_of(closed_child)
        if all(n.keyword in CLOSED_STATES for n in siblings_tasks):
            if (parent is not None and self.is_task(parent)
                    and parent.keyword not in CLOSED_STATES):
                effects.append(SideEffect(
                    "project-needs-review", parent.heading))
            return effects
        # Step 3 — scan for the first open TODO candidate: the whole
        # group in document order (I7).
        for candidate in group:
            if candidate.keyword != "TODO":
                continue
            if not self.is_project(candidate):
                # Leaf task → promote TODO→NEXT; stop (I7).
                candidate.keyword = "NEXT"
                candidate.logbook += 1
                effects.append(SideEffect(
                    "state-change", candidate.heading, "TODO", "NEXT"))
                self._reorder(group, candidate, old_keyword="TODO")
                return effects
            if self.active(candidate):
                # Subproject with an active descendant → skip it.
                continue
            # PARKED (scope of "all-done-but-open"): direct children
            # here vs descendants in active() above. With a category
            # heading inside the subproject, an open TODO below it does
            # not stop the emission — the subproject still reads as
            # all-done-but-open and gets a review side effect; and a
            # subproject whose only tasks live below such a heading has
            # no direct task children at all, so it falls through to the
            # drill and is passed silently. §4.5 does not pin the scope
            # (§2 severing, ex-§7 row 13's territory); awaiting olli's
            # ruling. `org-gtd-cli/subproject-all-done-p' matches this
            # choice exactly — neither side widens it unilaterally.
            subproject_tasks = [c for c in candidate.children
                                if self.is_task(c)]
            if subproject_tasks and all(c.keyword in CLOSED_STATES
                                        for c in subproject_tasks):
                # All-done-but-open subproject → emit review, continue.
                effects.append(SideEffect(
                    "project-needs-review", candidate.heading))
                continue
            # Stuck subproject → drill exactly one level (I7): promote
            # its first TODO non-project child; else continue past it.
            promoted = None
            for child in candidate.children:
                if child.keyword == "TODO" and not self.is_project(child):
                    promoted = child
                    break
            if promoted is not None:
                promoted.keyword = "NEXT"
                promoted.logbook += 1
                effects.append(SideEffect(
                    "state-change", promoted.heading, "TODO", "NEXT"))
                # §2: a task entering NEXT takes the top of the active
                # zone — placed within the drilled subproject's group.
                self._reorder(candidate.children, promoted,
                              old_keyword="TODO")
                return effects
        return effects

    # ── §4.6 the WAITING mechanism (blocker links, gate, wake) ────────

    def resolve_link(self, heading):
        """The other end of a blocker link, or None when it no longer
        resolves. Both §4.4's close-time drop and §4.6's cleanup skip a
        dangling link silently, so this never raises."""
        for node in self.all_nodes():
            if node.keyword is not None and node.heading == heading:
                return node
        return None

    def blocker_graph_reaches(self, start, target):
        """§4.6 ruling (a): is TARGET reachable from START over
        ``blockers`` edges? Cycle-safe (visited set), and a link that no
        longer resolves is skipped rather than treated as a rejection."""
        seen, queue = set(), [start]
        while queue:
            node = queue.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            if node is target:
                return True
            for heading in node.blockers:
                nxt = self.resolve_link(heading)
                if nxt is not None:
                    queue.append(nxt)
        return False

    def validate_waiting_entry(self, node, reason, blocked_by):
        """§4.6 WAITING-entry validation. Returns (blockers, error).

        The guardrail (at least one of reason / blocker link) plus the
        four edge cases ruled 2026-08-10: (a) no self-block and no cycle,
        (b) an already-closed blocker is rejected — the AND-gate stays
        close-event-driven and is never evaluated at entry, (d) a
        multi-line reason is rejected rather than flattened. (c), the
        WAITING→WAITING amend, is the caller's: every check here precedes
        every mutation (I12), so a rejected amend leaves the existing
        reason and links untouched.
        """
        blocked_by = list(blocked_by or ())
        if not reason and not blocked_by:
            return None, "WAITING requires a reason or a blocker link"
        if reason and "\n" in reason:
            return None, "WAITING --reason must be a single line"
        resolved = []
        for spec in blocked_by:
            blocker, error = self.find(spec)
            if blocker is None:
                return None, error
            if blocker is node:
                return None, "a task cannot block itself"
            if blocker.keyword in CLOSED_STATES:
                return None, (f"blocker {blocker.heading!r} is already "
                              f"{blocker.keyword}")
            if self.blocker_graph_reaches(blocker, node):
                return None, "would close a cycle in the blocker graph"
            resolved.append(blocker)
        return resolved, None

    def waiting_exit_cleanup(self, node):
        """§4.6 exit cleanup, shared by every WAITING exit site.

        Drops the reason, drops the node's own ``blockers``, and scrubs
        the node from each blocker's ``triggers``; a link that no longer
        resolves is skipped silently. The LOGBOOK record stays (I10).
        Returns one ``blocker-link-removed`` effect per unwound link,
        naming the blocker — the record actually edited — which is the
        same shape §4.12's waiting-side delete unwind emits.
        """
        effects = []
        node.waiting_reason = None
        blockers, node.blockers = node.blockers, ()
        for heading in blockers:
            blocker = self.resolve_link(heading)
            if blocker is None or node.heading not in blocker.triggers:
                continue
            blocker.triggers = tuple(h for h in blocker.triggers
                                     if h != node.heading)
            effects.append(SideEffect("blocker-link-removed",
                                      blocker.heading))
        return effects

    def wake_state(self, node):
        """§4.6 conditional wake (ruling 2026-08-06): NEXT iff the woken
        task is a leaf project child, no open sibling precedes it in
        document order (the completed and DEFER blocks ignored), and the
        group holds no NEXT; TODO otherwise."""
        if not (self.is_project_child(node) and self.is_leaf_task(node)):
            return "TODO"  # a lone task, or one that grew children (I3)
        group = self.sibling_group(node)
        for sibling in group:
            if sibling is node:
                break
            if sibling.keyword in ("NEXT", "TODO", "WAITING"):
                return "TODO"
        if any(s.keyword == "NEXT" for s in group):
            return "TODO"
        return "NEXT"

    def auto_unblock(self, closed):
        """§4.4 auto-unblock from a just-closed task.

        Each ``triggers`` entry that still resolves to a WAITING task is
        AND-gated on that task's own ``blockers``: the flip fires only
        when every one of them is closed, and while any remains open the
        waiting task is left completely untouched. A firing flip wakes
        the task per §4.6's conditional wake, runs the exit cleanup, and
        reports an ``unblocked`` effect. Position never changes and the
        §4.5 promotion rule is never invoked from here (I9).
        """
        effects = []
        for heading in tuple(closed.triggers):
            waiter = self.resolve_link(heading)
            if waiter is None or waiter.keyword != "WAITING":
                continue  # close-time silent drop of a dangling trigger
            gate = []
            for blocker_heading in waiter.blockers:
                blocker = self.resolve_link(blocker_heading)
                gate.append(blocker is None
                            or blocker.keyword in CLOSED_STATES)
            if not all(gate):
                continue  # an open blocker remains: untouched, no effect
            new_state = self.wake_state(waiter)
            waiter.keyword = new_state
            waiter.logbook += 1  # I10
            effects.append(SideEffect("unblocked", waiter.heading,
                                      "WAITING", new_state))
            effects.extend(self.waiting_exit_cleanup(waiter))
        return effects

    # ── §4.2 add-task ─────────────────────────────────────────────────

    def add_task(self, heading, state="TODO", category=None, priority=None):
        """§4.2: file a freestanding task; never NEXT (I3); placed per
        §4.1's arrival rule (end of its zone)."""
        if state == "NEXT":
            return Result(False, "NEXT is only valid inside a project")
        if state == "WAITING":
            # §4.2: create never mints WAITING — its guardrail lives on
            # the set-state entry (§4.6).
            return Result(False, "create never mints WAITING")
        if state not in KEYWORDS:
            return Result(False, f"Unknown state: {state}")
        node = Node(heading, state, priority=priority)
        if category is None:
            self.roots.append(node)
            self._reorder(self.roots, node)
            return Result(True, new_state=state)
        target, error = self.find_category(category)
        if target is None:
            return Result(False, error)
        target.children.append(node)
        self._reorder(target.children, node)
        return Result(True, new_state=state)

    # ── §4.3 add-subtask ──────────────────────────────────────────────

    def add_subtask(self, parent_substr, heading, state="TODO"):
        """§4.3: add a child task, placed per §4.1's arrival rule."""
        parent, error = self.find(parent_substr)
        if parent is None:
            return Result(False, error)
        if not self.is_task(parent):
            # Category headings never match as add-subtask parents.
            return Result(False, f"Not a task: {parent_substr}")
        if state == "WAITING":
            # §4.3: create never mints WAITING (the asymmetry with the
            # legal `--state NEXT` here is deliberate — WAITING's
            # guardrail lives on the set-state entry, §4.6).
            return Result(False, "create never mints WAITING")
        if state not in KEYWORDS:
            return Result(False, f"Unknown state: {state}")
        effects = []
        node = Node(heading, state)
        parent.children.append(node)
        if parent.keyword == "NEXT":
            # A project heading is never NEXT (I3): demote, side effect,
            # and re-place the demoted parent in its own sibling group —
            # immediately below that group's remaining NEXT prefix
            # (§4.1 NEXT-exit rule, demotions included).
            parent.keyword = "TODO"
            parent.logbook += 1
            effects.append(SideEffect(
                "state-change", parent.heading, "NEXT", "TODO"))
            self._reorder(self.sibling_group(parent), parent,
                          old_keyword="NEXT")
        # §4.1 arrival rule: the child enters at the end of its zone —
        # never blindly appended.
        self._reorder(parent.children, node)
        return Result(True, new_state=state, side_effects=effects)

    # ── §4.4 set-done / set-cancelled ─────────────────────────────────

    def set_done(self, substring, dry_run=False):
        return self._close(substring, "DONE", dry_run)

    def set_cancelled(self, substring, dry_run=False):
        return self._close(substring, "CANCELLED", dry_run)

    def _close(self, substring, new_state, dry_run):
        """§4.4: close a task; the only promotion triggers (I9)."""
        node, error = self.find(substring)
        if node is None:
            return Result(False, error)
        # Addressing scope (PARKED doc gap 2026-07-31): the CLI's close
        # commands match *open* tasks only — an already-closed target is
        # a not-found error. §4.0 does not yet specify per-command
        # addressing scope; modeled unconditionally pending a ruling.
        if node.keyword in CLOSED_STATES:
            return Result(False, f"No open task matching: {substring}")
        old_state = node.keyword
        if not self.is_task(node):
            return Result(False, f"Not a task: {substring}")
        if self.is_project(node):
            # I4: closure requires every descendant task already closed;
            # a blocked closure is a reported error and nothing changes.
            open_descendants = [n for n in self.task_descendants(node)
                                if n.keyword not in CLOSED_STATES]
            if open_descendants:
                return Result(
                    False, "blocked by an incomplete subtask",
                    old_state=old_state)
        if dry_run:
            preview = self.clone()
            result = preview._close(substring, new_state, dry_run=False)
            return replace(result, old_state=old_state)
        node.keyword = new_state
        node.logbook += 1  # I10 (CLOSED stamp + LOGBOOK)
        if not self.div.d7_no_priority_rules:
            node.priority = None  # §4.4: strip cookie on close (§7 row 7)
        # Ordering: §4.5 promotion runs on the state the close left, so
        # #39 changes nothing about it. It matters in one shape only — a
        # waiter that is a sibling of the closed task: promotion's step-1
        # guard sees it still WAITING and mints nothing, and the wake
        # then reads a group with no NEXT. That is exactly §4.6's
        # accepted residual (a woken task that is not first leaves the
        # project with no front, surfaced by the stuck view, I11).
        effects = self._promotion_rule(node)
        # §4.6: a close is itself a WAITING exit site.
        if old_state == "WAITING":
            effects.extend(self.waiting_exit_cleanup(node))
        effects.extend(self.auto_unblock(node))  # §4.4
        self._reorder(self.sibling_group(node), node,
                      old_keyword=old_state)
        return Result(True, old_state=old_state, new_state=new_state,
                      side_effects=effects)

    # ── §4.6 set-state ────────────────────────────────────────────────

    def set_state(self, substring, new_state, reason=None, blocked_by=None):
        """§4.6: change a keyword, nothing more — no promotion (I9).

        REASON and BLOCKED_BY (a list of blocker headings) are the §4.6
        WAITING-entry arguments; on any other transition REASON is a
        LOGBOOK note only and BLOCKED_BY is unused.
        """
        node, error = self.find(substring)
        if node is None:
            return Result(False, error)
        if not self.is_task(node):
            return Result(False, f"Not a task: {substring}")
        if new_state not in KEYWORDS:
            return Result(False, f"Unknown state: {new_state}")
        old_state = node.keyword
        # §3 legality matrix guards.
        if new_state == "NEXT":
            if not (self.is_project_child(node)
                    and self.is_leaf_task(node)):
                return Result(False, "NEXT is only valid on a project child leaf")
        blockers = []
        if new_state == "WAITING":
            if self.is_project(node):
                # §3: WAITING requires a leaf; project headings rejected.
                return Result(False, "WAITING is not valid on a project heading")
            # §4.6 entry guardrail + the 2026-08-10 rulings. All of it
            # runs before any mutation (I12), which is what makes a
            # rejected WAITING→WAITING amend leave the existing reason
            # and links fully intact.
            blockers, error = self.validate_waiting_entry(
                node, reason, blocked_by)
            if blockers is None:
                return Result(False, error, old_state=old_state)
        if new_state in CLOSED_STATES and self.is_project(node):
            open_descendants = [n for n in self.task_descendants(node)
                                if n.keyword not in CLOSED_STATES]
            if open_descendants:
                return Result(False, "blocked by an incomplete subtask",
                              old_state=old_state)
        effects = []
        # §4.6 exit cleanup: *any* CLI-driven exit from WAITING, the
        # WAITING→WAITING amend included — it replaces, never
        # accumulates (ruling 2026-08-10c).
        if old_state == "WAITING":
            effects.extend(self.waiting_exit_cleanup(node))
        node.keyword = new_state
        node.logbook += 1  # I10
        if new_state in CLOSED_STATES and not self.div.d7_no_priority_rules:
            # §4.6 (PR #68): a transition *into* a closed state runs the
            # same §4.4 close post-conditions as ``_close`` — the
            # promotion rule alone excepted (I9), so ``_promotion_rule``
            # is deliberately NOT called here.  Gated exactly like
            # ``_close``'s strip so #41 (§7 row 7) retires it in one
            # place.
            node.priority = None
        if new_state == "WAITING":
            node.waiting_reason = reason
            for blocker in blockers:
                if blocker.heading not in node.blockers:
                    node.blockers += (blocker.heading,)
                if node.heading not in blocker.triggers:
                    blocker.triggers += (node.heading,)
        if new_state in CLOSED_STATES:
            # §4.6 (PR #68): a close driven through set-state runs the
            # §4.4 post-conditions, the promotion rule alone excepted
            # (I9) — auto-unblock included.
            effects.extend(self.auto_unblock(node))
        # §4.6/§4.1: minimal move. The primitive itself keeps a
        # TODO→WAITING in place (same boundary class) and sends a
        # NEXT→WAITING immediately below the remaining NEXT prefix.
        self._reorder(self.sibling_group(node), node,
                      old_keyword=old_state)
        return Result(True, old_state=old_state, new_state=new_state,
                      side_effects=effects)

    # ── §4.7 set-next ─────────────────────────────────────────────────

    def set_next(self, substring):
        """§4.7: convenience front-setter."""
        node, error = self.find(substring)
        if node is None:
            return Result(False, error)
        if not self.is_task(node):
            return Result(False, f"Not a task: {substring}")
        if self.is_project(node):
            if self.is_subproject(node):
                # §4.7: a subproject heading target is rejected (both
                # modes — today's CLI guards this too).
                return Result(False, "set-next on a subproject heading")
            existing = [c for c in node.children if c.keyword == "NEXT"]
            if existing:
                # Report it and change nothing.
                return Result(True, old_state=node.keyword,
                              new_state=node.keyword)
            for child in node.children:
                if child.keyword != "TODO":
                    continue
                if self.is_project(child):
                    # I3 (§4.7): subproject headings are never promoted;
                    # the candidate is the first TODO *non-project*
                    # direct child — the promotion drill's rule.
                    continue
                child.keyword = "NEXT"
                child.logbook += 1
                self._reorder(node.children, child, old_keyword="TODO")
                return Result(
                    True, old_state=node.keyword, new_state=node.keyword,
                    side_effects=[SideEffect(
                        "state-change", child.heading, "TODO", "NEXT")])
            return Result(False, "no TODO children to promote")
        # Leaf path: same guard as set-state NEXT; already-NEXT is an
        # idempotent success (§4.7).
        if node.keyword == "NEXT":
            return Result(True, old_state="NEXT", new_state="NEXT")
        if (node.keyword in CLOSED_STATES
                and self.div.dx_setnext_rejects_closed_leaf):
            return Result(False, "is in done state", old_state=node.keyword)
        if not self.is_project_child(node):
            return Result(False, "NEXT is only valid inside a project")
        old_state = node.keyword
        node.keyword = "NEXT"
        node.logbook += 1
        # §4.6: set-next is one of the WAITING exit sites.
        effects = (self.waiting_exit_cleanup(node)
                   if old_state == "WAITING" else [])
        # §2/§4.1: entering NEXT from within the active zone takes the
        # top of the active zone; a DEFER release lands at the end of
        # the NEXT prefix.
        self._reorder(self.sibling_group(node), node,
                      old_keyword=old_state)
        return Result(True, old_state=old_state, new_state="NEXT",
                      side_effects=effects)

    # ── §4.8 refile ───────────────────────────────────────────────────

    def refile(self, substring, to=None, category=None):
        """§4.8: move a subtree under a new parent, then repair invariants."""
        node, error = self.find(substring)
        if node is None:
            return Result(False, error)
        if to is not None:
            # §4.8 --to: exact heading match (case-insensitive), any
            # heading type, first match in document order; matches
            # inside the source subtree are skipped (self-nesting
            # excluded).
            subtree = {id(n) for n in self._descendants(node)}
            subtree.add(id(node))
            targets = [n for n in self.all_nodes()
                       if n.heading.lower() == to.lower()
                       and id(n) not in subtree]
            if not targets:
                return Result(False, f"No valid refile target: {to}")
            target = targets[0]
        else:
            target, error = self.find_category(category)
            if target is None:
                return Result(False, error)
        effects = []
        self.sibling_group(node).remove(node)
        target.children.append(node)
        moved_state = node.keyword
        if moved_state == "NEXT":
            # Invariant repairs at the destination (I3, I6).
            freestanding = not self.task_ancestors(node)
            duplicate_next = any(
                sibling is not node and sibling.keyword == "NEXT"
                for sibling in target.children)
            if freestanding or duplicate_next:
                node.keyword = "TODO"
                node.logbook += 1
                effects.append(SideEffect(
                    "state-change", node.heading, "NEXT", "TODO"))
        if self.is_task(target) and target.keyword == "NEXT":
            # A NEXT target parent that just became a project → TODO,
            # re-placed in its own sibling group (§4.1 NEXT-exit rule,
            # demotions included).
            target.keyword = "TODO"
            target.logbook += 1
            effects.append(SideEffect(
                "state-change", target.heading, "NEXT", "TODO"))
            self._reorder(self.sibling_group(target), target,
                          old_keyword="NEXT")
        # §4.8/§4.1 arrival rule: the moved subtree enters the
        # destination group at the end of its (post-demotion) zone.
        self._reorder(target.children, node)
        return Result(True, new_state=node.keyword, side_effects=effects)

    # ── §4.9 move ─────────────────────────────────────────────────────

    def move(self, substring, direction=None, anchor=None):
        """§4.9: explicit user reordering within one sibling group."""
        node, error = self.find(substring)
        if node is None:
            return Result(False, error)
        group = self.sibling_group(node)
        index = group.index(node)
        if direction == "up":
            new_index = index - 1
        elif direction == "down":
            new_index = index + 1
        else:
            anchor_node, error = self.find(anchor)
            if anchor_node is None:
                return Result(False, error)
            if anchor_node not in group:
                return Result(False, "anchor is not a sibling")
            anchor_index = group.index(anchor_node)
            new_index = (anchor_index if direction == "before"
                         else anchor_index + 1)
            if anchor_index > index:
                new_index -= 1
        if not 0 <= new_index < len(group):
            return Result(False, "cannot move past the group edge")
        candidate = list(group)
        candidate.remove(node)
        candidate.insert(new_index, node)
        if self.is_uniform(group) and not self._zone_move_ok(candidate, node):
            # I5: a move that would cross a zone boundary is rejected.
            return Result(False, "move would cross a zone boundary")
        group[:] = candidate
        return Result(True, new_state=node.keyword)

    @staticmethod
    def _zone_move_ok(candidate, node):
        """§4.9 (#47): the moved entry may not cross a zone boundary.

        Checks the resulting order CANDIDATE for an I5 violation
        *involving NODE* — the completed block above it, the DEFER block
        below it, and the NEXT prefix at the top of the active zone.
        Moved-entry-relative (olli ruling 2026-08-07): the other
        siblings' relative order is untouched by a move, so on a state
        satisfying I5 any new violation involves the mover, while on an
        already-broken group pre-existing violations neither block the
        move nor get repaired — repair-by-move stays possible.  The
        whole-order `_zone_order_ok` remains the I5 checker for
        generated states.
        """
        idx = candidate.index(node)
        above, below = candidate[:idx], candidate[idx + 1:]
        zone = zone_of(node.keyword)
        if zone == ZONE_COMPLETED:
            return all(zone_of(n.keyword) == ZONE_COMPLETED for n in above)
        if zone == ZONE_DEFER:
            return all(zone_of(n.keyword) == ZONE_DEFER for n in below)
        # Active zone: between the two blocks, and behind the NEXT prefix.
        if any(zone_of(n.keyword) == ZONE_COMPLETED for n in below):
            return False
        if any(zone_of(n.keyword) == ZONE_DEFER for n in above):
            return False
        if node.keyword == "NEXT":
            return not any(zone_of(n.keyword) == ZONE_ACTIVE
                           and n.keyword != "NEXT" for n in above)
        return not any(n.keyword == "NEXT" for n in below)

    @staticmethod
    def _zone_order_ok(group):
        """I5: completed block, then active zone (NEXT prefix), then DEFER."""
        zones = [zone_of(n.keyword) for n in group]
        if zones != sorted(zones):
            return False
        active = [n for n in group if zone_of(n.keyword) == ZONE_ACTIVE]
        seen_non_next = False
        for n in active:
            if n.keyword == "NEXT":
                if seen_non_next:
                    return False
            else:
                seen_non_next = True
        return True

    # ── §4.10 annotation operations (state-neutral) ───────────────────

    def set_priority(self, substring, priority):
        """§4.10/§3: only 'A' or clear; anything else rejected."""
        node, error = self.find(substring)
        if node is None:
            return Result(False, error)
        if priority not in (None, "A") and not self.div.d7_no_priority_rules:
            return Result(False, "only [#A] or clear (§3)")
        node.priority = priority
        return Result(True, new_state=node.keyword)

    def rename(self, substring, new_heading):
        node, error = self.find(substring)
        if node is None:
            return Result(False, error)
        node.heading = new_heading
        return Result(True, new_state=node.keyword)

    # ── §6 invariant checks (tier-1 oracle) ───────────────────────────

    def check_invariants(self):
        """Return a list of invariant-violation strings (empty = valid).

        Structural invariants only — the ones expressible as predicates
        over a single state (I1-I5 and the NEXT half of I3). The
        operational invariants (I6-I10, I12) are asserted as properties
        over transitions in the tier-1 tests.
        """
        errors = []
        for node in self.all_nodes():
            if node.keyword is not None and node.keyword not in KEYWORDS:
                errors.append(f"I2: unknown keyword on {node.heading!r}")
            if node.keyword == "NEXT":
                # I3: NEXT only inside a project, never on a project
                # heading, never on a lone task.
                if not self.is_project_child(node) or self.is_project(node):
                    errors.append(f"I3: NEXT on {node.heading!r}")
            if node.keyword == "WAITING" and self.is_project(node):
                errors.append(f"§3: WAITING on project {node.heading!r}")
            if node.keyword in CLOSED_STATES and self.is_project(node):
                open_desc = [n for n in self.task_descendants(node)
                             if n.keyword not in CLOSED_STATES]
                if open_desc:
                    errors.append(f"I4: closed project {node.heading!r} "
                                  f"with open descendants")
            group = node.children
            if group and self.is_uniform(group):
                if not self._zone_order_ok(group):
                    errors.append(f"I5: zone order under {node.heading!r}")
        if self.roots and self.is_uniform(self.roots):
            if not self._zone_order_ok(self.roots):
                errors.append("I5: zone order at top level")
        return errors

    # ── org-text projection (tier-2 harness only) ─────────────────────

    def to_org_text(self):
        """Serialize the forest to org text the CLI can operate on.

        A node carrying §4.6 WAITING state also gets a `:PROPERTIES:`
        drawer: `:REASON:`, and the org-depend link pair. The model has
        no ids, so a node's *heading* is used as its `:ID:` — org ids are
        opaque strings, and headings are already globally unique here, so
        the CLI reads exactly the links the model holds.
        """
        lines = []
        def emit(node, level):
            keyword = f"{node.keyword} " if node.keyword else ""
            priority = f"[#{node.priority}] " if node.priority else ""
            tags = f" :{':'.join(node.tags)}:" if node.tags else ""
            lines.append(f"{'*' * level} {keyword}{priority}"
                         f"{node.heading}{tags}")
            props = []
            if node.blockers or node.triggers:
                props.append(("ID", node.heading))
            if node.waiting_reason:
                props.append(("REASON", node.waiting_reason))
            if node.blockers:
                props.append(("BLOCKER", " ".join(node.blockers)))
            if node.triggers:
                props.append(("TRIGGER",
                              " ".join(f"{h}(TODO)" for h in node.triggers)))
            if props:
                lines.append(":PROPERTIES:")
                lines.extend(f":{key}: {value}" for key, value in props)
                lines.append(":END:")
            for child in node.children:
                emit(child, level + 1)
        for root in self.roots:
            emit(root, 1)
        return "\n".join(lines) + "\n" if lines else ""

    def skeleton(self):
        """The comparison surface: (depth, heading, keyword, priority)
        in document order. LOGBOOK contents, timestamps, and bodies are
        deliberately excluded."""
        out = []
        def walk(children, depth):
            for child in children:
                out.append((depth, child.heading, child.keyword,
                            child.priority))
                walk(child.children, depth + 1)
        walk(self.roots, 0)
        return out


_HEADING_RE = re.compile(
    r"^(\*+)\s+(?:(" + "|".join(KEYWORDS) + r")\s+)?"
    r"(?:\[#([A-Z0-9])\]\s+)?"
    r"(.*?)(?:\s+:([A-Za-z0-9_@#%:]+):)?\s*$")


def parse_org_text(text):
    """Parse org text back into a forest of Nodes (skeleton fields only).

    Recognizes heading lines; ignores planning lines, drawers (LOGBOOK,
    PROPERTIES), and body text — the model does not compare them.
    """
    roots = []
    stack = []  # (level, node)
    for line in text.splitlines():
        if not line.startswith("*"):
            continue
        match = _HEADING_RE.match(line)
        if not match:
            continue
        stars, keyword, priority, heading, tags = match.groups()
        node = Node(heading.strip(), keyword, priority,
                    tuple(tags.split(":")) if tags else ())
        level = len(stars)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots
