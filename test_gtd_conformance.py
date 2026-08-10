"""Tier 2 (issue #45): bounded daemon-backed CLI-vs-model conformance.

Generated operation sequences run through the real CLI (daemon mode —
tier 2 is daemon-only per the 2026-07-31 ruling on #45) and through the
reference model in **current** divergence mode (Divergences.current():
every §7 row switched to today's behavior). Results must match exactly:
exit-code class, the resulting file skeleton, and the reported
side_effects. A mismatch here is an *unknown* divergence — a bug in
code, document, or model.

The **normative** mode runs in the expected-failure tests at the bottom:
one per applicable §7 row, each a minimal witness of the divergence,
marked xfail(strict=True) with its closing issue. When a stage-2c fix
lands, its witness flips loudly and the §7 row (plus its Divergences
flag) is retired together with the xfail marker.

Comparison surface notes:
- ``warnings`` is never compared: per the 2026-07-28 warnings-channel
  ruling the model predicts ``side_effects`` exactly and ignores
  ``warnings``.
- ``refile`` side_effects are not compared: today's refile envelope
  reports none even when it demotes (PARKED 2026-07-31 as a candidate
  §7 row — §4.0 requires reporting).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given, settings

from conftest import tier2_max_examples
from gtd_reference_model import (
    Divergences, Model, Node, parse_org_text,
)
from test_gtd_model_properties import models_and_ops
from test_org_gtd_cli import (
    CLI_SCRIPT, CORE_FILE, ELISP_FILE, kill_test_daemons,
)

# ---------------------------------------------------------------------------
# The daemon-backed CLI session
# ---------------------------------------------------------------------------


class CliSession:
    """One daemon identity (one ORG_DIRECTORY) reused across examples.

    Fresh state per example is written straight to tasks.org — the
    daemon picks up external on-disk changes at the next dispatch (the
    #27 conflict machinery re-reads refreshed state).
    """

    def __init__(self, root: Path):
        self.org_dir = root / "org"
        self.org_dir.mkdir(parents=True)
        # The socket root must stay SHORT (unix socket ~108-byte path
        # cap): same mkdtemp recipe as the main suite's daemon tests,
        # never the deep pytest basetemp.
        self.daemon_tmp = tempfile.mkdtemp(
            prefix="ogc-conf-", dir=os.environ.get("TMPDIR", "/tmp"))
        (self.org_dir / "inbox.org").write_text("")
        (self.org_dir / "tasks.org").write_text("")

    def env(self):
        env = os.environ.copy()
        env.update({
            "ORG_GTD_CLI_DAEMON": "1",
            "ORG_GTD_CLI_DAEMON_TTL": "600",
            "TMPDIR": self.daemon_tmp,
            "XDG_RUNTIME_DIR": "",
            "ORG_DIRECTORY": str(self.org_dir) + "/",
            "ORG_GTD_CORE_FILE": str(CORE_FILE),
            "ORG_GTD_ELISP_FILE": str(ELISP_FILE),
        })
        return env

    def run(self, *args):
        """Run one --json CLI command; returns (envelope|None, rc)."""
        result = subprocess.run(
            ["python3", str(CLI_SCRIPT), "--json", *args],
            capture_output=True, text=True, env=self.env(), timeout=60)
        try:
            envelope = json.loads(result.stdout) if result.stdout else None
        except json.JSONDecodeError:
            envelope = None
        return envelope, result.returncode

    def write_state(self, model):
        (self.org_dir / "tasks.org").write_text(model.to_org_text())

    def read_skeleton(self):
        text = (self.org_dir / "tasks.org").read_text()
        return Model(parse_org_text(text)).skeleton()

    def stop(self):
        self.run("daemon", "stop")
        kill_test_daemons(self.daemon_tmp)


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    session = CliSession(tmp_path_factory.mktemp("conformance"))
    yield session
    session.stop()


# ---------------------------------------------------------------------------
# Model-op → CLI-command mapping
# ---------------------------------------------------------------------------

def cli_args(op, args, kwargs):
    if op == "add_task":
        heading, state = args
        return ["add-task", heading, "--file", "tasks.org",
                "--state", state]
    if op == "add_subtask":
        parent, heading, state = args
        return ["add-subtask", parent, heading, "--state", state]
    if op == "set_done":
        return ["set-done", args[0]]
    if op == "set_cancelled":
        return ["set-cancelled", args[0]]
    if op == "set_state":
        out = ["set-state", args[0], args[1]]
        if kwargs.get("reason"):
            out += ["--reason", kwargs["reason"]]
        for blocker in kwargs.get("blocked_by") or ():
            out += ["--blocked-by", blocker]
        return out
    if op == "set_next":
        return ["set-next", args[0]]
    if op == "set_priority":
        return ["set-priority", args[0], args[1]]
    if op == "refile":
        if "to" in kwargs:
            return ["refile", args[0], "--to", kwargs["to"]]
        return ["refile", args[0], "--category", kwargs["category"]]
    if op == "move":
        direction = kwargs.get("direction")
        if direction in ("up", "down"):
            return ["move", args[0], f"--{direction}"]
        return ["move", args[0], f"--{direction}", kwargs["anchor"]]
    raise ValueError(op)


def envelope_side_effects(envelope):
    effects = (envelope or {}).get("side_effects") or []
    return sorted((e.get("action"), e.get("heading"),
                   e.get("old_state"), e.get("new_state"))
                  for e in effects)


def model_side_effects(result):
    return sorted((e.action, e.heading, e.old_state, e.new_state)
                  for e in result.side_effects)


# ---------------------------------------------------------------------------
# The generative conformance property (current mode — must be green)
# ---------------------------------------------------------------------------

# Deterministic pins for the narrow reorder shapes whose hand-written
# regression tests were DROPped into this property (see
# test-migration-manifest.md, TestAddSubtaskStateReorder): under the
# fast profile's small budget these shapes arise rarely per default
# run, so each is pinned as an @example — one CLI dispatch apiece,
# per the conftest.py rule that inputs worth keeping live in test code.
@example(model_and_ops=(
    # add-subtask DONE onto a parent whose children are NEXT-then-TODO
    # (the issue-#20 shape of the dropped test_add_done_reorders_above_next)
    Model([Node("proj", "TODO", children=[
        Node("aa", "NEXT"), Node("bb", "TODO")])],
        Divergences.normative()),
    [("add_subtask", ("proj", "new900", "DONE"), {})],
))
@example(model_and_ops=(
    # add-subtask NEXT to an empty parent (dropped
    # test_add_next_to_empty_parent)
    Model([Node("proj", "TODO")], Divergences.normative()),
    [("add_subtask", ("proj", "new901", "NEXT"), {})],
))
@example(model_and_ops=(
    # add-subtask NEXT above existing TODO siblings (dropped
    # test_add_next_reorders_above_todo)
    Model([Node("proj", "TODO", children=[
        Node("aa", "TODO"), Node("bb", "TODO")])],
        Divergences.normative()),
    [("add_subtask", ("proj", "new902", "NEXT"), {})],
))
@given(models_and_ops())
@settings(max_examples=tier2_max_examples(), deadline=None)
def test_cli_conforms_to_current_mode_model(cli, model_and_ops):
    initial, ops = model_and_ops
    model = initial.clone()
    model.div = Divergences.current()
    cli.write_state(model)
    for op, args, kwargs in ops:
        envelope, rc = cli.run(*cli_args(op, args, kwargs))
        result = getattr(model, op)(*args, **kwargs)
        assert (rc == 0) == result.ok, (
            f"{op}{args}{kwargs}: CLI rc={rc} vs model ok={result.ok} "
            f"(cli: {envelope})")
        disk = cli.read_skeleton()
        assert disk == model.skeleton(), (
            f"{op}{args}{kwargs}: file skeleton diverged\n"
            f"  cli:   {disk}\n  model: {model.skeleton()}")
        if result.ok and op != "refile":
            assert envelope_side_effects(envelope) == \
                model_side_effects(result), (
                f"{op}{args}{kwargs}: side_effects diverged\n"
                f"  cli:   {envelope_side_effects(envelope)}\n"
                f"  model: {model_side_effects(result)}")
        assert_response_integrity(op, args, kwargs, envelope, rc, disk)


def assert_response_integrity(op, args, kwargs, envelope, rc, disk):
    """#46 A19: a reported ``new_state`` must be the keyword on disk.

    The CLI-level counterpart of the tier-1 property (A18): the #46
    symptom was a ``set-state DONE`` envelope claiming
    ``"new_state": "DONE"`` while the blocked heading stayed TODO. A
    rejected transition must also never come back as rc 0.
    """
    reported = (envelope or {}).get("new_state")
    if reported is None:
        return
    assert rc == 0, (
        f"{op}{args}{kwargs}: rejected (rc={rc}) yet reported "
        f"new_state={reported!r}")
    heading = (envelope or {}).get("heading")
    on_disk = {h: kw for _, h, kw, _ in disk}
    assert heading in on_disk, (
        f"{op}{args}{kwargs}: reported heading {heading!r} not on disk")
    assert on_disk[heading] == reported, (
        f"{op}{args}{kwargs}: envelope says new_state={reported!r} but "
        f"{heading!r} is {on_disk[heading]!r} on disk")


# ---------------------------------------------------------------------------
# §7 witnesses (normative mode — xfail until the closing issue lands)
# ---------------------------------------------------------------------------

def run_normative(cli, model, op, args, kwargs):
    """Run one op through CLI and normative model; return both outcomes."""
    cli.write_state(model)
    envelope, rc = cli.run(*cli_args(op, args, kwargs))
    result = getattr(model, op)(*args, **kwargs)
    return envelope, rc, result


# §7 row 1 retired 2026-08-07 (#34): the reorder primitive is the §4.1
# minimal move — this witness now pins the agreeing behavior.
def test_s7row1_minimal_move_preserves_interleaving(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("aa", "TODO"), Node("ww", "WAITING"), Node("bb", "TODO"),
    ])], Divergences.normative())
    _, rc, result = run_normative(cli, model, "set_done", ("aa",), {})
    assert rc == 0 and result.ok
    assert cli.read_skeleton() == model.skeleton()


# §7 row 2 (#34) intentionally has NO xfail witness: the recorded
# divergence does not reproduce on 2026-07-31 master (refile's
# destination reorder runs unconditionally and places arrivals). Row
# flagged for re-examination; this plain regression test pins the
# agreeing behavior meanwhile.
def test_s7row2_refile_places_arrival_in_zone(cli):
    model = Model([
        Node("proj", "TODO", children=[
            Node("aa", "TODO"), Node("dd", "DEFER")]),
        Node("lone", "TODO"),
    ], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "refile", ("lone",), {"to": "proj"})
    assert rc == 0 and result.ok
    assert cli.read_skeleton() == model.skeleton()


# §7 rows 3 and 4 retired 2026-08-08 (#38): the promotion scan walks the
# whole sibling group in document order and reports every all-done-but-open
# subproject it passes, so both witnesses now pin agreeing behavior.
def test_s7row3_promotion_scans_whole_group(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("aa", "TODO"), Node("bb", "TODO"),
    ])], Divergences.normative())
    _, rc, result = run_normative(cli, model, "set_done", ("bb",), {})
    assert rc == 0 and result.ok
    # Normative: aa (first open TODO in document order) is promoted.
    assert model_side_effects(result) == [
        ("state-change", "aa", "TODO", "NEXT")]
    assert cli.read_skeleton() == model.skeleton()


def test_s7row4_subproject_review_emitted(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("aa", "TODO"),
        Node("sub", "TODO", children=[Node("done1", "DONE")]),
        Node("bb", "TODO"),
    ])], Divergences.normative())
    # Closing aa: the scan passes the all-done-but-open subproject "sub"
    # (emitting review for it) and promotes bb.
    envelope, rc, result = run_normative(cli, model, "set_done", ("aa",), {})
    assert rc == 0 and result.ok
    assert ("project-needs-review", "sub", None, None) in \
        model_side_effects(result)
    assert envelope_side_effects(envelope) == model_side_effects(result)


# §7 row 5 retired 2026-08-10 (#39): the whole WAITING mechanism landed —
# entry guardrail, blocker links, the AND-gated auto-unblock with its
# conditional wake, and the exit cleanup — so these witnesses now pin
# agreeing behavior.
def test_s7row5_waiting_requires_reason(cli):
    model = Model([Node("proj", "TODO", children=[Node("aa", "TODO")])],
                  Divergences.normative())
    envelope, rc, result = run_normative(
        cli, model, "set_state", ("aa", "WAITING"), {})
    # Normative: a bare WAITING entry is rejected on both sides.
    assert result.ok is False
    assert rc != 0
    # I12: nothing written.
    assert cli.read_skeleton() == model.skeleton()
    assert envelope_side_effects(envelope) == []


def test_s7row5_blocker_link_and_wake(cli):
    """The full round trip: link, AND-gate, conditional wake, cleanup."""
    model = Model([Node("proj", "TODO", children=[
        Node("bb", "TODO"), Node("aa", "TODO"),
    ])], Divergences.normative())
    envelope, rc, result = run_normative(
        cli, model, "set_state", ("aa", "WAITING"), {"blocked_by": ["bb"]})
    assert rc == 0 and result.ok
    assert cli.read_skeleton() == model.skeleton()
    assert envelope_side_effects(envelope) == model_side_effects(result)
    # Closing the blocker wakes aa. `run_normative` rewrites the file
    # from the model, so the links the model now holds are handed back
    # to the CLI verbatim (headings double as ids in `to_org_text`).
    envelope, rc, result = run_normative(cli, model, "set_done", ("bb",), {})
    assert rc == 0 and result.ok
    assert ("unblocked", "aa", "WAITING", "NEXT") in model_side_effects(result)
    assert envelope_side_effects(envelope) == model_side_effects(result)
    assert cli.read_skeleton() == model.skeleton()


def test_s7row5_multi_blocker_and_gate(cli):
    """Two blockers: the first close leaves the waiter untouched."""
    model = Model([Node("proj", "TODO", children=[
        Node("b1", "TODO"), Node("b2", "TODO"),
        Node("aa", "WAITING", blockers=("b1", "b2")),
    ])], Divergences.normative())
    # Wire the blockers' trigger sides the way a linked state is on disk.
    for node in model.all_nodes():
        if node.heading in ("b1", "b2"):
            node.triggers = ("aa",)
    envelope, rc, result = run_normative(cli, model, "set_done", ("b1",), {})
    assert rc == 0 and result.ok
    assert model_side_effects(result) == []  # gate did not fire
    assert envelope_side_effects(envelope) == []
    assert cli.read_skeleton() == model.skeleton()


def test_s7row5_create_never_mints_waiting(cli):
    model = Model([Node("proj", "TODO", children=[Node("aa", "TODO")])],
                  Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "add_task", ("new950", "WAITING"), {})
    assert result.ok is False
    assert rc != 0
    assert cli.read_skeleton() == model.skeleton()
    _, rc, result = run_normative(
        cli, model, "add_subtask", ("proj", "new951", "WAITING"), {})
    assert result.ok is False
    assert rc != 0
    assert cli.read_skeleton() == model.skeleton()


@pytest.mark.xfail(reason="§7 row 7 (#41): set-priority accepts any "
                          "cookie; close leaves cookies in place",
                   strict=True)
def test_s7row7_priority_rules(cli):
    model = Model([Node("lone", "TODO")], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "set_priority", ("lone", "B"), {})
    assert result.ok is False
    assert rc != 0


# §7 row 8 retired 2026-08-10 (#46): the set-state legality guards, the
# blocked-close rejection and set-next's non-project candidate rule all
# landed, so these four witnesses now pin agreeing behavior.
def test_s7row8_next_guard_rejects_subproject_heading(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("sub", "TODO", children=[Node("aa", "TODO")]),
    ])], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "set_state", ("sub", "NEXT"), {})
    assert result.ok is False
    assert rc != 0
    assert cli.read_skeleton() == model.skeleton()


def test_s7row8_waiting_guard_rejects_project_heading(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("aa", "TODO"), Node("bb", "TODO"),
    ])], Divergences.normative())
    envelope, rc, result = run_normative(
        cli, model, "set_state", ("proj", "WAITING"), {"reason": "because"})
    assert result.ok is False
    assert rc != 0
    # I12: nothing written.
    assert cli.read_skeleton() == model.skeleton()
    assert envelope_side_effects(envelope) == []


def test_s7row8_blocked_close_via_set_state_is_rejected(cli):
    model = Model([Node("proj", "TODO", children=[Node("aa", "TODO")])],
                  Divergences.normative())
    envelope, rc, result = run_normative(
        cli, model, "set_state", ("proj", "DONE"), {})
    # Normative: a blocked close is an error, never a false success.
    assert result.ok is False
    assert rc != 0
    assert cli.read_skeleton() == model.skeleton()
    # The original symptom: the envelope must not claim a new_state the
    # file never reached.
    assert (envelope or {}).get("new_state") is None
    assert envelope_side_effects(envelope) == []


def test_s7row8_set_next_skips_subproject_first_child(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("sub", "TODO", children=[Node("ss", "TODO")]),
        Node("bb", "TODO"),
    ])], Divergences.normative())
    envelope, rc, result = run_normative(cli, model, "set_next", ("proj",), {})
    assert rc == 0 and result.ok
    # The non-project child later in the group is promoted, not "sub".
    assert model_side_effects(result) == [
        ("state-change", "bb", "TODO", "NEXT")]
    assert envelope_side_effects(envelope) == model_side_effects(result)
    assert cli.read_skeleton() == model.skeleton()


# §7 row 9 retired 2026-08-07 (#47, after the #37 interim subset): move
# guards the full §4.9 zone invariant, so both witnesses now pin
# agreeing behavior.
def test_s7row9_completed_block_guard(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("done1", "DONE"), Node("aa", "TODO"),
    ])], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "move", ("aa",), {"direction": "up"})
    # Crossing into the completed block is rejected, file unchanged.
    assert result.ok is False
    assert rc != 0
    assert cli.read_skeleton() == model.skeleton()


def test_s7row9_full_zone_guard(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("nn", "NEXT"), Node("aa", "TODO"),
    ])], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "move", ("aa",), {"direction": "up"})
    # Crossing above the NEXT prefix is rejected, file unchanged.
    assert result.ok is False
    assert rc != 0
    assert cli.read_skeleton() == model.skeleton()


def test_s7row9_defer_block_guard(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("aa", "TODO"), Node("dd", "DEFER"),
    ])], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "move", ("aa",), {"direction": "down"})
    # Sinking an open task into the DEFER block is rejected.
    assert result.ok is False
    assert rc != 0
    assert cli.read_skeleton() == model.skeleton()


# §7 row 14 retired 2026-08-07 (#34): the level-1 guard is gone — a
# uniform top-level group places like an implicit category bucket
# (ruling 2026-08-02). This witness now pins the agreeing behavior.
def test_s7row14_toplevel_group_sorts_as_category_bucket(cli):
    # Uniform flat top-level group (NEXT is illegal at top level, so
    # the bucket's zones are TODO/WAITING -> DEFER -> closed).
    model = Model([
        Node("aa", "TODO"), Node("bb", "TODO"), Node("dd", "DEFER"),
    ], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "set_state", ("aa", "DEFER"), {})
    assert rc == 0 and result.ok
    # aa sinks to the top of the DEFER block.
    assert cli.read_skeleton() == model.skeleton()
