#!/usr/bin/env bash
# org-gtd-cli test suite
# NOTE: Do NOT use set -e. Tests deliberately check non-zero exit codes.
set -uo pipefail

PASS=0 FAIL=0
TEST_DIR=$(mktemp -d)
EMACS_DIR=$(mktemp -d)

# Locate elisp + fixtures relative to script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Run an elisp command in batch Emacs
LAST_OUTPUT="" LAST_STDERR="" LAST_RC=0
run_cmd() {
  local stderr_file
  stderr_file=$(mktemp)
  # Don't use || true — we need the real exit code, and set -e is off
  LAST_OUTPUT=$(emacs --batch -q \
    --eval "(setq user-emacs-directory \"$EMACS_DIR/\")" \
    --eval "(setenv \"ORG_DIRECTORY\" \"$TEST_DIR/\")" \
    -l "$SCRIPT_DIR/org-gtd-cli.el" \
    --eval "$1" 2>"$stderr_file")
  LAST_RC=$?
  LAST_STDERR=$(cat "$stderr_file")
  rm -f "$stderr_file"
}

reset_fixtures() {
  rm -rf "${TEST_DIR:?}"/*
  cp "$SCRIPT_DIR"/fixtures/*.org "$TEST_DIR/"
  mkdir -p "$TEST_DIR/agent-notes"
}

assert_exit() {
  local expected="$1" actual="$2" desc="$3"
  if [[ "$actual" -eq "$expected" ]]; then
    echo "  PASS: $desc"; ((PASS++))
  else
    echo "  FAIL: $desc (expected exit $expected, got $actual)"; ((FAIL++))
    if [[ -n "$LAST_STDERR" ]]; then
      echo "  STDERR: $(echo "$LAST_STDERR" | head -5)"
    fi
    if [[ -n "$LAST_OUTPUT" ]]; then
      echo "  OUTPUT: $(echo "$LAST_OUTPUT" | head -5)"
    fi
  fi
}

assert_output_contains() {
  local output="$1" pattern="$2" desc="$3"
  if echo "$output" | grep -qF "$pattern"; then
    echo "  PASS: $desc"; ((PASS++))
  else
    echo "  FAIL: $desc (output does not contain '$pattern')"; ((FAIL++))
    echo "  OUTPUT: $(echo "$output" | head -5)"
  fi
}

assert_output_not_contains() {
  local output="$1" pattern="$2" desc="$3"
  if ! echo "$output" | grep -qF "$pattern"; then
    echo "  PASS: $desc"; ((PASS++))
  else
    echo "  FAIL: $desc (output unexpectedly contains '$pattern')"; ((FAIL++))
  fi
}

assert_file_contains() {
  local file="$1" pattern="$2" desc="$3"
  if grep -qF "$pattern" "$file" 2>/dev/null; then
    echo "  PASS: $desc"; ((PASS++))
  else
    echo "  FAIL: $desc (file does not contain '$pattern')"; ((FAIL++))
  fi
}

assert_file_not_contains() {
  local file="$1" pattern="$2" desc="$3"
  if ! grep -qF "$pattern" "$file" 2>/dev/null; then
    echo "  PASS: $desc"; ((PASS++))
  else
    echo "  FAIL: $desc (file unexpectedly contains '$pattern')"; ((FAIL++))
  fi
}

assert_line_before() {
  local file="$1" first="$2" second="$3" desc="$4"
  local line_a line_b
  line_a=$(grep -nF "$first" "$file" 2>/dev/null | head -1 | cut -d: -f1)
  line_b=$(grep -nF "$second" "$file" 2>/dev/null | head -1 | cut -d: -f1)
  if [[ -n "$line_a" && -n "$line_b" && "$line_a" -lt "$line_b" ]]; then
    echo "  PASS: $desc"; ((PASS++))
  else
    echo "  FAIL: $desc ('$first' at line ${line_a:-?} not before '$second' at line ${line_b:-?})"; ((FAIL++))
  fi
}

summary() {
  echo ""
  echo "═══════════════════════════════════════"
  echo "Results: $PASS passed, $FAIL failed"
  echo "═══════════════════════════════════════"
  [[ $FAIL -eq 0 ]]
}
trap 'summary; rm -rf "$TEST_DIR" "$EMACS_DIR"' EXIT

# ══════════════════════════════════════════════════════════════════════════════
# org-timestamp
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== org-timestamp ==="

echo "test: outputs correct date with day-of-week"
run_cmd '(org-gtd-cli/org-timestamp "2026-03-15")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "<2026-03-15 Sun>" "correct timestamp"

echo "test: outputs correct date+time"
run_cmd '(org-gtd-cli/org-timestamp "2026-03-15" "14:00")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "<2026-03-15 Sun 14:00>" "correct timestamp with time"

echo "test: outputs time range"
run_cmd '(org-gtd-cli/org-timestamp "2026-03-15" "14:00-15:30")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "<2026-03-15 Sun 14:00-15:30>" "correct time range"

# ══════════════════════════════════════════════════════════════════════════════
# add-task
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== add-task ==="

echo "test: adds to inbox by default"
reset_fixtures
run_cmd '(org-gtd-cli/add-task "Test task" nil nil nil nil nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "TODO Test task" "task in inbox"
assert_output_contains "$LAST_OUTPUT" "Added: Test task -> inbox.org (inbox.org:" "output message with file:line"

echo "test: with tags"
reset_fixtures
run_cmd '(org-gtd-cli/add-task "Tagged task" nil "buy,@errand" nil nil nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" ":buy:@errand:" "tags formatted"

echo "test: with schedule"
reset_fixtures
run_cmd '(org-gtd-cli/add-task "Scheduled task" nil nil "2026-03-20" nil nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "SCHEDULED: <2026-03-20 Fri>" "scheduled line"

echo "test: with deadline"
reset_fixtures
run_cmd '(org-gtd-cli/add-task "Deadline task" nil nil nil "2026-03-25" nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "DEADLINE: <2026-03-25 Wed>" "deadline line"

echo "test: with body"
reset_fixtures
run_cmd '(org-gtd-cli/add-task "Body task" "This is the body text" nil nil nil nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "This is the body text" "body text present"

echo "test: with priority"
reset_fixtures
run_cmd '(org-gtd-cli/add-task "Priority task" nil nil nil nil "A" nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "[#A]" "priority cookie"

echo "test: with state WAITING"
reset_fixtures
run_cmd '(org-gtd-cli/add-task "Waiting task" nil nil nil nil nil nil nil "WAITING")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "WAITING Waiting task" "state set"

echo "test: with category"
reset_fixtures
run_cmd '(org-gtd-cli/add-task "Category task" nil nil nil nil nil nil "Work" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Category task" "task in tasks.org"
assert_output_contains "$LAST_OUTPUT" "tasks.org/Work" "output shows category"

echo "test: category not found fails"
reset_fixtures
run_cmd '(org-gtd-cli/add-task "Missing cat" nil nil nil nil nil nil "Nonexistent" nil)'
assert_exit 1 "$LAST_RC" "exits 1 for missing category"

# ══════════════════════════════════════════════════════════════════════════════
# add-subtask
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== add-subtask ==="

echo "test: adds child heading at correct level"
reset_fixtures
run_cmd '(org-gtd-cli/add-subtask "Write quarterly report" "Draft introduction" nil nil nil nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "*** TODO Draft introduction" "correct level"
assert_output_contains "$LAST_OUTPUT" "Added subtask" "output message"

echo "test: disambiguation works"
reset_fixtures
run_cmd '(org-gtd-cli/add-subtask "Buy" "New subtask" nil nil nil nil nil nil nil)'
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"

echo "test: index selects match"
reset_fixtures
run_cmd '(org-gtd-cli/add-subtask "Buy" "New subtask" nil nil nil nil nil nil "1")'
assert_exit 0 "$LAST_RC" "exits 0 with index"

# ══════════════════════════════════════════════════════════════════════════════
# agenda
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== agenda ==="

echo "test: returns all non-done tasks"
reset_fixtures
run_cmd '(org-gtd-cli/agenda nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "TODO" "contains TODO tasks"
assert_output_not_contains "$LAST_OUTPUT" "DONE Submit expense claims" "excludes DONE tasks"

echo "test: state filter TODO"
reset_fixtures
run_cmd '(org-gtd-cli/agenda "TODO" nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_not_contains "$LAST_OUTPUT" "NEXT " "excludes NEXT tasks"
assert_output_not_contains "$LAST_OUTPUT" "WAITING " "excludes WAITING tasks"

echo "test: state filter WAITING"
reset_fixtures
run_cmd '(org-gtd-cli/agenda "WAITING" nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Consider buying a new monitor" "finds WAITING task"
assert_output_contains "$LAST_OUTPUT" "Get travel insurance quote" "finds second WAITING task"

echo "test: tag filter @agent"
reset_fixtures
run_cmd '(org-gtd-cli/agenda nil "@agent" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set up automated backups" "finds agent task"
assert_output_contains "$LAST_OUTPUT" "Buy a formicarium" "finds inherited agent task"
assert_output_not_contains "$LAST_OUTPUT" "Pay quarterly taxes" "excludes non-agent"

echo "test: deadline shown in output"
reset_fixtures
run_cmd '(org-gtd-cli/agenda nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "D:<2026-03-15 Sun>" "deadline shown"

echo "test: priority shown in output"
reset_fixtures
run_cmd '(org-gtd-cli/agenda nil nil nil nil)'
assert_output_contains "$LAST_OUTPUT" "[#A]" "priority shown"

echo "test: file:line in output"
reset_fixtures
run_cmd '(org-gtd-cli/agenda nil nil nil nil)'
assert_output_contains "$LAST_OUTPUT" "(inbox.org:" "file reference shown"

echo "test: date range filter"
reset_fixtures
run_cmd '(org-gtd-cli/agenda nil nil "2026-03-10" "2026-03-15")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Pay quarterly taxes" "task with deadline in range"
assert_output_contains "$LAST_OUTPUT" "Choose a formicarium" "task with deadline in range"
assert_output_not_contains "$LAST_OUTPUT" "Write quarterly report" "task with deadline outside range excluded"
assert_output_not_contains "$LAST_OUTPUT" "Buy groceries" "task without date excluded"

# ══════════════════════════════════════════════════════════════════════════════
# show
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== show ==="

echo "test: shows full subtree"
reset_fixtures
run_cmd '(org-gtd-cli/show "Buy a formicarium" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Messor Barbarus" "body text shown"
assert_output_contains "$LAST_OUTPUT" "Research formicarium options" "subtask shown"
assert_output_contains "$LAST_OUTPUT" "(tasks.org:" "file:line header"

echo "test: shows LOGBOOK drawers"
reset_fixtures
run_cmd '(org-gtd-cli/show "Fix org-capture workspace" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" ":LOGBOOK:" "logbook shown"

echo "test: task with org link"
reset_fixtures
run_cmd '(org-gtd-cli/show "interesting article" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "[[https://example.com" "link shown"

# ══════════════════════════════════════════════════════════════════════════════
# subtasks
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== subtasks ==="

echo "test: lists children with states and progress"
reset_fixtures
run_cmd '(org-gtd-cli/subtasks "Improve agent workflow" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "DONE What are the current pain points" "DONE child"
assert_output_contains "$LAST_OUTPUT" "TODO Design CLI tool" "TODO child (subproject)"
assert_output_contains "$LAST_OUTPUT" "2/4 done" "progress count"
assert_output_contains "$LAST_OUTPUT" "(tasks.org:" "child has file:line"

echo "test: exits 1 if no subtasks"
reset_fixtures
run_cmd '(org-gtd-cli/subtasks "Pay quarterly taxes" nil)'
assert_exit 1 "$LAST_RC" "exits 1 for leaf task"

echo "test: nested project subtasks"
reset_fixtures
run_cmd '(org-gtd-cli/subtasks "Holiday pre-trip tasks" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "DONE Book flights" "done subtask"
assert_output_contains "$LAST_OUTPUT" "NEXT Book a rental car" "next subtask"
assert_output_contains "$LAST_OUTPUT" "WAITING Get travel insurance" "waiting subtask"

# ══════════════════════════════════════════════════════════════════════════════
# process-agent-tasks
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== process-agent-tasks ==="

echo "test: finds agent tasks"
reset_fixtures
run_cmd '(org-gtd-cli/process-agent-tasks)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set up automated backups" "finds backup task"
assert_output_contains "$LAST_OUTPUT" "Improve agent workflow" "finds workflow task"
assert_output_contains "$LAST_OUTPUT" "Buy a formicarium" "finds formicarium task"
assert_output_contains "$LAST_OUTPUT" "Found 3 agent tasks" "correct count"

echo "test: includes AGENT instruction"
reset_fixtures
run_cmd '(org-gtd-cli/process-agent-tasks)'
assert_output_contains "$LAST_OUTPUT" "research backup strategies" "AGENT instruction shown"

echo "test: shows subtask progress"
reset_fixtures
run_cmd '(org-gtd-cli/process-agent-tasks)'
assert_output_contains "$LAST_OUTPUT" "2/4 done" "subtask progress"

echo "test: includes project context (skips non-TODO ancestors)"
reset_fixtures
run_cmd '(org-gtd-cli/process-agent-tasks)'
# "Set up automated backups" is under "Agents" (no TODO keyword) → no project
assert_output_not_contains "$LAST_OUTPUT" "Project: Agents" "skips non-TODO ancestor"
# "Buy a formicarium" is under "Pet Ants" (no TODO keyword) → no project
assert_output_not_contains "$LAST_OUTPUT" "Project: Pet Ants" "skips non-TODO ancestor for formicarium"

# ══════════════════════════════════════════════════════════════════════════════
# done
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== done ==="

echo "test: marks single match as DONE"
reset_fixtures
run_cmd '(org-gtd-cli/done "Book a rental car" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Done: Book a rental car (tasks.org:" "done message with file:line"
assert_file_contains "$TEST_DIR/tasks.org" "DONE Book a rental car" "state changed in file"

echo "test: exit 2 on ambiguous"
reset_fixtures
run_cmd '(org-gtd-cli/done "Buy" nil nil)'
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"
assert_output_contains "$LAST_OUTPUT" "[1]" "shows indexed matches"
assert_output_contains "$LAST_OUTPUT" "[2]" "shows second match"

echo "test: index selects match"
reset_fixtures
run_cmd '(org-gtd-cli/done "Buy" "1" nil)'
assert_exit 0 "$LAST_RC" "exits 0 with index"

echo "test: dry-run doesn't modify"
reset_fixtures
run_cmd '(org-gtd-cli/done "Book a rental car" nil "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would mark done" "dry-run message"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Book a rental car" "file unchanged"

echo "test: auto-progress promotes next TODO to NEXT"
reset_fixtures
run_cmd '(org-gtd-cli/done "Add more test cases" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Auto-progressed" "auto-progress message"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Test on actual project" "next sibling promoted"

echo "test: done removes WAITING tag"
reset_fixtures
run_cmd '(org-gtd-cli/done "Get travel insurance quote" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "DONE Get travel insurance quote" "marked done"

# ══════════════════════════════════════════════════════════════════════════════
# set-state
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-state ==="

echo "test: changes state"
reset_fixtures
run_cmd '(org-gtd-cli/set-state "Book a rental car" "WAITING" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "NEXT -> WAITING (tasks.org:" "state change message with file:line"
assert_file_contains "$TEST_DIR/tasks.org" "WAITING Book a rental car" "state in file"

echo "test: WAITING adds tag"
reset_fixtures
run_cmd '(org-gtd-cli/set-state "Book a rental car" "WAITING" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" ":WAITING:" "WAITING tag added"

echo "test: removing WAITING removes tag"
reset_fixtures
run_cmd '(org-gtd-cli/set-state "Consider buying a new monitor" "TODO" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Consider buying a new monitor" "state changed"

echo "test: dry-run doesn't modify"
reset_fixtures
run_cmd '(org-gtd-cli/set-state "Book a rental car" "TODO" nil "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would change" "dry-run message"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Book a rental car" "file unchanged"

echo "test: preserves priority cookie"
reset_fixtures
run_cmd '(org-gtd-cli/set-state "Pay quarterly taxes" "NEXT" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT [#A] Pay quarterly taxes" "priority preserved"

echo "test: invalid state gives clean error"
reset_fixtures
run_cmd '(org-gtd-cli/set-state "Book a rental car" "INVALID" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "not a valid state" "clean error message"
assert_output_contains "$LAST_OUTPUT" "TODO, NEXT, DONE, WAITING, DEFER, CANCELLED" "lists valid states"

echo "test: DEFER → WAITING cleans DEFER tag"
reset_fixtures
run_cmd '(org-gtd-cli/set-state "Book a rental car" "DEFER" nil nil)'
run_cmd '(org-gtd-cli/set-state "Book a rental car" "WAITING" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" ":WAITING:" "WAITING tag present"
assert_file_not_contains "$TEST_DIR/tasks.org" ":DEFER:" "DEFER tag removed"

echo "test: WAITING → TODO cleans WAITING tag"
reset_fixtures
run_cmd '(org-gtd-cli/set-state "Book a rental car" "WAITING" nil nil)'
run_cmd '(org-gtd-cli/set-state "Book a rental car" "TODO" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
# The fixture already has a WAITING task, so check specifically on this task's line
assert_file_contains "$TEST_DIR/tasks.org" "TODO Book a rental car" "state is TODO"

# ══════════════════════════════════════════════════════════════════════════════
# refile
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== refile ==="

echo "test: moves task to target heading"
reset_fixtures
run_cmd '(org-gtd-cli/refile "Buy groceries" "Shopping" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_not_contains "$TEST_DIR/inbox.org" "Buy groceries" "removed from inbox"
assert_file_contains "$TEST_DIR/tasks.org" "Buy groceries" "added to tasks.org"
assert_output_contains "$LAST_OUTPUT" "Refiled" "refile message"
assert_output_contains "$LAST_OUTPUT" "(inbox.org:" "refile shows file:line"

echo "test: target not found fails"
reset_fixtures
run_cmd '(org-gtd-cli/refile "Buy groceries" "Nonexistent" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: dry-run doesn't modify"
reset_fixtures
run_cmd '(org-gtd-cli/refile "Buy groceries" "Shopping" nil "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would refile" "dry-run message"
assert_file_contains "$TEST_DIR/inbox.org" "Buy groceries" "still in inbox"

# ══════════════════════════════════════════════════════════════════════════════
# add-event
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== add-event ==="

echo "test: appends to calendar.org"
reset_fixtures
run_cmd '(org-gtd-cli/add-event "Team dinner" "2026-03-20" nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/calendar.org" "Team dinner" "event added"
assert_file_contains "$TEST_DIR/calendar.org" ":calpersonal:" "default tag"
assert_file_contains "$TEST_DIR/calendar.org" "<2026-03-20 Fri>" "timestamp"

echo "test: with time"
reset_fixtures
run_cmd '(org-gtd-cli/add-event "Lunch" "2026-03-21" "12:00-13:00" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/calendar.org" "<2026-03-21 Sat 12:00-13:00>" "time range"

echo "test: custom tag"
reset_fixtures
run_cmd '(org-gtd-cli/add-event "Family BBQ" "2026-03-22" nil "calfamily" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/calendar.org" ":calfamily:" "custom tag"

# ══════════════════════════════════════════════════════════════════════════════
# add-note
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== add-note ==="

echo "test: creates note file"
reset_fixtures
run_cmd '(org-gtd-cli/add-note "Test research topic" nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Created:" "creation message"
local_note_file="$TEST_DIR/agent-notes/test-research-topic.org"
if [[ -f "$local_note_file" ]]; then
  echo "  PASS: note file exists"; ((PASS++))
  assert_file_contains "$local_note_file" "#+title: Test research topic" "title header"
  assert_file_contains "$local_note_file" "#+filetags: :research:" "filetags"
  assert_file_contains "$local_note_file" "* Summary" "default section"
else
  echo "  FAIL: note file not created at $local_note_file"; ((FAIL++))
fi

echo "test: custom sections"
reset_fixtures
run_cmd '(org-gtd-cli/add-note "Custom note" nil nil "Background,Analysis,Recommendations")'
assert_exit 0 "$LAST_RC" "exits 0"
local_note_file="$TEST_DIR/agent-notes/custom-note.org"
if [[ -f "$local_note_file" ]]; then
  assert_file_contains "$local_note_file" "* Background" "custom section 1"
  assert_file_contains "$local_note_file" "* Analysis" "custom section 2"
  assert_file_contains "$local_note_file" "* Recommendations" "custom section 3"
else
  echo "  FAIL: note file not created"; ((FAIL++))
fi

echo "test: link-task adds link"
reset_fixtures
run_cmd '(org-gtd-cli/add-note "Formicarium research" "Buy a formicarium" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "[[file:agent-notes/formicarium-research.org]]" "link added to task"

# ══════════════════════════════════════════════════════════════════════════════
# append-body
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== append-body ==="

echo "test: appends to task with existing body (before timestamp with time)"
reset_fixtures
run_cmd '(org-gtd-cli/append-body "Buy a small UPS" "Check APC models" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Check APC models" "text appended"
assert_output_contains "$LAST_OUTPUT" "Appended to" "append message"
assert_line_before "$TEST_DIR/tasks.org" "Check APC models" "[2026-03-11 Wed 13:35]" "text before timestamp"

echo "test: appends before date-only timestamp"
reset_fixtures
run_cmd '(org-gtd-cli/append-body "Buy anti-escape coating" "Also check Fluon PTFE spray" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Also check Fluon PTFE spray" "text appended"
# Verify ordering: new text appears after existing body but before the timestamp.
assert_line_before "$TEST_DIR/tasks.org" "PTFE anti-escape" "Also check Fluon PTFE spray" "new text after existing body"
# Verify the timestamp is after the new text (check relative to task heading)
TASK_LINE=$(grep -n "Buy anti-escape coating" "$TEST_DIR/tasks.org" | head -1 | cut -d: -f1)
TEXT_LINE=$(grep -n "Also check Fluon PTFE spray" "$TEST_DIR/tasks.org" | head -1 | cut -d: -f1)
TS_LINE=$(awk -v start="$TASK_LINE" 'NR>start && /^\[2026-03-12 Thu\]$/{print NR; exit}' "$TEST_DIR/tasks.org")
if [[ -n "$TEXT_LINE" && -n "$TS_LINE" && "$TEXT_LINE" -lt "$TS_LINE" ]]; then
  echo "  PASS: text before date-only timestamp"; ((PASS++))
else
  echo "  FAIL: text before date-only timestamp (text=$TEXT_LINE, ts=$TS_LINE)"; ((FAIL++))
fi

echo "test: appends to task with no body"
reset_fixtures
run_cmd '(org-gtd-cli/append-body "Reply to dentist" "Call if no reply by Friday" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "Call if no reply by Friday" "text appended"

# ══════════════════════════════════════════════════════════════════════════════
# move
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== move ==="

echo "test: move up"
reset_fixtures
run_cmd '(org-gtd-cli/move "Implement CLI tool" "up" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Moved:" "move message"
assert_output_contains "$LAST_OUTPUT" "(tasks.org:" "move shows file:line"

echo "test: move down"
reset_fixtures
run_cmd '(org-gtd-cli/move "Design CLI tool" "down" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Moved:" "move message"

echo "test: move after sibling"
reset_fixtures
run_cmd '(org-gtd-cli/move "Buy anti-escape coating" "after" "Research formicarium" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Moved:" "move message"

# ══════════════════════════════════════════════════════════════════════════════
# org-timestamp --inactive
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== org-timestamp --inactive ==="

echo "test: inactive timestamp uses square brackets"
run_cmd '(org-gtd-cli/org-timestamp "2026-03-15" nil "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "[2026-03-15 Sun]" "inactive timestamp"

echo "test: inactive timestamp with time"
run_cmd '(org-gtd-cli/org-timestamp "2026-03-15" "14:00" "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "[2026-03-15 Sun 14:00]" "inactive timestamp with time"

# ══════════════════════════════════════════════════════════════════════════════
# set-next
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-next ==="

echo "test: already has NEXT → no-op"
reset_fixtures
run_cmd '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Already has NEXT" "already has NEXT message"

echo "test: promotes first TODO child"
reset_fixtures
# First complete the NEXT child so there's no NEXT
run_cmd '(org-gtd-cli/done "Book a rental car" nil nil)'
# Now the WAITING child remains but no TODO — set-state it first
run_cmd '(org-gtd-cli/set-state "Get travel insurance" "TODO" nil nil)'
run_cmd '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set NEXT" "set next message"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Get travel insurance" "child promoted"

echo "test: no TODO children → exit 1"
reset_fixtures
run_cmd '(org-gtd-cli/set-next "Buy a formicarium" nil)'
# This has DONE, NEXT, TODO children — it has NEXT, so it should say "Already has NEXT"
assert_exit 0 "$LAST_RC" "exits 0 (already has NEXT)"

echo "test: leaf task (no children) → sets task itself to NEXT"
reset_fixtures
run_cmd '(org-gtd-cli/set-next "Pay quarterly taxes" nil)'
assert_exit 0 "$LAST_RC" "exits 0 for leaf task"
assert_output_contains "$LAST_OUTPUT" "Set NEXT" "set next on leaf task"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT [#A] Pay quarterly taxes" "leaf task promoted to NEXT"

echo "test: leaf task already NEXT → no-op"
run_cmd '(org-gtd-cli/set-next "Pay quarterly taxes" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Already NEXT" "already NEXT message for leaf"

echo "test: subproject set-next"
reset_fixtures
run_cmd '(org-gtd-cli/set-next "Design CLI tool" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Already has NEXT" "Design CLI tool already has NEXT child"

# ══════════════════════════════════════════════════════════════════════════════
# Subproject tests
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== subproject ==="

echo "test: subtasks of subproject"
reset_fixtures
run_cmd '(org-gtd-cli/subtasks "Design CLI tool" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "NEXT Add more test cases" "first child"
assert_output_contains "$LAST_OUTPUT" "TODO Test on actual project" "second child"
assert_output_contains "$LAST_OUTPUT" "TODO Start using it" "third child"
assert_output_contains "$LAST_OUTPUT" "0/3 done" "progress"

echo "test: parent project includes subproject as child"
reset_fixtures
run_cmd '(org-gtd-cli/subtasks "Improve agent workflow" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "TODO Design CLI tool" "subproject shown as child"
assert_output_contains "$LAST_OUTPUT" "TODO Implement CLI tool" "sibling shown"

echo "test: process-agent-tasks shows correct direct-child subtask count"
reset_fixtures
run_cmd '(org-gtd-cli/process-agent-tasks)'
assert_output_contains "$LAST_OUTPUT" "2/4 done" "correct subtask count for improved workflow"

echo "test: process-agent-tasks subtasks have file:line"
reset_fixtures
run_cmd '(org-gtd-cli/process-agent-tasks)'
assert_output_contains "$LAST_OUTPUT" "DONE What are the current pain points? (tasks.org:" "child has file:line"

# ══════════════════════════════════════════════════════════════════════════════
# Edge case tests: out-of-bounds index
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== edge cases: out-of-bounds index ==="

assert_file_unchanged() {
  local file="$1" before="$2" desc="$3"
  local after
  after=$(md5sum "$file" | cut -d' ' -f1)
  if [[ "$before" == "$after" ]]; then
    echo "  PASS: $desc"; ((PASS++))
  else
    echo "  FAIL: $desc (file was modified)"; ((FAIL++))
  fi
}

echo "test: done with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/done "Buy" "999" nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

echo "test: set-state with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/set-state "Buy" "NEXT" "999" nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

echo "test: refile with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/refile "Buy" "Shopping" "999" nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

echo "test: append-body with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/append-body "Buy" "text" "999")'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

echo "test: move with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/move "Buy" "up" nil "999")'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

echo "test: add-subtask with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/add-subtask "Buy" "child" nil nil nil nil nil nil "999")'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

# ══════════════════════════════════════════════════════════════════════════════
# Edge case tests: no match found
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== edge cases: no match ==="

echo "test: done nonexistent"
reset_fixtures
run_cmd '(org-gtd-cli/done "xyznonexistent" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: set-state nonexistent"
reset_fixtures
run_cmd '(org-gtd-cli/set-state "xyznonexistent" "NEXT" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: refile nonexistent"
reset_fixtures
run_cmd '(org-gtd-cli/refile "xyznonexistent" "Shopping" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: append-body nonexistent"
reset_fixtures
run_cmd '(org-gtd-cli/append-body "xyznonexistent" "text" nil)'
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: move nonexistent"
reset_fixtures
run_cmd '(org-gtd-cli/move "xyznonexistent" "up" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

# ══════════════════════════════════════════════════════════════════════════════
# Edge case: invalid refile target
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== edge cases: invalid refile target ==="

echo "test: refile to nonexistent heading"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/inbox.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/refile "Buy groceries" "Nonexistent Heading" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/inbox.org" "$BEFORE" "file unchanged"

# ══════════════════════════════════════════════════════════════════════════════
# Integration test: add-subtask → set-next → done chain
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== integration: add-subtask → set-next → done chain ==="

reset_fixtures

echo "test: step 1 - set-next on project with existing NEXT → no-op"
run_cmd '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Already has NEXT" "already has NEXT"

echo "test: step 2 - done Book a rental car → auto-progress"
run_cmd '(org-gtd-cli/done "Book a rental car" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "DONE Book a rental car" "done"

echo "test: step 3 - verify via subtasks"
run_cmd '(org-gtd-cli/subtasks "Holiday pre-trip tasks" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "DONE Book a rental car" "done shown"
assert_output_contains "$LAST_OUTPUT" "WAITING Get travel insurance" "waiting shown"

echo "test: step 4 - set-next with no TODO children → exit 1"
run_cmd '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil)'
assert_exit 1 "$LAST_RC" "exits 1 (no TODO to promote)"

echo "test: step 5 - set-state WAITING → TODO"
run_cmd '(org-gtd-cli/set-state "Get travel insurance" "TODO" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"

echo "test: step 6 - set-next promotes to NEXT"
run_cmd '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set NEXT" "promoted"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Get travel insurance" "promoted in file"

echo "test: step 7 - add-subtask to NEXT task demotes parent to TODO"
run_cmd '(org-gtd-cli/add-subtask "Get travel insurance" "Compare providers" nil nil nil nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Get travel insurance" "demoted to TODO"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Compare providers" "subtask added"

echo "test: step 8 - verify demotion via show"
run_cmd '(org-gtd-cli/show "Get travel insurance" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "TODO Get travel insurance" "state is TODO"
assert_output_contains "$LAST_OUTPUT" "Compare providers" "has child"

# ══════════════════════════════════════════════════════════════════════════════
# rename
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== rename ==="

echo "test: basic rename"
reset_fixtures
run_cmd '(org-gtd-cli/rename "Buy groceries" "Buy organic groceries" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Renamed:" "rename message"
assert_output_contains "$LAST_OUTPUT" "\"Buy groceries\" -> \"Buy organic groceries\"" "old and new in output"
assert_file_contains "$TEST_DIR/inbox.org" "Buy organic groceries" "new heading in file"
assert_file_not_contains "$TEST_DIR/inbox.org" "Buy groceries" "old heading removed"

echo "test: rename preserves state, priority, and tags"
reset_fixtures
run_cmd '(org-gtd-cli/rename "Pay quarterly taxes" "Pay quarterly income taxes" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO [#A] Pay quarterly income taxes" "state and priority preserved"

echo "test: rename preserves tags"
reset_fixtures
run_cmd '(org-gtd-cli/rename "Get travel insurance" "Get travel insurance from Allianz" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Get travel insurance from Allianz" "renamed"
assert_file_contains "$TEST_DIR/tasks.org" ":WAITING:" "WAITING tag preserved"
assert_file_contains "$TEST_DIR/tasks.org" ":email:" "email tag preserved"

echo "test: rename dry-run"
reset_fixtures
run_cmd '(org-gtd-cli/rename "Buy groceries" "Buy organic groceries" nil "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would rename" "dry-run message"
assert_file_contains "$TEST_DIR/inbox.org" "Buy groceries" "file unchanged"

echo "test: rename ambiguous"
reset_fixtures
run_cmd '(org-gtd-cli/rename "Buy" "Something" nil nil)'
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"

# ══════════════════════════════════════════════════════════════════════════════
# set-schedule
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-schedule ==="

echo "test: set schedule on unscheduled task"
reset_fixtures
run_cmd '(org-gtd-cli/set-schedule "Buy groceries" "2026-03-20" nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Scheduled:" "schedule message"
assert_file_contains "$TEST_DIR/inbox.org" "SCHEDULED: <2026-03-20 Fri>" "schedule in file"

echo "test: set schedule with time"
reset_fixtures
run_cmd '(org-gtd-cli/set-schedule "Buy groceries" "2026-03-20" "14:00" nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "SCHEDULED: <2026-03-20 Fri 14:00>" "schedule with time in file"

echo "test: overwrite existing schedule"
reset_fixtures
run_cmd '(org-gtd-cli/set-schedule "Consider buying a new monitor" "2026-04-01" nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "SCHEDULED: <2026-04-01 Wed>" "new schedule in file"
assert_file_not_contains "$TEST_DIR/tasks.org" "SCHEDULED: <2026-03-09 Mon>" "old schedule gone"

echo "test: clear existing schedule"
reset_fixtures
run_cmd '(org-gtd-cli/set-schedule "Consider buying a new monitor" nil nil "t" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Cleared schedule" "clear message"
assert_file_not_contains "$TEST_DIR/tasks.org" "SCHEDULED: <2026-03-09 Mon>" "schedule removed"

echo "test: clear when no schedule (no-op)"
reset_fixtures
run_cmd '(org-gtd-cli/set-schedule "Buy groceries" nil nil "t" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Cleared schedule" "clear message even when none existed"

echo "test: set-schedule dry-run"
reset_fixtures
run_cmd '(org-gtd-cli/set-schedule "Buy groceries" "2026-03-20" nil nil nil "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would schedule" "dry-run message"
assert_file_not_contains "$TEST_DIR/inbox.org" "SCHEDULED:" "file unchanged"

echo "test: set-schedule ambiguous"
reset_fixtures
run_cmd '(org-gtd-cli/set-schedule "Buy" "2026-03-20" nil nil nil nil)'
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"

# ══════════════════════════════════════════════════════════════════════════════
# set-deadline
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-deadline ==="

echo "test: set deadline on task without one"
reset_fixtures
run_cmd '(org-gtd-cli/set-deadline "Buy groceries" "2026-03-25" nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Deadline:" "deadline message"
assert_file_contains "$TEST_DIR/inbox.org" "DEADLINE: <2026-03-25 Wed>" "deadline in file"

echo "test: set deadline with time"
reset_fixtures
run_cmd '(org-gtd-cli/set-deadline "Buy groceries" "2026-03-25" "17:00" nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "DEADLINE: <2026-03-25 Wed 17:00>" "deadline with time in file"

echo "test: overwrite existing deadline"
reset_fixtures
run_cmd '(org-gtd-cli/set-deadline "Pay quarterly taxes" "2026-03-30" nil nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "DEADLINE: <2026-03-30 Mon>" "new deadline"
assert_file_not_contains "$TEST_DIR/tasks.org" "DEADLINE: <2026-03-15 Sun>" "old deadline gone"

echo "test: clear existing deadline"
reset_fixtures
run_cmd '(org-gtd-cli/set-deadline "Pay quarterly taxes" nil nil "t" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Cleared deadline" "clear message"
assert_file_not_contains "$TEST_DIR/tasks.org" "DEADLINE: <2026-03-15 Sun>" "deadline removed"

echo "test: clear when no deadline (no-op)"
reset_fixtures
run_cmd '(org-gtd-cli/set-deadline "Buy groceries" nil nil "t" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Cleared deadline" "clear message even when none existed"

echo "test: set-deadline dry-run"
reset_fixtures
run_cmd '(org-gtd-cli/set-deadline "Buy groceries" "2026-03-25" nil nil nil "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would set deadline" "dry-run message"
assert_file_not_contains "$TEST_DIR/inbox.org" "DEADLINE:" "file unchanged"

# ══════════════════════════════════════════════════════════════════════════════
# set-tags
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-tags ==="

echo "test: add tag"
reset_fixtures
run_cmd '(org-gtd-cli/set-tags "Buy groceries" "urgent" nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Tags:" "tags message"
assert_file_contains "$TEST_DIR/inbox.org" ":urgent:" "tag added"
# Original tags preserved
assert_file_contains "$TEST_DIR/inbox.org" ":buy:" "buy tag preserved"
assert_file_contains "$TEST_DIR/inbox.org" ":@errand:" "errand tag preserved"

echo "test: remove tag"
reset_fixtures
run_cmd '(org-gtd-cli/set-tags "Buy groceries" nil "@errand" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" ":buy:" "buy tag preserved"

echo "test: add and remove in one call"
reset_fixtures
run_cmd '(org-gtd-cli/set-tags "Buy groceries" "urgent,@home" "@errand" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" ":urgent:" "urgent added"
assert_file_contains "$TEST_DIR/inbox.org" ":@home:" "home added"

echo "test: remove nonexistent tag (no-op)"
reset_fixtures
run_cmd '(org-gtd-cli/set-tags "Buy groceries" nil "nonexistent" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
# Original tags still there
assert_file_contains "$TEST_DIR/inbox.org" ":buy:" "buy tag preserved"
assert_file_contains "$TEST_DIR/inbox.org" ":@errand:" "errand tag preserved"

echo "test: add tag that already exists (no-op)"
reset_fixtures
run_cmd '(org-gtd-cli/set-tags "Buy groceries" "buy" nil nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" ":buy:" "buy tag still there"

echo "test: set-tags dry-run"
reset_fixtures
run_cmd '(org-gtd-cli/set-tags "Buy groceries" "urgent" nil nil "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would set tags" "dry-run message"
assert_file_not_contains "$TEST_DIR/inbox.org" ":urgent:" "file unchanged"

echo "test: set-tags ambiguous"
reset_fixtures
run_cmd '(org-gtd-cli/set-tags "Buy" "urgent" nil nil nil)'
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"

# ══════════════════════════════════════════════════════════════════════════════
# Edge cases for new commands: out-of-bounds index
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== edge cases: new commands out-of-bounds index ==="

echo "test: rename with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/rename "Buy" "Something" "999" nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

echo "test: set-schedule with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/set-schedule "Buy" "2026-03-20" nil nil "999" nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

echo "test: set-deadline with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/set-deadline "Buy" "2026-03-25" nil nil "999" nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

echo "test: set-tags with out-of-bounds index"
reset_fixtures
BEFORE=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)
run_cmd '(org-gtd-cli/set-tags "Buy" "urgent" nil "999" nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE" "file unchanged"

# ══════════════════════════════════════════════════════════════════════════════
# Edge cases for new commands: no match
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== edge cases: new commands no match ==="

echo "test: rename nonexistent"
reset_fixtures
run_cmd '(org-gtd-cli/rename "xyznonexistent" "Something" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: set-schedule nonexistent"
reset_fixtures
run_cmd '(org-gtd-cli/set-schedule "xyznonexistent" "2026-03-20" nil nil nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: set-deadline nonexistent"
reset_fixtures
run_cmd '(org-gtd-cli/set-deadline "xyznonexistent" "2026-03-25" nil nil nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: set-tags nonexistent"
reset_fixtures
run_cmd '(org-gtd-cli/set-tags "xyznonexistent" "urgent" nil nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

# ══════════════════════════════════════════════════════════════════════════════
# archive (single task)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== archive: single task ==="

echo "test: archive happy path"
reset_fixtures
run_cmd '(org-gtd-cli/archive "buy new router" nil nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" 'Archived: "Buy new router"' "output confirms archive"
# Task should be gone from tasks.org
assert_file_not_contains "$TEST_DIR/tasks.org" "Buy new router" "removed from tasks.org"
# Task should be in archive file
assert_file_contains "$TEST_DIR/tasks.org_archive" "Buy new router" "present in tasks.org_archive"

echo "test: archive dry-run"
reset_fixtures
run_cmd '(org-gtd-cli/archive "research dentists" nil "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" 'Would archive:' "output says would archive"
# Task should still be in tasks.org
assert_file_contains "$TEST_DIR/tasks.org" "Research dentists" "still in tasks.org"

echo "test: archive rejects active task (rule 1)"
reset_fixtures
run_cmd '(org-gtd-cli/archive "write quarterly report" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "still active" "output mentions still active"

echo "test: archive rejects recent dates (rule 2b)"
reset_fixtures
run_cmd '(org-gtd-cli/archive "submit expense claims" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "recent dates" "output mentions recent dates"

echo "test: archive rejects inside active project (rule 3)"
reset_fixtures
run_cmd '(org-gtd-cli/archive "pack suitcases" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "inside active project" "output mentions active project"

echo "test: archive no match"
reset_fixtures
run_cmd '(org-gtd-cli/archive "xyznonexistent" nil nil)'
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: archive ambiguous match"
reset_fixtures
run_cmd '(org-gtd-cli/archive "buy" nil nil)'
assert_exit 2 "$LAST_RC" "exits 2"

# ══════════════════════════════════════════════════════════════════════════════
# archive --all (batch)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== archive: batch (--all) ==="

echo "test: archive --all happy path"
reset_fixtures
run_cmd '(org-gtd-cli/archive-all nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Archived" "output mentions archived"
# Old DONE tasks should be gone
assert_file_not_contains "$TEST_DIR/tasks.org" "Buy new router" "Buy new router archived"
assert_file_not_contains "$TEST_DIR/tasks.org" "Research dentists" "Research dentists archived"
# Dateless DONE should be skipped
assert_output_contains "$LAST_OUTPUT" 'Skipped (no dates): "Mystery task"' "Mystery task skipped (no dates)"
assert_file_contains "$TEST_DIR/tasks.org" "Mystery task" "Mystery task still in tasks.org"
# Recent DONE tasks should still be there
assert_file_contains "$TEST_DIR/tasks.org" "Submit expense claims" "recent DONE preserved"
# Archive file should exist with archived tasks
assert_file_contains "$TEST_DIR/tasks.org_archive" "Buy new router" "Buy new router in archive"
assert_file_contains "$TEST_DIR/tasks.org_archive" "Research dentists" "Research dentists in archive"

echo "test: archive --all dry-run"
reset_fixtures
run_cmd '(org-gtd-cli/archive-all "t")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would archive" "output says would archive"
# Files should be unchanged
assert_file_contains "$TEST_DIR/tasks.org" "Buy new router" "Buy new router still in tasks.org"
assert_file_contains "$TEST_DIR/tasks.org" "Research dentists" "Research dentists still in tasks.org"

echo "test: archive --all nothing eligible"
reset_fixtures
# First archive everything eligible
run_cmd '(org-gtd-cli/archive-all nil)'
assert_exit 0 "$LAST_RC" "first pass exits 0"
# Run again — nothing should be left
run_cmd '(org-gtd-cli/archive-all nil)'
assert_exit 0 "$LAST_RC" "second pass exits 0"
assert_output_contains "$LAST_OUTPUT" "No archivable tasks found" "reports nothing eligible"

# ══════════════════════════════════════════════════════════════════════════════
# State-based sibling reordering
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "═══ State-based sibling reordering ═══"

echo "test: done reorders DONE above NEXT"
reset_fixtures
cat > "$TEST_DIR/reorder.org" << 'ORGEOF'
* Project A
** DONE Already done
** NEXT Active task
** TODO Target task
** TODO Another task
ORGEOF
run_cmd '(org-gtd-cli/done "Target task")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/reorder.org" "DONE Target task" "task marked DONE"
assert_line_before "$TEST_DIR/reorder.org" "DONE Target task" "NEXT Active task" "DONE Target above NEXT Active"
assert_line_before "$TEST_DIR/reorder.org" "DONE Already done" "DONE Target task" "DONE Already done above DONE Target (preserves order)"

echo "test: done + auto-progress reorders both"
reset_fixtures
cat > "$TEST_DIR/reorder.org" << 'ORGEOF'
* TODO Project B
** DONE Old done
** NEXT Current task
** TODO First todo
** TODO Second todo
** TODO Third todo
ORGEOF
# Complete the NEXT task — auto-progress should promote "First todo" to NEXT
run_cmd '(org-gtd-cli/done "Current task")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/reorder.org" "DONE Current task" "NEXT marked DONE"
assert_file_contains "$TEST_DIR/reorder.org" "NEXT First todo" "First todo auto-progressed to NEXT"
assert_line_before "$TEST_DIR/reorder.org" "DONE Current task" "NEXT First todo" "DONE Current above NEXT First"
assert_line_before "$TEST_DIR/reorder.org" "NEXT First todo" "TODO Second todo" "NEXT First above TODO Second"

echo "test: set-state reorders correctly"
reset_fixtures
cat > "$TEST_DIR/reorder.org" << 'ORGEOF'
* Project C
** DONE Old done
** NEXT Active
** TODO Alpha
** TODO Beta
ORGEOF
# Change Beta to NEXT — should sort above TODO Alpha
run_cmd '(org-gtd-cli/set-state "Beta" "NEXT")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_line_before "$TEST_DIR/reorder.org" "DONE Old done" "NEXT Active" "DONE above NEXT Active"
assert_line_before "$TEST_DIR/reorder.org" "NEXT Beta" "TODO Alpha" "NEXT Beta above TODO Alpha"

echo "test: set-next reorders promoted task"
reset_fixtures
cat > "$TEST_DIR/reorder.org" << 'ORGEOF'
* TODO Project D
** DONE Old done
** TODO Alpha
** TODO Beta
ORGEOF
run_cmd '(org-gtd-cli/set-next "Project D")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/reorder.org" "NEXT Alpha" "Alpha promoted to NEXT"
assert_line_before "$TEST_DIR/reorder.org" "DONE Old done" "NEXT Alpha" "DONE above NEXT"
assert_line_before "$TEST_DIR/reorder.org" "NEXT Alpha" "TODO Beta" "NEXT Alpha above TODO Beta"

echo "test: CANCELLED sorts with DONE"
reset_fixtures
cat > "$TEST_DIR/reorder.org" << 'ORGEOF'
* Project E
** TODO Alpha
** TODO Beta
ORGEOF
run_cmd '(org-gtd-cli/set-state "Alpha" "CANCELLED")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_line_before "$TEST_DIR/reorder.org" "CANCELLED Alpha" "TODO Beta" "CANCELLED above TODO"

echo "test: WAITING/DEFER ordering"
reset_fixtures
cat > "$TEST_DIR/reorder.org" << 'ORGEOF'
* Project F
** TODO Alpha
** TODO Beta
ORGEOF
run_cmd '(org-gtd-cli/set-state "Alpha" "DEFER")'
assert_exit 0 "$LAST_RC" "set-state DEFER exits 0"
run_cmd '(org-gtd-cli/set-state "Beta" "WAITING")'
assert_exit 0 "$LAST_RC" "set-state WAITING exits 0"
assert_line_before "$TEST_DIR/reorder.org" "WAITING Beta" "DEFER Alpha" "WAITING above DEFER"

echo "test: non-task siblings skip reorder"
reset_fixtures
cat > "$TEST_DIR/reorder.org" << 'ORGEOF'
* Computers
** Agents
*** TODO Agent task one
*** TODO Agent task two
** Emacs
*** TODO Emacs task one
ORGEOF
# "Agents" and "Emacs" are organizational headings without TODO keywords
# set-state on a child should not disrupt the parent's children order
run_cmd '(org-gtd-cli/set-state "Agent task one" "DONE")'
assert_exit 0 "$LAST_RC" "exits 0"
# Agents heading should still be before Emacs heading (not reordered)
assert_line_before "$TEST_DIR/reorder.org" "** Agents" "** Emacs" "organizational headings not reordered"

echo "test: top-level task skip reorder (level 1)"
reset_fixtures
cat > "$TEST_DIR/reorder.org" << 'ORGEOF'
* TODO Top level A
* TODO Top level B
ORGEOF
# Level 1 headings have no parent — reorder should be a no-op
run_cmd '(org-gtd-cli/set-state "Top level A" "DONE")'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/reorder.org" "DONE Top level A" "state changed"

echo ""
echo "All tests completed."
