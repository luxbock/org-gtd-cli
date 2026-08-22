"""Tier 1 (issue #45): fast Hypothesis properties against the reference
model alone — no Emacs, no subprocess, runs in seconds.

The model under test is gtd_reference_model.Model in NORMATIVE mode
(SEMANTICS.md exactly). Properties assert the §6 invariants: the
structural ones (I1-I5) via Model.check_invariants() after every
operation, the operational ones (I6, I8, I9, I12) as explicit
transition properties.

Profiles: see conftest.py — 'fast' (default) vs 'thorough'
(ORG_GTD_TEST_PROFILE=thorough).
"""

import itertools

import pytest
from hypothesis import assume, given, settings, strategies as st

from gtd_reference_model import (
    CLOSED_STATES, Divergences, Model, Node, parse_org_text, zone_of,
)

# ---------------------------------------------------------------------------
# Strategies: valid GTD states by construction
# ---------------------------------------------------------------------------

# Keywords legal per structural position (§3 legality matrix). These
# describe what may legally be *on disk*, WAITING included — a WAITING
# task written by Emacs is legal everywhere (§4.6's tolerance).
LONE_TASK_STATES = ("TODO", "WAITING", "DEFER", "DONE", "CANCELLED")
LEAF_CHILD_STATES = ("TODO", "NEXT", "WAITING", "DEFER", "DONE", "CANCELLED")
PROJECT_HEADING_STATES = ("TODO", "DEFER")  # closed handled post-hoc (I4)

# What `add-task` / `add-subtask` may *create* (§4.2/§4.3, #39): create
# never mints WAITING — its guardrail lives on the `set-state` entry, so
# a generated `--state WAITING` create would only ever exercise the
# rejection. Kept separate from the on-disk sets above rather than
# narrowing them, so forests still generate WAITING freely.
CREATE_LONE_STATES = tuple(s for s in LONE_TASK_STATES if s != "WAITING")
CREATE_CHILD_STATES = tuple(s for s in LEAF_CHILD_STATES if s != "WAITING")


def _zone_sort(group):
    """Repair a uniform group into legal zone order (I5), preserving the
    relative order inside each zone and putting NEXT first in the active
    zone. Used only to make *generated* states legal."""
    if not all(n.keyword is not None for n in group):
        return group
    completed = [n for n in group if zone_of(n.keyword) == 0]
    active = [n for n in group if zone_of(n.keyword) == 1]
    defer = [n for n in group if zone_of(n.keyword) == 2]
    nexts = [n for n in active if n.keyword == "NEXT"]
    rest = [n for n in active if n.keyword != "NEXT"]
    return completed + nexts + rest + defer


@st.composite
def forests(draw, max_depth=3, max_width=4):
    """Generate a valid state object: a forest satisfying §2/§3/§6.

    Headings are globally unique short slugs so CLI substring addressing
    (§4.0) is unambiguous — mirrors how tier 2 names tasks.
    """
    counter = itertools.count()

    def build_node(depth, must_be_task):
        name = f"t{next(counter):03d}"
        want_children = depth < max_depth and draw(
            st.integers(0, 3)) == 0  # ~25% of nodes get children
        children = []
        if want_children:
            width = draw(st.integers(1, max_width))
            # A category child forest may mix tasks and categories; keep
            # groups uniform ~75% of the time so zones exist (§2).
            uniform = draw(st.booleans() | st.just(True))
            for _ in range(width):
                children.append(build_node(
                    depth + 1, must_be_task=uniform))
        is_task = must_be_task or draw(st.booleans())
        if not is_task:
            return Node(name, None, children=children)
        has_task_child = any(c.keyword is not None for c in children)
        if has_task_child:
            keyword = draw(st.sampled_from(PROJECT_HEADING_STATES))
        else:
            keyword = draw(st.sampled_from(LONE_TASK_STATES))
        return Node(name, keyword, children=children)

    width = draw(st.integers(1, max_width))
    roots = [build_node(0, must_be_task=False) for _ in range(width)]
    model = Model(roots, Divergences.normative())

    # Legalize: NEXT is only assignable to project-child leaves (I3);
    # give some legal spots a NEXT, at most one per sibling group (I6 is
    # about minting, but generated states keep the common one-front shape
    # and hand-declared parallels come from ops).
    for node in model.all_nodes():
        if not (model.is_task(node) and node.children):
            continue
        leaves = [c for c in node.children
                  if model.is_leaf_task(c) and c.keyword == "TODO"]
        group_active = [c for c in node.children
                        if c.keyword in ("NEXT", "WAITING")]
        if leaves and not group_active and draw(st.booleans()):
            leaves[0].keyword = "NEXT"

    # Zone-repair every uniform group (I5) and satisfy I4 by reopening
    # any closed heading that turned out to be a project with open work.
    def repair(children):
        for child in children:
            repair(child.children)
            child.children[:] = _zone_sort(child.children)
        return children
    repair(model.roots)
    model.roots[:] = _zone_sort(model.roots)
    for node in model.all_nodes():
        if node.keyword in CLOSED_STATES and model.is_project(node):
            if any(n.keyword not in CLOSED_STATES
                   for n in model.task_descendants(node)):
                node.keyword = "TODO"
    assume(not model.check_invariants())
    return model


