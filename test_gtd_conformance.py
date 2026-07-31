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
from hypothesis import given, settings

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


# ---------------------------------------------------------------------------
# §7 witnesses (normative mode — xfail until the closing issue lands)
# ---------------------------------------------------------------------------

def run_normative(cli, model, op, args, kwargs):
    """Run one op through CLI and normative model; return both outcomes."""
    cli.write_state(model)
    envelope, rc = cli.run(*cli_args(op, args, kwargs))
    result = getattr(model, op)(*args, **kwargs)
    return envelope, rc, result


@pytest.mark.xfail(reason="§7 row 1 (#34): reorder is a full stable "
                          "state-sort, destroying TODO/WAITING interleaving",
                   strict=True)
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


@pytest.mark.xfail(reason="§7 row 3 (#38): promotion scans forward from "
                          "the closed task only, not the whole group",
                   strict=True)
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


@pytest.mark.xfail(reason="§7 row 4 (#38): no per-subproject "
                          "project-needs-review is emitted",
                   strict=True)
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


@pytest.mark.xfail(reason="§7 row 5 (#39): WAITING entry requires no "
                          "reason/blocker", strict=True)
def test_s7row5_waiting_requires_reason(cli):
    model = Model([Node("proj", "TODO", children=[Node("aa", "TODO")])],
                  Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "set_state", ("aa", "WAITING"), {})
    # Normative: rejected without a reason; today the CLI accepts.
    assert result.ok is False
    assert rc != 0


@pytest.mark.xfail(reason="§7 row 7 (#41): set-priority accepts any "
                          "cookie; close leaves cookies in place",
                   strict=True)
def test_s7row7_priority_rules(cli):
    model = Model([Node("lone", "TODO")], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "set_priority", ("lone", "B"), {})
    assert result.ok is False
    assert rc != 0


@pytest.mark.xfail(reason="§7 row 8 (#46): set-state NEXT admits "
                          "subproject headings", strict=True)
def test_s7row8_next_guard_rejects_subproject_heading(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("sub", "TODO", children=[Node("aa", "TODO")]),
    ])], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "set_state", ("sub", "NEXT"), {})
    assert result.ok is False
    assert rc != 0


@pytest.mark.xfail(reason="§7 row 9 (#37/#47): move performs cross-zone "
                          "reorders unguarded", strict=True)
def test_s7row9_move_zone_guard(cli):
    model = Model([Node("proj", "TODO", children=[
        Node("done1", "DONE"), Node("aa", "TODO"),
    ])], Divergences.normative())
    _, rc, result = run_normative(
        cli, model, "move", ("aa",), {"direction": "up"})
    # Normative: crossing into the completed block is rejected.
    assert result.ok is False
    assert rc != 0
    assert cli.read_skeleton() == model.skeleton()
