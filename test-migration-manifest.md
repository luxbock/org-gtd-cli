# Test Migration Manifest — issue #45 part 2

**Base:** `test_org_gtd_cli.py` at merge-base `f63ab81` (99 test classes,
771 test functions).

**Guarantee:** every `def test_*` in that base file appears in exactly one
entry below. Spot-check:

    grep -c "def test_" test_org_gtd_cli.py   # 771 at f63ab81

**Legend**
- **KEEP** — CLI-surface coverage the tier-1/tier-2 conformance suite
  cannot see (JSON envelope shape, error message wording, LOGBOOK/CLOSED
  format, addressing edge cases, `--dry-run` text, `--index`/`--file`
  targeting, warnings channel, file I/O specifics, daemon lifecycle,
  batch mode, view rendering, etc.).
- **KEEP+ANNOTATE row N (#NN)** — pins CURRENT (§7-divergent) behavior.
  Most carry a `# pins §7 row N (#NN)` comment: the closing issue's
  stage-2c fix must flip that assertion. Two carry
  `# anchor §7 row N (#NN)` instead — non-flipping regression anchors
  whose fixture (or unasserted setup) sits on the issue's code path but
  whose assertions stay green when it lands; the marker distinction is
  what tells a grep-driven stage-2c worker which expectation to hold.
- **DROP** — subsumed by tier-1 (`test_gtd_model_properties.py`
  invariant properties on the normative reference model) or tier-2
  (`test_gtd_conformance.py` daemon-backed CLI-vs-current-mode-model
  conformance: rc-class, file skeleton, side_effects excl. `refile`,
  `warnings` never compared). Each DROP names the subsuming property or
  conformance guarantee.

**Classification bias:** when a test could plausibly pin divergent
behavior OR only stress CLI surface, KEEP (per the BRIEF's "when in
doubt, KEEP" rule). Every `pins`-marked KEEP+ANNOTATE call-out below is a test whose
assertion flips after the closing issue lands; the two `anchor`-marked
ones do not flip (their entries say why) and exist as regression
anchors on the issue's code path.

## Per-class classifications

### TestPytestDaemonIsolation (3 tests) — KEEP all
Daemon-isolation invariants + pytest tmp-scoping (subprocess mocks,
`ORG_GTD_CLI_DAEMON` env plumbing). Purely test-harness / environment
concerns; reference model has no notion of processes, sockets, or xdist
workers.
- test_run_cli_defaults_to_batch_under_ambient_daemon
- test_run_cli_explicit_daemon_opt_in_wins
- test_cleanup_is_scoped_to_worker_tmp_root

### TestOrgTimestamp (3 tests) — KEEP all
`org-timestamp` formatting utility (day-of-week, time, ranges). No
GTD-state semantics; not modelled.
- test_date_with_day_of_week
- test_date_with_time
- test_time_range

### TestAddTask (23 tests) — KEEP all
Task-creation CLI surface: default file selection, `--tags`,
`--schedule`, `--deadline`, `--time`, `--body`, `--priority`,
`--category` path/substring/ambiguity/case-insensitivity, blank-line
handling, rejection of body containing `* ` heading delimiters. The
reference model has no equivalent of category-path resolution, body-text
validation, or per-file targeting.
All 23 tests KEEP.

### TestAddTaskCategoryBlankLines (1 test) — KEEP
`test_repeated_add_task_category_no_consecutive_blank_lines` — file
formatting invariant (no `\n\n\n`). Model has no textual layer.

### TestAddSubtask (3 tests) — KEEP all
`add-subtask` CLI addressing (default state, disambiguation via
`--index`, sibling insertion). CLI-envelope surface (rc-class + stdout
text + file text).
- test_adds_child_at_correct_level
- test_disambiguation
- test_index_selects_match

### TestFindTaskCategoryHint (4 tests) — KEEP all
Hint suggestions in error output when a `--category` typo is close to a
real category. Pure CLI error-surface; model has no diagnostics layer.
- test_find_task_ambiguous_categories_hint_by_default
- test_find_task_ambiguous_categories_hint_by_specific_path
- test_find_task_ambiguous_categories_hint_by_prefix_path
- test_find_task_ambiguous_categories_hint_no_partial_matches

### TestAddSubtaskBlankLines (1 test) — KEEP
`test_add_subtask_then_add_note_no_consecutive_blank_lines` — file
formatting.

### TestAgenda (11 tests) — KEEP all
`agenda` view rendering: state filter (default open, closed, all,
custom), tag AND/OR, date window, exit codes, tag/date interaction. View
layer, not modelled in tier-1/2 (which target §§2-6 state semantics
only). §5.3 divergence-free surface.

### TestSearch (16 tests) — KEEP all
`search` heading substring: keyword filters, JSON structure, empty
result rc, project/subproject rendering. View layer.

### TestShow (11 tests) — KEEP all
`show` one-task envelope: heading, body, LOGBOOK, category path, ID,
priority, dates, tags, project/leaf kind classification. Read-only
CLI-envelope surface.

### TestSubtasks (3 tests) — KEEP all
`subtasks` view (direct children + progress fraction). View layer.

### TestCategories (7 tests) — KEEP all
`categories` list of category-heading paths. View layer (§5.5).

### TestShowSubtasksCategoryLookup (14 tests) — KEEP all
Category-vs-task lookup precedence in `show`/`subtasks` (exact category
match beats substring task, ambiguity errors, path disambiguation,
case-insensitivity, plain-category envelope shape). CLI-addressing
surface; tier-2's model uses task-only addressing.

### TestProjects (3 tests) — KEEP all
`projects` view (active projects list; excludes done/category headings).
View layer.

### TestProcessAgentTasks (1 test) — KEEP
`test_process_agent_tasks_removed` — asserts the deprecated command is
gone. Legacy-surface pin.

### TestDone (18 tests) — KEEP all
Almost every test asserts on stdout text ("Auto-progressed", "project
left open for review", "in subproject"), JSON envelope, or `--dry-run`
preview text — CLI surface tier-2 does not compare. KEEP unchanged:
- test_marks_single_match_as_done
- test_exit_2_on_ambiguous
- test_index_selects_match
- test_dry_run
- test_auto_progress_promotes_next_todo
- test_done_removes_waiting_tag
- test_all_siblings_done_leaves_parent_open
- test_subproject_drill_in_promotes_first_child
- test_existing_next_prevents_promotion  *(I6 pin — normative, not divergent)*
- test_waiting_sibling_blocks_promotion  *(I6 pin — normative, not divergent)*
- test_waiting_sibling_blocks_promotion_dry_run
- test_dry_run_leaves_parent_open_preview
- test_dry_run_subproject_drill_in_preview
- test_no_cascade_leaves_ancestors_open
- test_no_cascade_dry_run_preview
- test_blocked_parent_does_not_complete
- test_parent_completes_when_all_children_done
- test_dry_run_blocked_parent_reports_blocked_and_mutates_nothing

*No test in this class asserts an outcome that flips under any current §7
row: the promotion tests exercise scenarios where forward-only scan and
whole-group scan agree, and blocked-parent handling is normative for the
dedicated `set-done` command (row 8 divergence lives on `set-state`).*

### TestSetState (11 tests) — MIXED
- test_changes_state — **KEEP** (stdout arrow text + skeleton)
- test_waiting_reason_adds_logbook_state_note — **KEEP** (LOGBOOK format)
- test_defer_reason_adds_logbook_state_note — **KEEP** (LOGBOOK format)
- test_without_reason_does_not_add_reason_note — **KEEP+ANNOTATE row 5 (#39)** (pins that WAITING is accepted without `--reason`; #39 requires reason at entry, so the plain call flips to failure)
- test_waiting_adds_tag — **KEEP+ANNOTATE row 6 (#40)** (pins the `:WAITING:` legacy tag write; #40 retires the tag machinery)
- test_removing_waiting_removes_tag — **KEEP** (rc + skeleton on TODO transition; tag cleanup is a follow-through, not a flip)
- test_dry_run — **KEEP** (`--dry-run` stdout text)
- test_preserves_priority_cookie — **KEEP** (§4.10 annotation-op: state changes leave priority; normative)
- test_invalid_state_clean_error — **KEEP** (CLI error text)
- test_defer_to_waiting_cleans_defer_tag — **KEEP+ANNOTATE row 6 (#40)** (pins the `:DEFER:` tag write/removal machinery)
- test_waiting_to_todo_cleans_waiting_tag — **KEEP** (asserts skeleton only)

### TestSetCancelled (8 tests) — KEEP all
- test_cancel_by_substring — CLI envelope (arrow, file text)
- test_cancel_by_id — CLI addressing via `:ID:`
- test_ambiguous_substring_exits_nonzero_with_hint — rc=2 + hint
- test_dry_run — `--dry-run` text
- test_blocked_parent_does_not_cancel_or_auto_progress — normative
  §4.4 (dedicated `set-cancelled` enforces I4 correctly; row 8's divergence
  is on `set-state CANCELLED`, not on this command)
- test_blocked_parent_dry_run_reports_blocked — `--dry-run` variant
- test_set_cancelled_auto_progress — promotion after cancel (normative)
- test_set_state_cancelled_no_auto_progress — pins I9 (plain `set-state`
  never promotes; normative)

### TestSetPriority (9 tests) — MIXED
- test_set_priority_a — **KEEP** (accepts 'A'; normative)
- test_change_priority_a_to_c — **KEEP+ANNOTATE row 7 (#41)** (accepts 'C'; #41 rejects everything but 'A')
- test_clear_priority — **KEEP+ANNOTATE row 7 (#41)** (anchor, non-flipping: only the unasserted 'C' setup call changes under #41)
- test_clear_on_no_priority — **KEEP** (clear op alone)
- test_invalid_priority — **KEEP+ANNOTATE row 7 (#41)** (asserts "A, B, C" as the valid set; message flips to just 'A')
- test_dry_run — **KEEP** (`--dry-run` text, priority 'A')
- test_change_existing_priority — **KEEP+ANNOTATE row 7 (#41)** (assigns 'C' to a fixture with existing '[#A]')
- test_lowercase_input — **KEEP** (case normalization; unaffected by #41)
- test_index_disambiguation — **KEEP** (`--index` addressing)

### TestRefile (3 tests) — KEEP all
- test_moves_task_to_target — CLI envelope
- test_dry_run — `--dry-run` text
- test_target_not_found — rc=1 on missing target (CLI addressing)

### TestRefileSelfMatch (3 tests) — KEEP all
Refile with a heading that matches itself as a target: skipped-as-target,
skipped-child-also-counts, all-targets-are-self-matches. CLI-addressing
edge cases; the reference model uses task-heading identity by name only
and does not simulate the substring-collision path.

### TestRefileToExact (7 tests) — KEEP all
`refile --to` addressing (exact match, case-insensitivity, path
disambiguation, TODO-heading targeting, `--dry-run`, typo failures).
CLI-addressing surface. `test_exact_match_finds_first` uses a
UNIQUE-in-fixture destination ("Tools"), so it does not exercise row 12's
first-match-among-duplicates divergence.
- test_exact_match_finds_first
- test_partial_match_fails
- test_path_disambiguates
- test_targets_todo_heading
- test_case_insensitive
- test_dry_run
- test_intermediate_path_typo_fails

### TestRefileCategory (8 tests) — KEEP all
`refile --category` addressing (substring, path disambiguation,
ambiguity → rc=2 + candidate list, `--dry-run`, "only-todo-matches"
fallback). CLI-addressing surface distinct from `--to`. The
`test_ambiguous_*` tests pin the error surface used by category
resolution and would need reworking if that surface changed — a
different scope from §7.

### TestAddEvent (10 tests) — KEEP all
Calendar-event creation surface (gcal drawer, `#+PROPERTY: calendar-id`,
date ranges, timed events, custom tags, non-default files). No state
semantics; not modelled.

### TestAddNote (10 tests) — KEEP all
Note-body handling (LOGBOOK note format, `--reason`, escapes,
line-wrapping). No state semantics.

### TestAppendBody (6 tests) — KEEP all
Body-text mutation (`append-body`) — line handling, blank-line
insertion, timestamps. No state semantics.

### TestSetBody (8 tests) — KEEP all
Body-text replacement — content invariants, file-input via `--body-file`,
`-`-as-stdin marker validation. No state semantics.

### TestMove (6 tests) — KEEP all
User-driven reorder within one sibling group (`--up`/`--down`/`--after`/
`--before`). The four tests that use the default fixture perform legal
same-zone moves (row 9's cross-zone divergence needs a fixture that
straddles the completed/active/DEFER boundaries — none of these do).
`test_move_reorders_siblings_every_direction` pins the enriched-JSON
"moved-task heading" regression from PR #53, orthogonal to §7.
- test_move_up
- test_move_down
- test_move_after_sibling
- test_move_before_stays_under_correct_parent
- test_move_after_stays_under_correct_parent
- test_move_reorders_siblings_every_direction

### TestOrgTimestampInactive (2 tests) — KEEP all
`--inactive` formatting. Utility surface.

### TestSetNext (9 tests) — KEEP all
`set-next` CLI surface: idempotent no-op, promotes first TODO,
subproject-heading rejection with "has subtasks" hint, JSON error+hint
shape. The subproject-rejection tests match §4.7 normative
(subproject-heading target → rejected) — row 8's divergent "project path
promotes a subproject heading" needs a scenario where `set-next` on a
PROJECT would end up picking a subproject child, which none of these
tests set up. All KEEP as CLI-surface pins.

### TestNextProjectGuard (9 tests) — KEEP all
NEXT-on-freestanding rejection (`add-task --state NEXT`,
`set-state ... NEXT`, `set-next` on standalone tasks). Pins the CLI
error text `"NEXT is only valid for an actionable item inside a project"`
(REASON constant on the class) plus rc=1 and no state change. This
matches I3 normatively for lone tasks — row 8's divergence about
`set-state NEXT` accepting SUBPROJECT HEADINGS is a different scenario
(these tests target *standalone* tasks under category headings).
- test_add_task_next_into_category_rejected
- test_add_task_next_into_inbox_rejected
- test_add_task_todo_into_category_still_works
- test_set_state_next_on_standalone_rejected
- test_set_state_next_dry_run_on_standalone_rejected
- test_set_next_on_standalone_rejected
- test_set_state_next_on_project_child_allowed
- test_set_next_on_project_child_allowed
- test_reject_json_shape

### TestSubproject (3 tests) — KEEP all
- test_parent_project_includes_subproject — `projects` view surface
- test_subtasks_of_subproject — `subtasks` view surface
- test_process_agent_tasks_removed — legacy-removal pin

### TestEdgeCasesOutOfBoundsIndex (6 tests) — KEEP all
`--index 999` on every mutation command: rc=1 + file unchanged (via
md5). Tier-2 does not exercise the `--index` flag at all; addressing
edge cases are CLI-surface.

### TestEdgeCasesNoMatch (5 tests) — KEEP all
Non-existent-substring targets: rc=1 across every mutation command.
Tier-2 uses generated headings that resolve; explicit-miss coverage is
CLI-surface (and cheap — the whole class runs in a fraction of a
second).

### TestEdgeCasesInvalidRefileTarget (1 test) — KEEP
`refile --to <nonexistent>` rc=1 + file unchanged. CLI addressing.

### TestIntegrationChain (1 test) — KEEP
`test_add_subtask_then_set_next_then_done` — multi-command scenario
end-to-end. Pins the reprogramming of state through a realistic user
flow (add → promote → close); not subsumed by any single tier-2 example.

### TestRename (5 tests) — KEEP all
`rename` — heading-only mutation, tag/priority/state preserved, `:ID:`
preserved, error surface. §4.10 annotation op (no state semantics
divergence).

### TestSetSchedule (7 tests) — KEEP all
`set-schedule` — SCHEDULED planning line, dry-run, clear, format
constraints. §4.10 annotation op.

### TestSetDeadline (6 tests) — KEEP all
`set-deadline` — DEADLINE planning line, dry-run, clear, format.
§4.10 annotation op.

### TestSetTags (6 tests) — KEEP all
Whole-list tag replacement, colon syntax, format constraints. §4.10.

### TestEdgeCasesNewCommandsOobIndex (4 tests) — KEEP all
`--index 999` on `rename`, `set-tags`, `set-schedule`, `set-deadline`:
rc=1 + file unchanged. Addressing edge cases on annotation ops (not
exercised by tier-2).

### TestEdgeCasesNewCommandsNoMatch (4 tests) — KEEP all
Non-existent substring on annotation ops: rc=1. CLI addressing.

### TestSetProperty (19 tests) — KEEP all
`set-property` — arbitrary key/value pairs into `:PROPERTIES:` drawer,
`--clear`, value validation, `AGENT_EFFORT`/`AGENT_MODEL` reserved-key
semantics, JSON envelope shape (`old_value`/`new_value` fields), file
targeting. Not modelled.

### TestTaskProperties (7 tests) — KEEP all
`show`/`subtasks` output of the `:PROPERTIES:` drawer (which keys
surface, formatting, category vs task treatment). Read-only projection.

### TestArchiveSingle (7 tests) — KEEP all
`archive` — happy path, `--dry-run`, active/recent-date/inside-active-
project rejections, no-match, ambiguous. §4.11 error surface + file I/O.
Row 13's "archiving over open severed tasks emits no warning"
divergence would need a fixture with an open severed subtree inside an
otherwise-closed archivable task — none of these do.

### TestArchiveBatch (3 tests) — KEEP all
`archive --all` batch happy path, dry-run, "nothing eligible" report.
Batch-mode + reporting surface.

### TestSiblingReordering (10 tests) — MIXED (9 DROP, 1 KEEP)
Pure custom-fixture skeleton-line-order assertions after `set-done`/
`set-state`/`set-next`. Every assertion here compares only file text via
`assert_line_before` (no stdout/stderr, no JSON, no error surface, no
LOGBOOK). The generated tier-2 conformance property covers exactly this
comparison — CLI file skeleton == current-mode model skeleton — across
random forests including all these state-transition cases.

- test_done_reorders_above_next — **DROP** (tier-2 conformance:
  `set_done` skeleton matches model; tier-1
  `test_minimal_move_preserves_active_interleaving` pins the property)
- test_done_auto_progress_reorders_both — **DROP** (tier-2:
  `set_done` + promotion; tier-1 `test_promotion_never_mints_a_second_front`)
- test_set_state_reorders — **DROP** (tier-2: `set_state` skeleton;
  tier-1 `test_invariants_preserved_by_any_operation_sequence`)
- test_set_next_reorders_promoted_task — **DROP** (tier-2: `set_next`
  skeleton)
- test_cancelled_sorts_with_done — **DROP** (tier-2: `set_state
  CANCELLED` skeleton; tier-1 `test_invariants_preserved_...`)
- test_waiting_defer_ordering — **DROP** (tier-2: `set_state
  WAITING/DEFER` skeleton)
- test_todo_to_waiting_preserves_sibling_position — **DROP** (tier-1
  `test_same_boundary_class_transition_never_moves_anyone` covers
  TODO→WAITING no-move; tier-2 confirms CLI matches)
- test_next_to_waiting_preserves_sibling_position — **KEEP+ANNOTATE
  row 1 (#34)** (asserts that a NEXT→WAITING keeps its original position
  when it was the only NEXT; the normative §4.1 rule sends the WAITING
  to the top of the active zone in that shape, so #34's stage-2c fix
  flips this)
- test_non_task_siblings_skip_reorder — **DROP** (tier-1
  `test_mixed_groups_are_never_reordered` covers this exactly)
- test_top_level_task_skip_reorder — **DROP** (tier-2: `set_state`
  skeleton on top-level singleton group)

### TestAddSubtaskStateReorder (9 tests) — MIXED (8 DROP, 1 KEEP+ANNOTATE)
Pure custom-fixture skeleton-line-order assertions after `add-subtask
--state X` for every X. Same shape as TestSiblingReordering: only
compares `assert_line_before` on file text. Tier-2 exercises
`add_subtask` with generated states + skeleton comparison. All
scenarios except `WAITING` here produce the same skeleton under current
and normative (append-last + sort ≡ arrival-in-zone for the fixtures
used).

- test_add_next_reorders_above_todo — **DROP** (tier-2: `add_subtask
  NEXT` skeleton)
- test_add_next_reorders_between_done_and_todo — **DROP** (tier-2)
- test_add_done_reorders_above_next — **DROP** (tier-2)
- test_add_cancelled_reorders_above_todo — **DROP** (tier-2)
- test_add_waiting_preserves_end_position — **KEEP+ANNOTATE row 1
  (#34)** (anchor, non-flipping: asserts that add-subtask WAITING lands AFTER existing TODO
  siblings; §4.1 arrival-in-zone puts a WAITING at the end of the
  active zone — same end position in this fixture — but the assertion
  is written to pin the append-last behavior explicitly, matching the
  regression note "the WAITING position invariant, see 3f0802b". #34's
  stage-2c work touches this code path so the pin is a useful anchor)
- test_add_defer_preserves_end_position — **DROP** (tier-2)
- test_add_todo_preserves_end_position — **DROP** (tier-2)
- test_add_next_to_empty_parent — **DROP** (tier-2)
- test_add_next_with_single_child — **DROP** (tier-2)

### TestRefileInvariants (9 tests) — KEEP all
Refile-repair invariants (NEXT demoted when moved out of project,
NEXT-parent-becomes-project → demote, --index disambiguation of
same-named duplicates, destination-sibling reorder, --category applies
same invariants, --dry-run mutates nothing, empty-parent handling).
Every test either asserts file skeleton with a scenario that current
and normative agree on (repair correctness) or asserts CLI-specific
--index/--dry-run behavior. Row 12 (refile --to first-match resolution)
is not pinned here — test_duplicate_heading_refile_demotes_moved_task_
not_existing uses --index to pick source, not --to to resolve
destination.

### TestAgendaView (14 tests) — KEEP all
`agenda-view` curated dashboard blocks (Calendar, Next Tasks, Tasks,
Waiting, Stuck Projects, Projects, Deferred, Tasks to Archive). Block
membership + rendering surface. Row 6 (view predicates read legacy
tags) sits in §5.2/§5.4 — but these tests exercise composite output
that would change in either direction under #40, and the fix work will
walk this class directly (§5's own tests). KEEP as regression pins;
the row-6 tag-machinery pin lives on TestSetState (write side).

### TestFixTimestamps (1 test) — KEEP
`fix-timestamps` utility — legacy timestamp reformat. No state semantics.

### TestFillText (14 tests) — KEEP all
`fill-text` body-line reflow — wrap width, list handling,
timestamp/property-drawer skipping, `--dry-run`. Utility surface.

### TestDelete (11 tests) — KEEP all
`delete` — exact-full-heading match (stricter than substring),
child-guard (must have no child heading, task or category), file
targeting, `--dry-run`, no-match. §4.12 CLI surface. Delete never
triggers promotion.

### TestMarkupAwareMatching (9 tests) — KEEP all
Substring matching over heading text stripped of org markup (`*bold*`,
`/italic/`, `~code~`). CLI addressing surface distinct from the model's
plain-string identity.

### TestRefileMarkupAware (5 tests) — KEEP all
Same but for refile source/destination addressing.

### TestUnescapeBodyNewlines (6 tests) — KEEP all
Body-text `\n` unescape handling in every body-accepting command. Body
surface.

### TestBodyFileInput (6 tests) — KEEP all
`--body-file`/`--body-file -` (stdin) input plumbing across body
commands. CLI I/O surface.

### TestRejectLiteralDash (3 tests) — KEEP all
CLI plumbing: reject a literal `-` as `--body` value (guides users to
`--body-file -` for stdin).

### TestBodyFlagOnBodyCommands (12 tests) — KEEP all
`--body` flag behavior on every body-accepting command (add-task,
add-subtask, add-note, add-event, append-body, set-body): validation,
error surface, precedence with `--body-file`.

### TestJsonInfrastructure (5 tests) — KEEP all
`--json` output routing (stdout-only, no interleaving with warnings,
exit-code discipline). JSON envelope surface.

### TestJsonSearch (9 tests) — KEEP all
JSON envelope for `search`.

### TestJsonAgenda (6 tests) — KEEP all
JSON envelope for `agenda`.

### TestJsonShow (10 tests) — KEEP all
JSON envelope for `show` — full task shape, category/kind fields.

### TestJsonSubtasks (5 tests) — KEEP all
JSON envelope for `subtasks`.

### TestJsonCategories (3 tests) — KEEP all
JSON envelope for `categories`.

### TestJsonProjects (4 tests) — KEEP all
JSON envelope for `projects`.

### TestListTags (7 tests) — KEEP all
`list-tags` view — with/without counts, exclusions, JSON shape. View
layer.

### TestJsonMutations (40 tests) — KEEP all
JSON envelope for every mutation command: `add-task`, `add-subtask`,
`set-done`, `set-cancelled`, `set-state`, `set-next`, `set-priority`,
`set-property`, `refile`, `move`, `rename`, `add-note`, `add-event`,
`append-body`, `set-body`, `archive`, `delete`, `set-schedule`,
`set-deadline`, `set-tags`. Every test checks JSON structure/fields —
JSON envelope shape is not part of tier-2's comparison surface. All KEEP.

### TestJsonNonAsciiEncoding (4 tests) — KEEP all
UTF-8 encoding of JSON output (bare chars, escaped chars, Emacs default
prin1 escapes). I/O layer.

### TestDaemonSaveChatter (2 tests) — KEEP all
Daemon `save-buffer` message suppression (avoid `Wrote ...` echo into
stderr). Daemon-mode I/O.

### TestDaemonRobustness (7 tests) — KEEP all
Daemon environment robustness: XDG_RUNTIME_DIR, socket-path escapes,
stale-socket recovery, orphan handling. Daemon lifecycle.

### TestDaemonConflictDetection (11 tests) — KEEP all
Concurrent write detection during a daemon dispatch (issue #27
conflict envelope: `file`/`partial`/`saved_files`). Daemon-mode
concurrency.

### TestDaemonSortStalenessRegression (1 test) — KEEP
Daemon-mode regression: sibling reorder after external file mutation.

### TestDaemonLifecycleCore (6 tests) — KEEP all
Daemon spawn/dispatch/reuse/idle-timeout core paths.

### TestDaemonManagementCommands (24 tests) — KEEP all
`daemon status`/`stop`/`gc` commands, path canonicalization,
`ALTERNATE_EDITOR` handling, TTL-expire orphan-guard. Includes the
harness fix (rename-file-based socket swap) noted in RESULT.md.

### TestRemovedCommands (2 tests) — KEEP all
Legacy-removal pins.

### TestSetTagsAddRemoveFlags (8 tests) — KEEP all
`set-tags --add`/`--remove` fine-grained tag operations, ordering
semantics.

### TestSetNextNonProjectParent (3 tests) — KEEP all
`set-next` rejection when the target's parent is a category heading
(not a task). Pins the error text and JSON error shape. Standalone from
row 8 (subproject-heading admittance).

### TestFullFlag (8 tests) — KEEP all
`--full` flag: fetch full task body across mutation commands; JSON
envelope enrichment.

### TestAutoStdin (5 tests) — KEEP all
Stdin auto-detection for body input (isatty vs pipe). CLI I/O.

### TestBatch (14 tests) — KEEP all
`--batch` mode command envelope: streaming input, per-command envelopes
in output, exit-code aggregation.

### TestBatchDelegation (13 tests) — KEEP all
Batch-mode delegates single-command semantics.

### TestBatchMixed (6 tests) — KEEP all
Batch with mixed successful/failing commands.

### TestBatchExtendedCommands (13 tests) — KEEP all
Batch-mode coverage for annotation ops, view ops, etc.

### TestBatchCoverageGaps (14 tests) — KEEP all
Batch envelope for the previously-uncovered commands.

### TestBatchLoudErrors (11 tests) — KEEP all
Batch: assertion that errors are surfaced (not swallowed) with structured
error envelopes.

### TestBatchAddTaskDeepCategory (2 tests) — KEEP all
Batch + `add-task --category` interaction (deep category path from within
batch).

### TestCorrectiveErrors (7 tests) — KEEP all
Error-surface polish: "did-you-mean" suggestions, category-hint output,
addressing failure guidance.

### TestJsonErrorsOnStdout (3 tests) — KEEP all
Error path in `--json` mode: error object on STDOUT (not stderr) plus
non-zero rc. JSON envelope surface.

### TestMutationTaskField (6 tests) — KEEP all
Mutation envelopes include the `task` field (post-op full task
representation). JSON envelope enrichment surface.

### TestAddSessionId (3 tests) — KEEP all
`add-session-id` command — appending a session id to task
`:PROPERTIES:`. Not modelled.

### TestGetSessionIds (3 tests) — KEEP all
`get-session-ids` reader — listing recorded session ids.

### TestStableIdAddressing (7 tests) — KEEP all
Addressing tasks by their `:ID:` property across every command. CLI
addressing surface distinct from the model's name-based identity.

### TestOutline (19 tests) — KEEP all
`outline` view — the forest-skeleton projection (§5.5). Read-only
projection.

### TestOutlineEvents (3 tests) — KEEP all
`outline` interaction with `add-event` (calendar events surface in the
outline). View layer.

### TestReadIdentity (18 tests) — KEEP all
`read-identity` command — identity-file parsing surface used by the
agent-notes machinery. Not modelled.

### TestSyncConflictWarning (19 tests) — KEEP all
Batch-mode file-conflict warnings (issue #27 warnings channel). Warnings
are NEVER compared by tier-2 per the ruling in `test_gtd_conformance.py`
header.

### TestSyncConflictWarningDaemon (2 tests) — KEEP all
Daemon-mode variant of sync-conflict warnings.

### TestRenderFile (14 tests) — KEEP all
`render-file` HTML projection with ORG_DIRECTORY scoping. View layer.

## Summary counts

- **KEEP: 745 tests** (base default; every CLI-surface test not
  otherwise classified)
- **KEEP+ANNOTATE: 9 tests** (all §7-row pins):
  - Row 1 (#34): 2 — TestSiblingReordering::test_next_to_waiting_
    preserves_sibling_position, TestAddSubtaskStateReorder::
    test_add_waiting_preserves_end_position
  - Row 5 (#39): 1 — TestSetState::test_without_reason_does_not_add_
    reason_note
  - Row 6 (#40): 2 — TestSetState::test_waiting_adds_tag,
    TestSetState::test_defer_to_waiting_cleans_defer_tag
  - Row 7 (#41): 4 — TestSetPriority::test_change_priority_a_to_c,
    TestSetPriority::test_clear_priority, TestSetPriority::
    test_invalid_priority, TestSetPriority::test_change_existing_
    priority
- **DROP: 17 tests** (all subsumed by tier-1/tier-2):
  - TestSiblingReordering: 9 tests (all except
    `test_next_to_waiting_preserves_sibling_position`)
  - TestAddSubtaskStateReorder: 8 tests (all except
    `test_add_waiting_preserves_end_position`)

**Total: 745 + 9 + 17 = 771** ✓ (matches
`grep -c "def test_" test_org_gtd_cli.py` at f63ab81)

## Post-migration coverage note

The reference model (`gtd_reference_model.py`) covers SEMANTICS.md §§2-6
state semantics only. The 745 KEEP tests carry:
- JSON envelope shape for every command (tier-2 does not compare)
- Warnings-channel content (tier-2 does not compare, per the 2026-07-28
  warnings ruling)
- Refile side_effects (tier-2 does not compare per the 2026-07-31 parked
  ruling on unreported refile repairs, now row 12)
- CLI error text, hint text, rc-class beyond ok/fail-1 (rc=2 for
  ambiguous, rc=1 for various failure classes)
- LOGBOOK format details (timestamps, escape backslashes, state notes)
- File-format specifics (blank-line handling, priority-cookie
  positioning, tag ordering)
- Addressing edge cases: `--index`, `--id`, category-path lookup,
  substring vs exact, markup-aware matching, self-match handling
- Daemon lifecycle, socket handling, conflict detection
- Batch-mode command envelope + streaming semantics
- View-layer rendering (agenda, agenda-view, outline, search, show,
  subtasks, categories, projects, list-tags, render-file)
- Body-text handling (fill, unescape, body-file input, body-flag
  precedence)
- Session-id, identity, sync-conflict warnings — all outside modelled
  semantics

The 9 KEEP+ANNOTATE tests are anchors for stage-2c #34 / #39 / #40 /
#41 work: each stage-2c PR grep-finds its `# pins §7 row N (#NN)`
pointers, flips the assertion, and retires the pin with the row.