@st.composite
def operations(draw, model):
    """One random operation against a node picked from the model."""
    nodes = model.all_nodes()
    assume(nodes)
    node = draw(st.sampled_from(nodes))
    op = draw(st.sampled_from(
        ["set_done", "set_cancelled", "set_state", "set_next",
         "add_subtask", "add_task", "move", "refile"]))
    if op in ("set_done", "set_cancelled"):
        return (op, (node.heading,), {})
    if op == "set_state":
        state = draw(st.sampled_from(
            ("TODO", "NEXT", "WAITING", "DEFER", "DONE", "CANCELLED")))
        kwargs = {"reason": "because"} if state == "WAITING" else {}
        return (op, (node.heading, state), kwargs)
    if op == "set_next":
        return (op, (node.heading,), {})
    if op == "add_subtask":
        state = draw(st.sampled_from(CREATE_CHILD_STATES))
        return (op, (node.heading, f"new{draw(st.integers(0, 999)):03d}",
                     state), {})
    if op == "add_task":
        state = draw(st.sampled_from(CREATE_LONE_STATES))
        return (op, (f"new{draw(st.integers(0, 999)):03d}", state), {})
    if op == "move":
        direction = draw(st.sampled_from(("up", "down")))
        return (op, (node.heading,), {"direction": direction})
    target = draw(st.sampled_from(nodes))
    return ("refile", (node.heading,), {"to": target.heading})


@st.composite
def models_and_ops(draw, max_ops=6):
    model = draw(forests())
    ops = []
    for _ in range(draw(st.integers(1, max_ops))):
        ops.append(draw(operations(model)))
    return model, ops


# ---------------------------------------------------------------------------
# The main preservation property
# ---------------------------------------------------------------------------

def hits_parked_spec_gap(model, op, args, kwargs):
    """PARKED SPEC QUESTIONS — none are open (kept as the routing hook).

    Gap families where a documented-legal operation produced a state the
    §3 matrix / §6 invariants forbid used to be routed around here, so
    the preservation properties could stay green while the gap waited
    for a ruling. Every family recorded here has since been closed, so
    the properties now run against every generated shape:

    - the append-last, demotion-unplaced and WAITING-above-the-NEXT-
      prefix families: resolved by the §4.1 arrival/NEXT-exit rules
      (#34);
    - **§4.5 step-3 scope** (retired 2026-08-11, #58): §2 severing pins
      the scope on both sides — the activity test scans *task*
      descendants, which stop at a category heading, and the
      all-done-but-open test scans direct task children, so at the
      subproject's own level the two see the same set;
    - **I4 outside closure** and **Leaf→project WAITING repair
      missing** (retired 2026-08-11, #56, §7 rows 10-11): the §4.0
      closure repair now reopens a closed ancestor chain around an open
      arrival, and the keyword-outgrown repair demotes a WAITING parent
      that gains its first task child. Their witnesses are the two
      deterministic tests below.
    """
    return False


@given(models_and_ops())
def test_invariants_preserved_by_any_operation_sequence(model_and_ops):
    """§6: a valid state stays valid under every (attempted) operation.

    Operations are free to fail — a rejection must leave the state
    untouched (I12) — but no accepted operation may break I1-I5.
    """
    model, ops = model_and_ops
    for op, args, kwargs in ops:
        if hits_parked_spec_gap(model, op, args, kwargs):
            continue
        before = model.skeleton()
        result = getattr(model, op)(*args, **kwargs)
        if not result.ok:
            assert model.skeleton() == before, (
                f"I12 violated: failed {op}{args} mutated the state")
        violations = model.check_invariants()
        assert not violations, (
            f"after {op}{args}: {violations}")


