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
  one named flag per applicable §7 row (``d1_full_sort`` = §7 row 1, and
  so on). The tier-2 conformance harness runs the model in this mode and
  expects an exact match against the real CLI; per-row expected-failure
  tests run the normative mode and flip green as each stage-2c fix lands
  (2026-07-31 ruling on #45).

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

# §4.1 divergence (§7 row 1): today's reorder is a full stable sort by
# this rank. The normative primitive (minimal move) never uses it.
CURRENT_SORT_RANK = {
    "DONE": 0, "CANCELLED": 0, "NEXT": 1, "TODO": 2, "WAITING": 3, "DEFER": 4,
}

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
    logbook: int = 0  # I10: count of state-change records
    children: list = field(default_factory=list)

    def clone(self):
        return replace(
            self, tags=tuple(self.tags),
            children=[c.clone() for c in self.children])


@dataclass
class SideEffect:
    """§4.0: one machine-made change beyond the addressed heading."""

    action: str  # "state-change" | "project-needs-review"
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

    d1_full_sort: bool = False          # §7 row 1 → #34
    # §7 row 2 (#34) has no flag: the recorded divergence (refile not
    # placing arrivals in the destination's zones) does NOT reproduce on
    # 2026-07-31 master — the destination reorder runs unconditionally.
    # Row flagged to olli for re-examination when #34 lands.
    d3_scan_from_closed: bool = False   # §7 row 3 → #38
    d4_no_subproject_review: bool = False   # §7 row 4 → #38
    d5_no_waiting_reason: bool = False  # §7 row 5 → #39
    d7_no_priority_rules: bool = False  # §7 row 7 → #41
    d8_lax_state_guards: bool = False   # §7 row 8 → #46
    d9_move_unguarded: bool = False     # §7 row 9 → #37/#47
    # Observed divergence with no §7 row yet (PARKED for ruling
    # 2026-07-31): set-next on a *closed* leaf is rejected by the CLI,
    # while §4.7 ("same guard as set-state NEXT") implies acceptance.
    dx_setnext_rejects_closed_leaf: bool = False

    @classmethod
    def normative(cls):
        return cls()

    @classmethod
    def current(cls):
        return cls(d1_full_sort=True,
                   d3_scan_from_closed=True, d4_no_subproject_review=True,
                   d5_no_waiting_reason=True, d7_no_priority_rules=True,
                   d8_lax_state_guards=True, d9_move_unguarded=True,
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
        """§4.1: re-sort the changed task's sibling group.

        Normative: minimal move — the changed task moves to its zone
        boundary; nothing else moves, and a task whose boundary class
        did not change keeps its position (§2: sibling order is user
        data). OLD_KEYWORD is the keyword before the change; None means
        the task newly arrived in this group (add/refile) and is placed
        regardless. Current (§7 row 1): full stable sort by
        CURRENT_SORT_RANK. Mixed groups: never reordered (§2).
        """
        if not self.is_uniform(group):
            return
        if self.div.d1_full_sort:
            group.sort(key=lambda n: CURRENT_SORT_RANK[n.keyword])
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
        zone = zone_of(changed.keyword)
        if zone == ZONE_COMPLETED:
            # Closed → bottom of the completed block (top zone).
            index = 0
            while (index < len(group)
                   and zone_of(group[index].keyword) == ZONE_COMPLETED):
                index += 1
        elif changed.keyword == "NEXT":
            # NEXT → top of the active zone (§2: NEXT always at the top).
            index = 0
            while (index < len(group)
                   and zone_of(group[index].keyword) == ZONE_COMPLETED):
                index += 1
        elif changed.keyword == "DEFER":
            # DEFER → top of the DEFER block.
            index = 0
            while (index < len(group)
                   and zone_of(group[index].keyword) != ZONE_DEFER):
                index += 1
        else:
            # TODO/WAITING have no boundary of their own (§4.1).
            if old_keyword == "NEXT":
                # Demotion within the active zone: the minimal
                # I5-preserving move — just past the remaining NEXT
                # prefix, i.e. in place unless a NEXT sibling would end
                # up below it.
                index = 0
                while (index < len(group)
                       and zone_of(group[index].keyword) == ZONE_COMPLETED):
                    index += 1
                while index < len(group) and group[index].keyword == "NEXT":
                    index += 1
                group.insert(index, changed)
                return
            # True arrivals (old_keyword None: add/refile) and
            # cross-zone reopens are placed at the end of the active
            # zone.
            group.insert(self._active_zone_end(group), changed)
            return
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

        Returns the side-effect list. Steps 1-3 as specified; §7 rows
        3-4 switch the divergent scan behavior.
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
        # Step 3 — scan for the first open TODO candidate.
        if self.div.d3_scan_from_closed:
            # §7 row 3: forward from the closed task only.
            start = group.index(closed_child) + 1
            candidates = group[start:]
        else:
            # Normative: the whole group in document order (I7).
            candidates = group
        for candidate in candidates:
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
            # PARKED: direct children here vs descendants in active()
            # above — with a category heading inside the subproject, an
            # all-done-but-open subproject is passed silently. §4.5 does
            # not pin the scope; awaiting olli's ruling.
            subproject_tasks = [c for c in candidate.children
                                if self.is_task(c)]
            if subproject_tasks and all(c.keyword in CLOSED_STATES
                                        for c in subproject_tasks):
                # All-done-but-open subproject → emit review, continue.
                if not self.div.d4_no_subproject_review:  # §7 row 4
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
                if not self.div.d1_full_sort:
                    # §2: a task entering NEXT takes the top of the
                    # active zone. Today's CLI never re-sorts the drilled
                    # subproject's group (row-1 reorder discipline).
                    self._reorder(candidate.children, promoted,
                                  old_keyword="TODO")
                return effects
        return effects

    # ── §4.2 add-task ─────────────────────────────────────────────────

    def add_task(self, heading, state="TODO", category=None, priority=None):
        """§4.2: file a freestanding task; never NEXT (I3)."""
        if state == "NEXT":
            return Result(False, "NEXT is only valid inside a project")
        if state not in KEYWORDS:
            return Result(False, f"Unknown state: {state}")
        node = Node(heading, state, priority=priority)
        if category is None:
            self.roots.append(node)
            return Result(True, new_state=state)
        target, error = self.find_category(category)
        if target is None:
            return Result(False, error)
        target.children.append(node)
        return Result(True, new_state=state)

    # ── §4.3 add-subtask ──────────────────────────────────────────────

    def add_subtask(self, parent_substr, heading, state="TODO"):
        """§4.3: append a child task as last direct child."""
        parent, error = self.find(parent_substr)
        if parent is None:
            return Result(False, error)
        if not self.is_task(parent):
            # Category headings never match as add-subtask parents.
            return Result(False, f"Not a task: {parent_substr}")
        if state not in KEYWORDS:
            return Result(False, f"Unknown state: {state}")
        effects = []
        node = Node(heading, state)
        parent.children.append(node)
        if parent.keyword == "NEXT":
            # A project heading is never NEXT (I3): demote, side effect.
            parent.keyword = "TODO"
            parent.logbook += 1
            effects.append(SideEffect(
                "state-change", parent.heading, "NEXT", "TODO"))
        if state == "NEXT" or state in CLOSED_STATES:
            # §4.3: reorder only when the created state ranks above TODO.
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
        effects = self._promotion_rule(node)
        self._reorder(self.sibling_group(node), node,
                      old_keyword=old_state)
        return Result(True, old_state=old_state, new_state=new_state,
                      side_effects=effects)

    # ── §4.6 set-state ────────────────────────────────────────────────

    def set_state(self, substring, new_state, reason=None):
        """§4.6: change a keyword, nothing more — no promotion (I9)."""
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
            if self.div.d8_lax_state_guards:
                # §7 row 8: today the guard only demands a task ancestor,
                # so a *subproject heading* is (wrongly) admitted.
                if not self.task_ancestors(node):
                    return Result(False, "NEXT is only valid inside a project")
            else:
                if not (self.is_project_child(node)
                        and self.is_leaf_task(node)):
                    return Result(False, "NEXT is only valid on a project child leaf")
        if new_state == "WAITING":
            if not self.div.d8_lax_state_guards and self.is_project(node):
                # §3: WAITING requires a leaf; project headings rejected.
                return Result(False, "WAITING is not valid on a project heading")
            if not self.div.d5_no_waiting_reason and not reason:
                # §7 row 5 → #39: reason or blocker link required at entry.
                return Result(False, "WAITING requires a reason")
        if new_state in CLOSED_STATES and self.is_project(node):
            open_descendants = [n for n in self.task_descendants(node)
                                if n.keyword not in CLOSED_STATES]
            if open_descendants:
                if self.div.d8_lax_state_guards:
                    # §7 row 8: today this silently no-ops, reporting
                    # success with the keyword unchanged.
                    return Result(True, old_state=old_state,
                                  new_state=new_state)
                return Result(False, "blocked by an incomplete subtask",
                              old_state=old_state)
        node.keyword = new_state
        node.logbook += 1  # I10
        if reason and new_state == "WAITING":
            node.waiting_reason = reason
        # §4.6/§4.1: reorder unless the task entered WAITING from
        # TODO/NEXT (it keeps its position — §2 zones).
        if not (new_state == "WAITING" and old_state in ("TODO", "NEXT")):
            self._reorder(self.sibling_group(node), node,
                          old_keyword=old_state)
        return Result(True, old_state=old_state, new_state=new_state)

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
                if self.is_project(child) and not self.div.d8_lax_state_guards:
                    # I3 (§7 row 8): subproject headings are never
                    # promoted — but today's project path takes them.
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
        if self.div.d8_lax_state_guards:
            if not self.task_ancestors(node):
                return Result(False, "NEXT is only valid inside a project")
        elif not self.is_project_child(node):
            return Result(False, "NEXT is only valid inside a project")
        old_state = node.keyword
        node.keyword = "NEXT"
        node.logbook += 1
        if not self.div.d1_full_sort:
            # §2: entering NEXT takes the top of the active zone; the
            # CLI's leaf set-next never reorders (row-1 discipline).
            self._reorder(self.sibling_group(node), node,
                          old_keyword=old_state)
        return Result(True, old_state=old_state, new_state="NEXT")

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
            # A NEXT target parent that just became a project → TODO.
            target.keyword = "TODO"
            target.logbook += 1
            effects.append(SideEffect(
                "state-change", target.heading, "NEXT", "TODO"))
        # §4.8: the destination group is reordered; a surviving NEXT
        # takes the top of the active zone (§2). Today's CLI does this
        # too (the §7 row 2 divergence does not reproduce — see the
        # Divergences docstring); only the sort discipline differs (d1).
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
        if (self.is_uniform(group) and not self.div.d9_move_unguarded
                and not self._zone_order_ok(candidate)):
            # I5: a move that would cross a zone boundary is rejected.
            return Result(False, "move would cross a zone boundary")
        group[:] = candidate
        return Result(True, new_state=node.keyword)

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
        """Serialize the forest to org text the CLI can operate on."""
        lines = []
        def emit(node, level):
            keyword = f"{node.keyword} " if node.keyword else ""
            priority = f"[#{node.priority}] " if node.priority else ""
            tags = f" :{':'.join(node.tags)}:" if node.tags else ""
            lines.append(f"{'*' * level} {keyword}{priority}"
                         f"{node.heading}{tags}")
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
