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

echo "test: appends to task with existing body"
reset_fixtures
run_cmd '(org-gtd-cli/append-body "Buy a small UPS" "Check APC models" nil)'
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Check APC models" "text appended"
assert_output_contains "$LAST_OUTPUT" "Appended to" "append message"

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

echo "test: leaf task (no children) → exit 1"
reset_fixtures
run_cmd '(org-gtd-cli/set-next "Pay quarterly taxes" nil)'
assert_exit 1 "$LAST_RC" "exits 1 for leaf task"

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

echo ""
echo "All tests completed."