def test_closure_repair_keeps_i4_outside_closure():
    """§4.0 closure repair (#56, was the parked I4-outside-closure gap).

    The former witness: add-subtask of an open child under a DONE leaf
    used to yield a closed project with open descendants. The arrival
    now reopens the ancestor and reports it.
    """
    model = Model([Node("done-leaf", "DONE")])
    result = model.add_subtask("done-leaf", "open child", "TODO")
    assert result.ok
    assert model.find("done-leaf")[0].keyword == "TODO"
    assert [(e.action, e.heading, e.old_state, e.new_state)
            for e in result.side_effects] == [
        ("state-change", "done-leaf", "DONE", "TODO")]
    assert not model.check_invariants()


def test_closure_repair_cascades_the_whole_closed_chain():
    """§4.0: *each* closed ancestor reopens, nearest first, and a
    category heading severs the chain (§2) — the outer DONE heading
    above one is left closed."""
    model = Model([Node("cat", None, children=[
        Node("outer", "DONE", children=[
            Node("inner", "CANCELLED")])])])
    result = model.set_state("inner", "TODO")
    assert result.ok
    assert [(e.action, e.heading, e.old_state, e.new_state)
            for e in result.side_effects] == [
        ("state-change", "outer", "DONE", "TODO")]
    assert not model.check_invariants()


def test_waiting_parent_demotes_when_it_grows_a_task_child():
    """§4.0 keyword-outgrown repair (#56, was the parked WAITING gap):
    state-change + blocker-link-removed + project-needs-review."""
    model = Model([Node("blocker", "TODO", triggers=("waiter",)),
                   Node("waiter", "WAITING", waiting_reason="held",
                        blockers=("blocker",))])
    result = model.add_subtask("waiter", "child", "TODO")
    assert result.ok
    waiter = model.find("waiter")[0]
    assert waiter.keyword == "TODO"
    assert waiter.waiting_reason is None and waiter.blockers == ()
    assert model.find("blocker")[0].triggers == ()
    assert [(e.action, e.heading) for e in result.side_effects] == [
        ("state-change", "waiter"),
        ("blocker-link-removed", "blocker"),
        ("project-needs-review", "waiter")]
    assert not model.check_invariants()


def test_waiting_parent_without_a_blocker_link_reports_two_effects():
    """§4.6: no link to unwind → no spurious ``blocker-link-removed``."""
    model = Model([Node("waiter", "WAITING", waiting_reason="held")])
    result = model.add_subtask("waiter", "child", "TODO")
    assert result.ok
    assert [(e.action, e.heading) for e in result.side_effects] == [
        ("state-change", "waiter"), ("project-needs-review", "waiter")]


def test_set_next_reopens_a_closed_project_child_leaf():
    """§4.7: a closed *project child* is accepted and reopens straight
    to NEXT, cascading its closed ancestors."""
    model = Model([Node("proj", "DONE", children=[Node("aa", "DONE")])])
    result = model.set_next("aa")
    assert result.ok, result.error
    assert result.old_state == "DONE" and result.new_state == "NEXT"
    assert [(e.action, e.heading, e.old_state, e.new_state)
            for e in result.side_effects] == [
        ("state-change", "proj", "DONE", "TODO")]
    assert not model.check_invariants()


def test_set_next_still_rejects_a_closed_lone_task():
    """§4.7/I3: a *lone* task never takes NEXT, closed or open."""
    model = Model([Node("lone", "DONE")])
    before = model.skeleton()
    result = model.set_next("lone")
    assert result.ok is False
    assert model.skeleton() == before  # I12


def test_refile_repairs_a_closed_and_waiting_destination():
    """§4.8: an open subtree arriving under a WAITING leaf demotes it
    (with the §4.6 cleanup) and reopens the closed chain above it."""
    model = Model([
        Node("blocker", "TODO", triggers=("dest",)),
        Node("outer", "DONE", children=[
            Node("dest", "WAITING", waiting_reason="held",
                 blockers=("blocker",))]),
        Node("mover", "TODO"),
    ])
    result = model.refile("mover", to="dest")
    assert result.ok, result.error
    assert [(e.action, e.heading) for e in result.side_effects] == [
        ("state-change", "dest"),
        ("blocker-link-removed", "blocker"),
        ("project-needs-review", "dest"),
        ("state-change", "outer")]
    assert not model.check_invariants()


@given(models_and_ops())
def test_failed_match_mutates_nothing(model_and_ops):
    """I12 via the addressing layer: a miss or ambiguity changes nothing."""
    model, _ = model_and_ops
    for miss in ("no-such-heading-xyz", "t"):  # "t" ambiguous when >1 node
        for op in (lambda: model.set_done(miss),
                   lambda: model.set_state(miss, "TODO"),
                   lambda: model.refile(miss, to="also-missing")):
            before = model.skeleton()
            result = op()
            if not result.ok:
                assert model.skeleton() == before


