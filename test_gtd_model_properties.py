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
    ZONE_ACTIVE,
)

# ---------------------------------------------------------------------------
# Strategies: valid GTD states by construction
# ---------------------------------------------------------------------------

# Keywords legal per structural position (§3 legality matrix).
LONE_TASK_STATES = ("TODO", "WAITING", "DEFER", "DONE", "CANCELLED")
LEAF_CHILD_STATES = ("TODO", "NEXT", "WAITING", "DEFER", "DONE", "CANCELLED")
PROJECT_HEADING_STATES = ("TODO", "DEFER")  # closed handled post-hoc (I4)


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
        state = draw(st.sampled_from(LEAF_CHILD_STATES))
        return (op, (node.heading, f"new{draw(st.integers(0, 999)):03d}",
                     state), {})
    if op == "add_task":
        state = draw(st.sampled_from(LONE_TASK_STATES))
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

def _in_closed_subtree(model, node):
    """True when node or a task ancestor is closed."""
    chain = [node] + model.task_ancestors(node)
    return any(n.keyword in CLOSED_STATES for n in chain)


def _append_breaks_zone_order(group, state):
    """True when appending STATE at the group's end violates I5."""
    if not group or not Model.is_uniform(group):
        return False
    return zone_of(state) < zone_of(group[-1].keyword)


def hits_parked_spec_gap(model, op, args, kwargs):
    """PARKED SPEC QUESTIONS (2026-07-31, awaiting olli's ruling).

    Two gap families where a documented-legal operation produces a state
    the §3 matrix / §6 invariants forbid — the real CLI accepts both:

    - **I4 outside closure**: §4.3 add-subtask, §4.6 set-state
      (reopening), and §4.8 refile can place an open task under a closed
      heading — a closed project with open descendants.
    - **Leaf→project repairs are incomplete**: §4.3/§4.8 only repair a
      NEXT parent that becomes a project; a WAITING leaf gaining its
      first task child keeps WAITING on what is now a project heading
      (§3 allows WAITING on leaves only). And the NEXT repair itself
      only demotes: the demoted parent is not re-placed in its own
      sibling group, so a NEXT sibling below it now violates I5's
      NEXT-prefix rule (the CLI's repair likewise runs no reorder
      there).
    - **Append-last vs I5**: §4.2/§4.3 append the new task as last
      child, but that spot can violate zone order — a DONE filed into a
      bucket of open tasks lands below the active zone, a TODO filed
      into a group with a DEFER block lands below it. §4.3 reorders only
      when the created state ranks above TODO; §4.2 never reorders.
    - **WAITING-keeps-position vs the NEXT prefix**: a NEXT entering
      WAITING keeps its position (§4.6/§4.1) — at the top of the active
      zone; if another (hand-declared) NEXT sits below, the group now
      has WAITING above NEXT, breaking I5's NEXT-prefix rule. The CLI's
      skip-sort mitigation produces the same state.

    Until ruled, the preservation property routes around these cases;
    the xfail test below keeps them visible.
    """
    if op == "add_task":
        _, state = args
        group = model.roots  # ops strategy files at top level only
        return _append_breaks_zone_order(group, state)
    if op == "add_subtask":
        parent, _, state = args
        node, _ = model.find(parent)
        if node is None:
            return False
        appended_out_of_zone = (
            state not in CLOSED_STATES and state != "NEXT"
            and _append_breaks_zone_order(node.children, state))
        # §4.3's NEXT-parent demotion never re-places the demoted parent
        # in its own group (same repair gap as refile's — docstring).
        demotes_unplaced = (
            node.keyword == "NEXT"
            and any(s is not node and s.keyword == "NEXT"
                    for s in model.sibling_group(node)))
        return ((state not in CLOSED_STATES
                 and _in_closed_subtree(model, node))
                or (node.keyword == "WAITING"
                    and model.is_leaf_task(node))
                or appended_out_of_zone
                or demotes_unplaced)
    if op == "set_state":
        target, state = args
        node, _ = model.find(target)
        if node is None:
            return False
        if (state not in CLOSED_STATES
                and any(n.keyword in CLOSED_STATES
                        for n in model.task_ancestors(node))):
            return True
        # WAITING-keeps-position vs NEXT prefix (see docstring).
        return (state == "WAITING" and node.keyword == "NEXT"
                and any(s is not node and s.keyword == "NEXT"
                        for s in model.sibling_group(node)))
    if op == "set_next":
        # Same I4 family: set-next can reopen (promote) inside a closed
        # subtree — directly, or via the project path's child promotion.
        node, _ = model.find(args[0])
        return (node is not None
                and any(n.keyword in CLOSED_STATES
                        for n in model.task_ancestors(node)))
    if op == "refile":
        node, _ = model.find(args[0])
        # Resolve the target the way refile itself does: --to is an
        # exact any-heading-type match (categories included), --category
        # is category-scoped. A task-scoped find() here would let
        # category-heading targets bypass the guard.
        if "to" in kwargs:
            target = next(
                (n for n in model.all_nodes()
                 if n.heading.lower() == kwargs["to"].lower()), None)
        else:
            target, _ = model.find_category(kwargs["category"])
        if node is None or target is None:
            return False
        subtree_open = (node.keyword not in CLOSED_STATES
                        or any(n.keyword not in CLOSED_STATES
                               for n in model.task_descendants(node)))
        subtree_has_task = (node.keyword is not None
                            or bool(model.task_descendants(node)))
        makes_waiting_project = (subtree_has_task
                                 and target.keyword == "WAITING"
                                 and model.is_leaf_task(target))
        # NEXT-leaf target: becoming a project demotes it (§4.8), but
        # the demoted parent is never re-placed in its own group.
        demotes_unplaced = (subtree_has_task
                            and target.keyword == "NEXT"
                            and model.is_leaf_task(target))
        return ((subtree_open and _in_closed_subtree(model, target))
                or makes_waiting_project or demotes_unplaced)
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


@pytest.mark.xfail(
    reason="spec gap parked for ruling 2026-07-31: §4.3/§4.6/§4.8 "
           "preconditions do not protect I4 (closed project can gain "
           "open descendants); the real CLI accepts all three",
    strict=True)
def test_spec_gap_i4_unguarded_outside_closure():
    """The minimal witness: add-subtask of an open child under a DONE
    leaf yields a closed project with open descendants."""
    model = Model([Node("done-leaf", "DONE")])
    result = model.add_subtask("done-leaf", "open child", "TODO")
    assert result.ok
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