# ---------------------------------------------------------------------------
# Response integrity (#46, A18)
# ---------------------------------------------------------------------------

@given(models_and_ops())
def test_reported_new_state_is_actually_reached(model_and_ops):
    """§4.0 response integrity: a reported ``new_state`` implies the
    state actually changed.

    Generalizes the #46 symptom — ``set-state DONE`` on a blocked
    project reporting ``"new_state": "DONE"`` while the keyword stayed
    TODO. No operation may report a state it did not reach.

    The addressed node is resolved by *identity*: the creating ops
    report the new child's state, and generated sequences may create
    two same-named siblings, which the substring addressing layer
    (§4.0) then reports as ambiguous — an addressing question, not a
    response-integrity one.
    """
    model, ops = model_and_ops
    for op, args, kwargs in ops:
        if hits_parked_spec_gap(model, op, args, kwargs):
            continue
        before = {id(n) for n in model.all_nodes()}
        result = getattr(model, op)(*args, **kwargs)
        if not (result.ok and result.new_state):
            continue
        if op in ("add_task", "add_subtask"):
            created = [n for n in model.all_nodes() if id(n) not in before]
            assert len(created) == 1, f"{op}{args} created {len(created)} nodes"
            node = created[0]
        else:
            node, error = model.find(args[0])
            if node is None:
                continue  # ambiguous/missing addressing — I12's business
        assert node.keyword == result.new_state, (
            f"{op}{args} reported new_state={result.new_state!r} but "
            f"{node.heading!r} is {node.keyword!r}")


def test_blocked_close_via_set_state_is_not_reported_as_success():
    """The #46 witness, deterministic: a project with an open task
    descendant cannot be closed through ``set-state`` — and the failure
    is reported as one (§7 row 8, retired 2026-08-10)."""
    model = Model([Node("proj", "TODO", children=[Node("aa", "TODO")])])
    before = model.skeleton()
    for state in ("DONE", "CANCELLED"):
        result = model.set_state("proj", state)
        assert result.ok is False
        assert result.new_state is None
        assert model.skeleton() == before  # I12


def test_set_state_legality_guards_reject_project_headings():
    """§3 matrix: NEXT needs a project-child leaf, WAITING a leaf — a
    project or subproject *heading* takes neither (I3)."""
    model = Model([Node("proj", "TODO", children=[
        Node("sub", "TODO", children=[Node("s1", "TODO")]),
        Node("a1", "TODO"),
    ])])
    before = model.skeleton()
    for target, state in (("sub", "NEXT"), ("proj", "NEXT"),
                          ("proj", "WAITING"), ("sub", "WAITING")):
        result = model.set_state(target, state, reason="because")
        assert result.ok is False, f"set_state({target}, {state}) accepted"
        assert model.skeleton() == before  # I12
    # The leaf still takes both.
    assert model.set_state("a1", "NEXT").ok
    assert model.set_state("a1", "WAITING", reason="because").ok


def test_set_state_close_runs_the_close_post_conditions():
    """§4.6: a close driven through set-state is a genuine close — the
    §4.4 post-conditions run (LOGBOOK stamp, reorder into the completed
    block) but the §4.5 promotion rule does not (I9)."""
    model = Model([Node("proj", "TODO", children=[
        Node("aa", "NEXT"), Node("bb", "TODO"),
    ])])
    result = model.set_state("aa", "DONE")
    assert result.ok and result.new_state == "DONE"
    aa, _ = model.find("aa")
    assert aa.logbook == 1  # I10
    # I9: no promotion — bb stays TODO and nothing is reported.
    assert result.side_effects == []
    assert model.find("bb")[0].keyword == "TODO"
    # §4.1: aa sits in the completed block, at the top of the group.
    assert [h for _, h, _, _ in model.skeleton()] == ["proj", "aa", "bb"]


def test_set_state_close_strips_priority_like_close_does():
    """A13: the model's set_state close path strips the cookie exactly as
    ``_close`` does — in both modes, since #41 (§7 row 7) retired
    ``d7_no_priority_rules`` and the two modes now agree here."""
    for divs in (Divergences.normative(), Divergences.current()):
        model = Model([Node("proj", "TODO", children=[
            Node("aa", "TODO", priority="A")])], divs)
        # Close the leaf so the project is not blocked.
        assert model.set_state("aa", "DONE").ok
        assert model.find("aa")[0].priority is None
        model = Model([Node("proj", "TODO", children=[
            Node("aa", "TODO", priority="A")])], divs)
        assert model.set_done("aa").ok
        assert model.find("aa")[0].priority is None


def test_set_priority_admits_only_a_or_clear():
    """§3/§4.10 (#41, §7 row 7): anything but A or clear is rejected and
    leaves the node's cookie untouched, in both divergence modes."""
    for divs in (Divergences.normative(), Divergences.current()):
        model = Model([Node("lone", "TODO", priority="A")], divs)
        for bad in ("B", "C"):
            assert model.set_priority("lone", bad).ok is False
            assert model.find("lone")[0].priority == "A"
        assert model.set_priority("lone", None).ok
        assert model.find("lone")[0].priority is None
        assert model.set_priority("lone", "A").ok
        assert model.find("lone")[0].priority == "A"


# ---------------------------------------------------------------------------
# Promotion-rule properties (§4.5)
# ---------------------------------------------------------------------------

@given(models_and_ops())
def test_promotion_never_mints_a_second_front(model_and_ops):
    """I6: closing a task whose sibling group already has a NEXT or
    WAITING sibling produces no promotion side effect."""
    model, _ = model_and_ops
    for node in list(model.all_nodes()):
        if not (model.is_project_child(node)
                and node.keyword == "TODO"
                and not model.is_project(node)):
            continue
        group = model.sibling_group(node)
        had_front = any(s is not node and s.keyword in ("NEXT", "WAITING")
                        for s in group if model.is_task(s))
        result = model.set_done(node.heading)
        if not result.ok:
            continue
        promotions = [e for e in result.side_effects
                      if e.action == "state-change" and e.new_state == "NEXT"]
        if had_front:
            assert not promotions, (
                f"promoted {promotions} although the group had a front")
        assert len(promotions) <= 1, "promotion promoted more than one task"


@given(models_and_ops())
def test_closing_last_open_sibling_reports_review_never_closes_parent(
        model_and_ops):
    """I8: the parent is never auto-closed; it gets project-needs-review."""
    model, _ = model_and_ops
    for node in list(model.all_nodes()):
        if not (model.is_project_child(node)
                and node.keyword not in CLOSED_STATES
                and not model.is_project(node)):
            continue
        group = model.sibling_group(node)
        others_closed = all(
            s.keyword in CLOSED_STATES
            for s in group if model.is_task(s) and s is not node)
        parent = model.parent_of(node)
        if not (others_closed and parent is not None
                and model.is_task(parent)
                and parent.keyword not in CLOSED_STATES):
            continue
        parent_state = parent.keyword
        result = model.set_done(node.heading)
        if not result.ok:
            continue
        assert parent.keyword == parent_state, "I8: parent state changed"
        reviews = [e for e in result.side_effects
                   if e.action == "project-needs-review"
                   and e.heading == parent.heading]
        assert reviews, "I8: no project-needs-review for the parent"
        return  # one exercised case per example is enough


@given(models_and_ops())
def test_plain_set_state_never_promotes(model_and_ops):
    """I9: parking or reopening via set-state yields no side effects."""
    model, _ = model_and_ops
    for node in list(model.all_nodes()):
        if not model.is_task(node):
            continue
        for state in ("WAITING", "DEFER", "TODO", "DONE"):
            before_next = {n.heading for n in model.all_nodes()
                           if n.keyword == "NEXT"}
            result = model.set_state(node.heading, state, reason="r")
            after_next = {n.heading for n in model.all_nodes()
                          if n.keyword == "NEXT"}
            if result.ok:
                assert after_next - before_next <= {node.heading}, (
                    "I9: set-state promoted a bystander")


# ---------------------------------------------------------------------------
# Reorder-primitive properties (§4.1, normative minimal move)
# ---------------------------------------------------------------------------

@given(models_and_ops())
def test_minimal_move_preserves_active_interleaving(model_and_ops):
    """§2/§4.1: the relative order of TODO/WAITING siblings is user data —
    closing a *different* sibling never reorders them among themselves."""
    model, _ = model_and_ops
    for node in list(model.all_nodes()):
        if not (model.is_task(node) and node.keyword == "TODO"
                and not model.is_project(node)):
            continue
        group = model.sibling_group(node)
        if not Model.is_uniform(group):
            continue
        others_before = [s.heading for s in group
                         if s is not node and s.keyword in ("TODO", "WAITING")]
        result = model.set_done(node.heading)
        if not result.ok:
            continue
        promoted = {e.heading for e in result.side_effects
                    if e.action == "state-change" and e.new_state == "NEXT"}
        others_after = [s.heading for s in group
                        if s.heading != node.heading
                        and s.keyword in ("TODO", "WAITING")
                        and s.heading not in promoted]
        expected = [h for h in others_before if h not in promoted]
        assert others_after == expected, (
            f"§4.1: closing {node.heading} reordered bystanders "
            f"{others_before} -> {others_after}")
        return


@given(models_and_ops())
def test_same_boundary_class_transition_never_moves_anyone(model_and_ops):
    """§4.1/§2 (review-bot on PR #55, finding 1): a set-state that stays
    within one boundary class — a no-op re-set, TODO↔WAITING, NEXT→NEXT
    — moves nothing at all, the changed task included."""
    model, _ = model_and_ops
    same_class = {"TODO": "WAITING", "WAITING": "TODO", "NEXT": "NEXT",
                  "DONE": "CANCELLED", "CANCELLED": "DONE"}
    for node in list(model.all_nodes()):
        if node.keyword not in same_class:
            continue
        group = model.sibling_group(node)
        order_before = [id(n) for n in group]
        result = model.set_state(
            node.heading, same_class[node.keyword], reason="r")
        if result.ok:
            assert [id(n) for n in group] == order_before, (
                f"{node.keyword}→{same_class[node.keyword]} on "
                f"{node.heading} moved a sibling")


def test_next_demotion_moves_minimally():
    """§4.1 (review-bot on PR #55, round 2): a NEXT demoted to TODO
    keeps its position unless a NEXT sibling would end up below it —
    then it drops just past the remaining NEXT prefix, never to the end
    of the active zone."""
    model = Model([Node("p", "TODO", children=[
        Node("a", "NEXT"), Node("b", "TODO"), Node("c", "TODO"),
    ])], Divergences.normative())
    model.set_state("a", "TODO")
    group = model.roots[0].children
    assert [n.heading for n in group] == ["a", "b", "c"]

    model = Model([Node("p", "TODO", children=[
        Node("n1", "NEXT"), Node("a", "NEXT"), Node("b", "TODO"),
    ])], Divergences.normative())
    model.set_state("n1", "TODO")
    group = model.roots[0].children
    assert [n.heading for n in group] == ["a", "n1", "b"]


@given(models_and_ops())
def test_mixed_groups_are_never_reordered(model_and_ops):
    """§2: a group with any keyword-less sibling gets no machine movement."""
    model, _ = model_and_ops
    for node in list(model.all_nodes()):
        group = model.sibling_group(node)
        if Model.is_uniform(group) or not model.is_task(node):
            continue
        order_before = [s.heading for s in group]
        result = model.set_done(node.heading)
        order_after = [s.heading for s in group]
        if result.ok:
            assert order_before == order_after, (
                "mixed group was reordered")
        return


# ---------------------------------------------------------------------------
# Zone-guard property for move (§4.9 normative)
# ---------------------------------------------------------------------------

@given(models_and_ops())
def test_move_never_breaks_zone_order(model_and_ops):
    """I5/§4.9: any accepted move leaves the group's zone order legal."""
    model, _ = model_and_ops
    for node in list(model.all_nodes()):
        group = model.sibling_group(node)
        if not Model.is_uniform(group):
            continue
        for direction in ("up", "down"):
            model.move(node.heading, direction=direction)
            assert not model.check_invariants()
        return


# ---------------------------------------------------------------------------
# Projection round-trip (the tier-2 harness's own correctness)
# ---------------------------------------------------------------------------

@given(forests())
def test_org_text_round_trip(model):
    """to_org_text ∘ parse_org_text is the identity on the skeleton."""
    parsed = Model(parse_org_text(model.to_org_text()))
    assert parsed.skeleton() == model.skeleton()


# ---------------------------------------------------------------------------
# The WAITING mechanism (§4.4/§4.6 normative, issue #39)
# ---------------------------------------------------------------------------

def _linked(waiter_state="WAITING"):
    """proj/[bb, aa] with aa waiting on bb, both link sides wired."""
    blocker = Node("bb", "TODO", triggers=("aa",))
    waiter = Node("aa", waiter_state, blockers=("bb",))
    return Model([Node("proj", "TODO", children=[blocker, waiter])],
                 Divergences.normative())


def test_waiting_entry_requires_reason_or_link():
    """§4.6 guardrail: a bare WAITING entry is rejected, nothing changes."""
    model = Model([Node("proj", "TODO", children=[Node("aa", "TODO")])],
                  Divergences.normative())
    before = model.skeleton()
    result = model.set_state("aa", "WAITING")
    assert result.ok is False
    assert model.skeleton() == before
    # Either flag alone satisfies it.
    assert model.set_state("aa", "WAITING", reason="because").ok
    assert model.roots[0].children[0].waiting_reason == "because"


def test_waiting_entry_edge_case_rulings():
    """§4.6 rulings 2026-08-10 (a)/(b)/(d), all atomic (I12)."""
    model = Model([Node("proj", "TODO", children=[
        Node("bb", "TODO"), Node("cc", "DONE"), Node("aa", "TODO"),
    ])], Divergences.normative())
    aa = model.roots[0].children[2]
    # (a) self-block
    assert model.set_state("aa", "WAITING", blocked_by=["aa"]).ok is False
    # (b) an already-closed blocker, and one closed among several
    assert model.set_state("aa", "WAITING", blocked_by=["cc"]).ok is False
    assert model.set_state(
        "aa", "WAITING", blocked_by=["bb", "cc"]).ok is False
    # (d) a multi-line reason
    assert model.set_state("aa", "WAITING", reason="one\ntwo").ok is False
    # Nothing was written by any of them.
    assert aa.keyword == "TODO" and aa.blockers == ()
    assert model.roots[0].children[0].triggers == ()


def test_waiting_entry_rejects_cycles_and_accepts_diamonds():
    """§4.6 ruling (a): a link closing a cycle is rejected; a diamond is not."""
    model = Model([Node("proj", "TODO", children=[
        Node("root", "TODO"), Node("left", "TODO"),
        Node("right", "TODO"), Node("top", "TODO"),
    ])], Divergences.normative())
    assert model.set_state("left", "WAITING", blocked_by=["root"]).ok
    assert model.set_state("right", "WAITING", blocked_by=["root"]).ok
    # A diamond: two disjoint paths to "root", no cycle.
    assert model.set_state(
        "top", "WAITING", blocked_by=["left", "right"]).ok
    # And a cycle: root may not wait on top.
    assert model.set_state("root", "WAITING", blocked_by=["top"]).ok is False
    # Cycle-safe even when the graph already holds one (visited set).
    model.roots[0].children[0].blockers = ("top",)
    assert model.set_state("root", "WAITING", blocked_by=["top"]).ok is False


def test_waiting_reentry_amends_by_replacing():
    """§4.6 ruling (c): the amend unwinds first, then writes."""
    model = Model([Node("proj", "TODO", children=[
        Node("b1", "TODO"), Node("b2", "TODO"), Node("aa", "TODO"),
    ])], Divergences.normative())
    assert model.set_state("aa", "WAITING", reason="r1", blocked_by=["b1"]).ok
    result = model.set_state("aa", "WAITING", blocked_by=["b2"])
    assert result.ok
    assert result.old_state == "WAITING" and result.new_state == "WAITING"
    b1, b2, aa = model.roots[0].children
    assert aa.blockers == ("b2",)          # replaced, never accumulated
    assert aa.waiting_reason is None       # r1 is not carried over
    assert b1.triggers == () and b2.triggers == ("aa",)
    assert [(e.action, e.heading) for e in result.side_effects] == [
        ("blocker-link-removed", "b1")]
    # A bare re-entry is still the guardrail's rejection, and atomic.
    assert model.set_state("aa", "WAITING").ok is False
    assert aa.blockers == ("b2",)


def test_and_gate_fires_only_when_all_closed():
    """§4.4: the wake fires on the last close, never the first."""
    model = Model([Node("proj", "TODO", children=[
        Node("b1", "TODO", triggers=("aa",)),
        Node("b2", "TODO", triggers=("aa",)),
        Node("aa", "WAITING", waiting_reason="r", blockers=("b1", "b2")),
    ])], Divergences.normative())
    aa = model.roots[0].children[2]
    result = model.set_done("b1")
    assert result.ok
    # Completely untouched while an open blocker remains.
    assert aa.keyword == "WAITING"
    assert aa.waiting_reason == "r" and aa.blockers == ("b1", "b2")
    assert [e for e in result.side_effects if e.heading == "aa"] == []
    result = model.set_done("b2")
    assert ("unblocked", "aa") in [(e.action, e.heading)
                                   for e in result.side_effects]
    assert aa.keyword in ("NEXT", "TODO")
    assert aa.waiting_reason is None and aa.blockers == ()


def test_and_gate_tolerates_dangling_links():
    """§4.4/§4.6: an id that no longer resolves is skipped silently."""
    model = Model([Node("proj", "TODO", children=[
        Node("bb", "TODO", triggers=("aa", "gone")),
        Node("aa", "WAITING", blockers=("bb", "also-gone")),
    ])], Divergences.normative())
    result = model.set_done("bb")
    assert result.ok
    # The dangling trigger is dropped, the live one still wakes; the
    # dangling blocker neither wedges the gate nor emits an effect.
    assert ("unblocked", "aa") in [(e.action, e.heading)
                                   for e in result.side_effects]
    assert [(e.action, e.heading) for e in result.side_effects
            if e.action == "blocker-link-removed"] == [
        ("blocker-link-removed", "bb")]


@pytest.mark.parametrize("group,expected", [
    # leaf project child, first open, no NEXT in the group → NEXT
    ([("done1", "DONE"), ("aa", "WAITING"), ("later", "TODO")], "NEXT"),
    # an open sibling precedes it → TODO
    ([("earlier", "TODO"), ("aa", "WAITING")], "TODO"),
    # the group already holds a NEXT → TODO ((b) without (a))
    ([("aa", "WAITING"), ("front", "NEXT")], "TODO"),
    # a DEFER sibling before it is ignored by the "precedes" test → NEXT
    ([("dd", "DEFER"), ("aa", "WAITING")], "NEXT"),
])
def test_conditional_wake_state(group, expected):
    """§4.6 conditional wake, on a project child leaf."""
    model = Model([
        Node("other", "TODO", children=[Node("bb", "TODO", triggers=("aa",))]),
        Node("proj", "TODO",
             children=[Node(h, k, blockers=("bb",) if h == "aa" else ())
                       for h, k in group]),
    ], Divergences.normative())
    order_before = [n.heading for n in model.roots[1].children]
    result = model.set_done("bb")
    assert result.ok
    woken = [e for e in result.side_effects if e.action == "unblocked"]
    assert len(woken) == 1 and woken[0].new_state == expected
    # Position never changes, and no §4.5 promotion runs from the wake.
    assert [n.heading for n in model.roots[1].children] == order_before


@pytest.mark.parametrize("shape", ["lone", "grown"])
def test_conditional_wake_is_todo_off_the_leaf_child_shape(shape):
    """§4.6/I3: a lone task, or one that grew children, wakes as TODO."""
    waiter = Node("aa", "WAITING", blockers=("bb",))
    if shape == "grown":
        waiter.children = [Node("kid", "TODO")]
        roots = [Node("other", "TODO",
                      children=[Node("bb", "TODO", triggers=("aa",))]),
                 Node("proj", "TODO", children=[waiter])]
    else:
        roots = [Node("other", "TODO",
                      children=[Node("bb", "TODO", triggers=("aa",))]),
                 waiter]
    model = Model(roots, Divergences.normative())
    result = model.set_done("bb")
    woken = [e for e in result.side_effects if e.action == "unblocked"]
    assert len(woken) == 1 and woken[0].new_state == "TODO"


@pytest.mark.parametrize("exit_op", ["set_state", "set_next", "set_done",
                                     "set_cancelled"])
def test_every_waiting_exit_unwinds_the_link_pair(exit_op):
    """§4.6: the exit cleanup runs from every CLI-driven exit site."""
    model = _linked()
    model.roots[0].children[1].waiting_reason = "r"
    args = ("aa", "TODO") if exit_op == "set_state" else ("aa",)
    result = getattr(model, exit_op)(*args)
    assert result.ok, result.error
    blocker, waiter = model.roots[0].children
    assert waiter.waiting_reason is None and waiter.blockers == ()
    assert blocker.triggers == ()
    assert ("blocker-link-removed", "bb") in [
        (e.action, e.heading) for e in result.side_effects]


def test_create_never_mints_waiting():
    """§4.2/§4.3: add-task and add-subtask reject WAITING; NEXT stays legal
    on add-subtask (the deliberate asymmetry)."""
    model = Model([Node("proj", "TODO", children=[Node("aa", "TODO")])],
                  Divergences.normative())
    before = model.skeleton()
    assert model.add_task("new1", state="WAITING").ok is False
    assert model.add_subtask("proj", "new2", state="WAITING").ok is False
    assert model.skeleton() == before
    assert model.add_subtask("proj", "new3", state="NEXT").ok


@given(models_and_ops())
def test_waiting_state_stays_consistent_under_any_sequence(model_and_ops):
    """No operation may leave a task WAITING-linked but not WAITING, nor a
    blocker pointing at a task that is not waiting on it back.

    Structural invariants are the main property's subject (and carry its
    PARKED-gap filter); this one asserts only the link pairing, which no
    documented gap touches.
    """
    model, ops = model_and_ops
    for op, args, kwargs in ops:
        getattr(model, op)(*args, **kwargs)
        for node in model.all_nodes():
            if node.blockers:
                assert node.keyword == "WAITING", (
                    f"{node.heading} carries blockers but is {node.keyword}")
            for heading in node.triggers:
                waiter = model.resolve_link(heading)
                if waiter is not None:
                    assert node.heading in waiter.blockers, (
                        "one-sided link pair survived an operation")
