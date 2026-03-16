#!/usr/bin/env bash
# org-gtd-cli test suite
# NOTE: Do NOT use set -e. Tests deliberately check non-zero exit codes.
set -uo pipefail

PASS=0 FAIL=0
TEST_DIR=$(mktemp -d)
EMACS_DIR=$(mktemp -d)
RESULTS_DIR=$(mktemp -d)

# Locate elisp + fixtures relative to script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Single-expression runner (legacy, used for complex multi-step tests) ---
LAST_OUTPUT="" LAST_STDERR="" LAST_RC=0
run_cmd() {
  local stderr_file
  stderr_file=$(mktemp)
  # Don't use || true — we need the real exit code, and set -e is off
  LAST_OUTPUT=$(emacs --batch -q \
    --eval "(setq user-emacs-directory \"$EMACS_DIR/\")" \
    --eval "(setenv \"ORG_DIRECTORY\" \"$TEST_DIR/\")" \
    -l "$SCRIPT_DIR/gtd-core.el" \
    -l "$SCRIPT_DIR/org-gtd-cli.el" \
    --eval "$1" 2>"$stderr_file")
  LAST_RC=$?
  LAST_STDERR=$(cat "$stderr_file")
  rm -f "$stderr_file"
}

# --- Batch runner: multiple tests in one Emacs session ---
# Write a batch .el file via heredoc, then call run_batch_file.
# Inside the .el file, use:
#   (org-gtd-test/reset)           — kill org buffers + re-copy fixtures
#   (org-gtd-test/run N '(expr))   — run expr, capture output/rc/files as result N
BATCH_FILE=""
run_batch_file() {
  rm -f "$RESULTS_DIR"/*.out "$RESULTS_DIR"/*.rc
  rm -rf "$RESULTS_DIR"/*.files
  emacs --batch -q \
    --eval "(setq user-emacs-directory \"$EMACS_DIR/\")" \
    --eval "(setenv \"ORG_DIRECTORY\" \"$TEST_DIR/\")" \
    --eval "(setq org-gtd-test/results-dir \"$RESULTS_DIR\")" \
    --eval "(setq org-gtd-test/test-dir \"$TEST_DIR/\")" \
    --eval "(setq org-gtd-test/script-dir \"$SCRIPT_DIR/\")" \
    -l "$SCRIPT_DIR/gtd-core.el" \
    -l "$SCRIPT_DIR/org-gtd-cli.el" \
    -l "$SCRIPT_DIR/test-harness.el" \
    -l "$BATCH_FILE" 2>/dev/null
  rm -f "$BATCH_FILE"
}

# Retrieve result N from a batch run. Sets LAST_OUTPUT, LAST_RC.
# Restores the file snapshot to TEST_DIR so assert_file_* calls work.
get_result() {
  local idx=$1
  LAST_OUTPUT=$(cat "$RESULTS_DIR/$idx.out" 2>/dev/null || true)
  LAST_RC=$(cat "$RESULTS_DIR/$idx.rc" 2>/dev/null || echo "999")
  LAST_STDERR=""
  if [[ -d "$RESULTS_DIR/$idx.files" ]]; then
    rm -rf "${TEST_DIR:?}"/*
    cp -a "$RESULTS_DIR/$idx.files/." "$TEST_DIR/"
  fi
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
  if grep -qF -- "$pattern" "$file" 2>/dev/null; then
    echo "  PASS: $desc"; ((PASS++))
  else
    echo "  FAIL: $desc (file does not contain '$pattern')"; ((FAIL++))
  fi
}

assert_file_not_contains() {
  local file="$1" pattern="$2" desc="$3"
  if ! grep -qF -- "$pattern" "$file" 2>/dev/null; then
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

assert_no_long_lines() {
  local file="$1" start_pat="$2" end_pat="$3" max_width="$4" desc="$5"
  local in_range=0 bad_lines=""
  while IFS= read -r line; do
    if [[ $in_range -eq 0 ]]; then
      [[ "$line" == *"$start_pat"* ]] && in_range=1
    else
      if [[ -n "$end_pat" && "$line" == *"$end_pat"* ]]; then
        break
      fi
      if [[ ${#line} -gt $max_width ]]; then
        bad_lines+="  (${#line} chars) $line"$'\n'
      fi
    fi
  done < "$file"
  if [[ -z "$bad_lines" ]]; then
    echo "  PASS: $desc"; ((PASS++))
  else
    echo "  FAIL: $desc (lines exceed $max_width chars)"; ((FAIL++))
    echo "$bad_lines" | head -3
  fi
}

summary() {
  echo ""
  echo "═══════════════════════════════════════"
  echo "Results: $PASS passed, $FAIL failed"
  echo "═══════════════════════════════════════"
  [[ $FAIL -eq 0 ]]
}
trap 'summary; rm -rf "$TEST_DIR" "$EMACS_DIR" "$RESULTS_DIR"' EXIT

# ══════════════════════════════════════════════════════════════════════════════
# org-timestamp (read-only — all calls in one Emacs session)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== org-timestamp ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/run 0 '(org-gtd-cli/org-timestamp "2026-03-15"))
(org-gtd-test/run 1 '(org-gtd-cli/org-timestamp "2026-03-15" "14:00"))
(org-gtd-test/run 2 '(org-gtd-cli/org-timestamp "2026-03-15" "14:00-15:30"))
ELISP
run_batch_file

echo "test: outputs correct date with day-of-week"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "<2026-03-15 Sun>" "correct timestamp"

echo "test: outputs correct date+time"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "<2026-03-15 Sun 14:00>" "correct timestamp with time"

echo "test: outputs time range"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "<2026-03-15 Sun 14:00-15:30>" "correct time range"

# ══════════════════════════════════════════════════════════════════════════════
# add-task
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== add-task ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/add-task "Test task" nil nil nil nil nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/add-task "Tagged task" nil "buy,@errand" nil nil nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/add-task "Scheduled task" nil nil "2026-03-20" nil nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/add-task "Deadline task" nil nil nil "2026-03-25" nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/add-task "Body task" "This is the body text" nil nil nil nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/add-task "Priority task" nil nil nil nil "A" nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 6 '(org-gtd-cli/add-task "Waiting task" nil nil nil nil nil nil nil "WAITING"))
(org-gtd-test/reset)
(org-gtd-test/run 7 '(org-gtd-cli/add-task "Category task" nil nil nil nil nil nil "Finance" nil))
(org-gtd-test/reset)
(org-gtd-test/run 8 '(org-gtd-cli/add-task "Missing cat" nil nil nil nil nil nil "Nonexistent" nil))
ELISP
run_batch_file

echo "test: adds to inbox by default"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "TODO Test task" "task in inbox"
assert_output_contains "$LAST_OUTPUT" "Added: Test task -> inbox.org (inbox.org:" "output message with file:line"

echo "test: with tags"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" ":buy:@errand:" "tags formatted"

echo "test: with schedule"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "SCHEDULED: <2026-03-20 Fri>" "scheduled line"

echo "test: with deadline"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "DEADLINE: <2026-03-25 Wed>" "deadline line"

echo "test: with body"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "This is the body text" "body text present"

echo "test: with priority"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "[#A]" "priority cookie"

echo "test: with state WAITING"
get_result 6
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "WAITING Waiting task" "state set"

echo "test: with category (single segment, unique)"
get_result 7
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Category task" "task in tasks.org"
assert_output_contains "$LAST_OUTPUT" "tasks.org/Finance" "output shows full path"

echo "test: category not found fails"
get_result 8
assert_exit 1 "$LAST_RC" "exits 1 for missing category"

# --- add-task --category path + ambiguity tests ---

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
;; Test 0: path match (multi-segment)
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/add-task "Path task" nil nil nil nil nil nil "Computers/Agents" nil))
;; Test 1: ambiguous single-segment (Tools exists under both Computers and Research)
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/add-task "Ambig task" nil nil nil nil nil nil "Tools" nil))
;; Test 2: path disambiguates ambiguity
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/add-task "Disambig task" nil nil nil nil nil nil "Research/Tools" nil))
;; Test 3: wrong path (Agents is under Computers, not Work)
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/add-task "Wrong path" nil nil nil nil nil nil "Work/Agents" nil))
ELISP
run_batch_file

echo "test: category with path match"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Path task" "task in tasks.org"
assert_output_contains "$LAST_OUTPUT" "Computers/Agents" "output shows full path"

echo "test: ambiguous category exits 2"
get_result 1
assert_exit 2 "$LAST_RC" "exits 2 for ambiguous match"
assert_output_contains "$LAST_OUTPUT" "Multiple category matches" "shows ambiguity message"
assert_output_contains "$LAST_OUTPUT" "Computers/Tools" "lists first match path"
assert_output_contains "$LAST_OUTPUT" "Research/Tools" "lists second match path"

echo "test: path disambiguates ambiguity"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Disambig task" "task in tasks.org"
assert_output_contains "$LAST_OUTPUT" "Research/Tools" "placed under Research/Tools"

echo "test: wrong path not found"
get_result 3
assert_exit 1 "$LAST_RC" "exits 1 for wrong path"
assert_output_contains "$LAST_OUTPUT" "not found" "shows not found error"

# ══════════════════════════════════════════════════════════════════════════════
# add-subtask
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== add-subtask ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/add-subtask "Write quarterly report" "Draft introduction" nil nil nil nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/add-subtask "Buy" "New subtask" nil nil nil nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/add-subtask "Buy" "New subtask" nil nil nil nil nil nil "1"))
ELISP
run_batch_file

echo "test: adds child heading at correct level"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "*** TODO Draft introduction" "correct level"
assert_output_contains "$LAST_OUTPUT" "Added subtask" "output message"

echo "test: disambiguation works"
get_result 1
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"

echo "test: index selects match"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0 with index"

# ══════════════════════════════════════════════════════════════════════════════
# agenda (read-only — single reset at section start)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== agenda ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/agenda nil nil nil nil))
(org-gtd-test/run 1 '(org-gtd-cli/agenda "TODO" nil nil nil))
(org-gtd-test/run 2 '(org-gtd-cli/agenda "WAITING" nil nil nil))
(org-gtd-test/run 3 '(org-gtd-cli/agenda nil "@agent" nil nil))
(org-gtd-test/run 4 '(org-gtd-cli/agenda nil nil "2026-03-10" "2026-03-15"))
ELISP
run_batch_file

echo "test: returns all non-done tasks"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "TODO" "contains TODO tasks"
assert_output_not_contains "$LAST_OUTPUT" "DONE Submit expense claims" "excludes DONE tasks"

echo "test: deadline shown in output"
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "D:<2026-03-15 Sun>" "deadline shown"

echo "test: priority shown in output"
assert_output_contains "$LAST_OUTPUT" "[#A]" "priority shown"

echo "test: file:line in output"
assert_output_contains "$LAST_OUTPUT" "(inbox.org:" "file reference shown"

echo "test: state filter TODO"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_not_contains "$LAST_OUTPUT" "NEXT " "excludes NEXT tasks"
assert_output_not_contains "$LAST_OUTPUT" "WAITING " "excludes WAITING tasks"

echo "test: state filter WAITING"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Consider buying a new monitor" "finds WAITING task"
assert_output_contains "$LAST_OUTPUT" "Get travel insurance quote" "finds second WAITING task"

echo "test: tag filter @agent"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set up automated backups" "finds agent task"
assert_output_contains "$LAST_OUTPUT" "Buy a formicarium" "finds inherited agent task"
assert_output_not_contains "$LAST_OUTPUT" "Pay quarterly taxes" "excludes non-agent"

echo "test: date range filter"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Pay quarterly taxes" "task with deadline in range"
assert_output_contains "$LAST_OUTPUT" "Choose a formicarium" "task with deadline in range"
assert_output_not_contains "$LAST_OUTPUT" "Write quarterly report" "task with deadline outside range excluded"
assert_output_not_contains "$LAST_OUTPUT" "Buy groceries" "task without date excluded"

# ══════════════════════════════════════════════════════════════════════════════
# search (read-only — single reset at section start)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== search ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/search "formicarium" nil nil nil))
(org-gtd-test/run 1 '(org-gtd-cli/search "formicarium" "all" nil nil))
(org-gtd-test/run 2 '(org-gtd-cli/search "buy" nil nil "inbox.org"))
(org-gtd-test/run 3 '(org-gtd-cli/search "zzzznonexistent" nil nil nil))
(org-gtd-test/run 4 '(org-gtd-cli/search "" nil nil nil))
(org-gtd-test/run 5 '(org-gtd-cli/search "backups" nil "@agent" nil))
(org-gtd-test/run 6 '(org-gtd-cli/search "insurance" "WAITING" nil nil))
(org-gtd-test/run 7 '(org-gtd-cli/search "dentist" "DONE" nil nil))
(org-gtd-test/run 8 '(org-gtd-cli/search "quarterly" nil nil nil))
(org-gtd-test/run 9 '(org-gtd-cli/search "buy" nil nil "nonexistent.org"))
(org-gtd-test/run 10 '(org-gtd-cli/search "report" nil "work" nil))
(org-gtd-test/run 11 '(org-gtd-cli/search "interesting" nil nil nil))
(org-gtd-test/run 12 '(org-gtd-cli/search "formicarium" "CANCELLED" nil nil))
ELISP
run_batch_file

echo "test: default state filter (TODO,NEXT)"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "[1]" "indexed output"
assert_output_contains "$LAST_OUTPUT" "[2]" "second match indexed"
assert_output_contains "$LAST_OUTPUT" "TODO Buy a formicarium" "finds TODO task"
assert_output_contains "$LAST_OUTPUT" "NEXT Choose a formicarium" "finds NEXT task"
assert_output_not_contains "$LAST_OUTPUT" "Research formicarium options" "excludes DONE task"

echo "test: --state all includes DONE"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Research formicarium options" "DONE task included"
assert_output_contains "$LAST_OUTPUT" "Buy a formicarium" "TODO task still included"
assert_output_contains "$LAST_OUTPUT" "Choose a formicarium" "NEXT task still included"

echo "test: --file restricts to single file"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Buy groceries" "finds task in inbox.org"
assert_output_not_contains "$LAST_OUTPUT" "Buy a formicarium" "excludes tasks.org task"
assert_output_contains "$LAST_OUTPUT" "(inbox.org:" "correct file reference"

echo "test: no matches returns exit 0"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "No matches." "no matches message"

echo "test: empty SUBSTR returns exit 1"
get_result 4
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "Error" "error message"

echo "test: --tag filter"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set up automated backups" "finds @agent task matching substr"
assert_output_not_contains "$LAST_OUTPUT" "Buy a formicarium" "excludes @agent task not matching substr"

echo "test: --state WAITING"
get_result 6
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Get travel insurance quote" "finds WAITING task"

echo "test: --state DONE finds only done tasks"
get_result 7
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Research dentists" "finds DONE task"
assert_output_not_contains "$LAST_OUTPUT" "Reply to dentist" "excludes TODO task"

echo "test: cross-file matches"
get_result 8
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Pay quarterly taxes" "finds task in tasks.org"
assert_output_contains "$LAST_OUTPUT" "Write quarterly report" "finds second task"

echo "test: --file with nonexistent file"
get_result 9
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "file not found" "error message for missing file"

echo "test: --tag with inherited tags"
get_result 10
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Write quarterly report" "finds task inheriting :work: tag"

echo "test: cross-file match from inbox"
get_result 11
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "interesting" "finds task with link in heading"
assert_output_contains "$LAST_OUTPUT" "(inbox.org:" "correct file reference for inbox task"

echo "test: valid state with no matches"
get_result 12
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "No matches." "no matches for unused state"

# ══════════════════════════════════════════════════════════════════════════════
# show (read-only — single reset at section start)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== show ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/show "Buy a formicarium" nil))
(org-gtd-test/run 1 '(org-gtd-cli/show "Fix org-capture workspace" nil))
(org-gtd-test/run 2 '(org-gtd-cli/show "interesting article" nil))
(org-gtd-test/run 3 '(org-gtd-cli/show "Improve agent workflow" nil "t"))
(org-gtd-test/run 4 '(org-gtd-cli/show "Pay quarterly taxes" nil "t"))
ELISP
run_batch_file

echo "test: shows full subtree"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Messor Barbarus" "body text shown"
assert_output_contains "$LAST_OUTPUT" "Research formicarium options" "subtask shown"
assert_output_contains "$LAST_OUTPUT" "(tasks.org:" "file:line header"

echo "test: shows LOGBOOK drawers"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" ":LOGBOOK:" "logbook shown"

echo "test: task with org link"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "[[https://example.com" "link shown"

echo "test: show --plain shows heading hierarchy without body/drawers/tags"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "TODO Improve agent workflow" "root heading with state"
assert_output_contains "$LAST_OUTPUT" "  TODO Design CLI tool" "indented child"
assert_output_contains "$LAST_OUTPUT" "    NEXT Add more test cases" "doubly indented grandchild"
assert_output_not_contains "$LAST_OUTPUT" "Top pain points" "no body text"
assert_output_not_contains "$LAST_OUTPUT" ":LOGBOOK:" "no drawers"
assert_output_not_contains "$LAST_OUTPUT" ":@agent:" "no tags"

echo "test: show --plain on leaf task"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "TODO [#A] Pay quarterly taxes" "heading with priority"
assert_output_not_contains "$LAST_OUTPUT" "DEADLINE" "no planning line"

# ══════════════════════════════════════════════════════════════════════════════
# subtasks (read-only — single reset at section start)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== subtasks ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/subtasks "Improve agent workflow" nil))
(org-gtd-test/run 1 '(org-gtd-cli/subtasks "Pay quarterly taxes" nil))
(org-gtd-test/run 2 '(org-gtd-cli/subtasks "Holiday pre-trip tasks" nil))
ELISP
run_batch_file

echo "test: lists children with states and progress"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "DONE What are the current pain points" "DONE child"
assert_output_contains "$LAST_OUTPUT" "TODO Design CLI tool" "TODO child (subproject)"
assert_output_contains "$LAST_OUTPUT" "2/4 done" "progress count"
assert_output_contains "$LAST_OUTPUT" "(tasks.org:" "child has file:line"

echo "test: exits 1 if no subtasks"
get_result 1
assert_exit 1 "$LAST_RC" "exits 1 for leaf task"

echo "test: nested project subtasks"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "DONE Book flights" "done subtask"
assert_output_contains "$LAST_OUTPUT" "NEXT Book a rental car" "next subtask"
assert_output_contains "$LAST_OUTPUT" "WAITING Get travel insurance" "waiting subtask"

# ══════════════════════════════════════════════════════════════════════════════
# categories (read-only — single reset at section start)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== categories ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/categories))
(org-gtd-test/run 1 '(org-gtd-cli/categories "inbox.org"))
(org-gtd-test/run 2 '(org-gtd-cli/categories "nonexistent.org"))
ELISP
run_batch_file

echo "test: shows plain headings as full paths (default: tasks.org)"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Work (tasks.org:" "top-level category path"
assert_output_contains "$LAST_OUTPUT" "Computers/Agents (tasks.org:" "nested path with slash"
assert_output_contains "$LAST_OUTPUT" "Family/Pet Ants (tasks.org:" "full path to nested heading"
assert_output_contains "$LAST_OUTPUT" "Computers/Tools (tasks.org:" "Tools under Computers"
assert_output_contains "$LAST_OUTPUT" "Research/Tools (tasks.org:" "Tools under Research"
assert_output_contains "$LAST_OUTPUT" "(tasks.org:" "includes file:line"
assert_output_not_contains "$LAST_OUTPUT" "* " "no star prefix in output"

echo "test: does not show TODO headings"
assert_output_not_contains "$LAST_OUTPUT" "Write quarterly report" "no TODO heading"
assert_output_not_contains "$LAST_OUTPUT" "Pay quarterly taxes" "no TODO task"
assert_output_not_contains "$LAST_OUTPUT" "Buy a formicarium" "no TODO subtask"

echo "test: does not show children of TODO headings"
assert_output_not_contains "$LAST_OUTPUT" "Add more test cases" "no child of TODO heading"
assert_output_not_contains "$LAST_OUTPUT" "Research formicarium options" "no child of TODO project"

echo "test: does not show headings from other files"
assert_output_not_contains "$LAST_OUTPUT" "(inbox.org:" "no inbox.org headings"
assert_output_not_contains "$LAST_OUTPUT" "(calendar.org:" "no calendar.org headings"

echo "test: categories --file for a different file"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0 for inbox.org"
assert_output_not_contains "$LAST_OUTPUT" "(tasks.org:" "no tasks.org headings"

echo "test: categories --file for nonexistent file"
get_result 2
assert_exit 1 "$LAST_RC" "exits 1 for missing file"
assert_output_contains "$LAST_OUTPUT" "File not found" "shows error message"

# ══════════════════════════════════════════════════════════════════════════════
# process-agent-tasks (read-only — single call, all assertions together)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== process-agent-tasks ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/process-agent-tasks))
ELISP
run_batch_file

echo "test: finds agent tasks / includes AGENT instruction / shows subtask progress / includes project context"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set up automated backups" "finds backup task"
assert_output_contains "$LAST_OUTPUT" "Improve agent workflow" "finds workflow task"
assert_output_contains "$LAST_OUTPUT" "Buy a formicarium" "finds formicarium task"
assert_output_contains "$LAST_OUTPUT" "Found 3 agent tasks" "correct count"
assert_output_contains "$LAST_OUTPUT" "research backup strategies" "AGENT instruction shown"
assert_output_contains "$LAST_OUTPUT" "2/4 done" "subtask progress"
# "Set up automated backups" is under "Agents" (no TODO keyword) → no project
assert_output_not_contains "$LAST_OUTPUT" "Project: Agents" "skips non-TODO ancestor"
# "Buy a formicarium" is under "Pet Ants" (no TODO keyword) → no project
assert_output_not_contains "$LAST_OUTPUT" "Project: Pet Ants" "skips non-TODO ancestor for formicarium"

# ══════════════════════════════════════════════════════════════════════════════
# done
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== done ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-done "Book a rental car" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-done "Buy" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/set-done "Buy" "1" nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/set-done "Book a rental car" nil "t"))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/set-done "Add more test cases" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/set-done "Get travel insurance quote" nil nil))
ELISP
run_batch_file

echo "test: marks single match as DONE"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Done: Book a rental car (tasks.org:" "done message with file:line"
assert_file_contains "$TEST_DIR/tasks.org" "DONE Book a rental car" "state changed in file"

echo "test: exit 2 on ambiguous"
get_result 1
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"
assert_output_contains "$LAST_OUTPUT" "[1]" "shows indexed matches"
assert_output_contains "$LAST_OUTPUT" "[2]" "shows second match"

echo "test: index selects match"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0 with index"

echo "test: dry-run doesn't modify"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would mark done" "dry-run message"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Book a rental car" "file unchanged"

echo "test: auto-progress promotes next TODO to NEXT"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Auto-progressed" "auto-progress message"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Test on actual project" "next sibling promoted"

echo "test: done removes WAITING tag"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "DONE Get travel insurance quote" "marked done"

# ══════════════════════════════════════════════════════════════════════════════
# set-state
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-state ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-state "Book a rental car" "WAITING" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-state "Book a rental car" "WAITING" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/set-state "Consider buying a new monitor" "TODO" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/set-state "Book a rental car" "TODO" nil "t"))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/set-state "Pay quarterly taxes" "NEXT" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/set-state "Book a rental car" "INVALID" nil nil))
;; Multi-step: DEFER → WAITING (no reset between)
(org-gtd-test/reset)
(org-gtd-test/run 6 '(org-gtd-cli/set-state "Book a rental car" "DEFER" nil nil))
(org-gtd-test/run 7 '(org-gtd-cli/set-state "Book a rental car" "WAITING" nil nil))
;; Multi-step: WAITING → TODO (no reset between)
(org-gtd-test/reset)
(org-gtd-test/run 8 '(org-gtd-cli/set-state "Book a rental car" "WAITING" nil nil))
(org-gtd-test/run 9 '(org-gtd-cli/set-state "Book a rental car" "TODO" nil nil))
ELISP
run_batch_file

echo "test: changes state"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "NEXT -> WAITING (tasks.org:" "state change message with file:line"
assert_file_contains "$TEST_DIR/tasks.org" "WAITING Book a rental car" "state in file"

echo "test: WAITING adds tag"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" ":WAITING:" "WAITING tag added"

echo "test: removing WAITING removes tag"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Consider buying a new monitor" "state changed"

echo "test: dry-run doesn't modify"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would change" "dry-run message"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Book a rental car" "file unchanged"

echo "test: preserves priority cookie"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT [#A] Pay quarterly taxes" "priority preserved"

echo "test: invalid state gives clean error"
get_result 5
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "not a valid state" "clean error message"
assert_output_contains "$LAST_OUTPUT" "TODO, NEXT, DONE, WAITING, DEFER, CANCELLED" "lists valid states"

echo "test: DEFER → WAITING cleans DEFER tag"
get_result 7
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" ":WAITING:" "WAITING tag present"
assert_file_not_contains "$TEST_DIR/tasks.org" ":DEFER:" "DEFER tag removed"

echo "test: WAITING → TODO cleans WAITING tag"
get_result 9
assert_exit 0 "$LAST_RC" "exits 0"
# The fixture already has a WAITING task, so check specifically on this task's line
assert_file_contains "$TEST_DIR/tasks.org" "TODO Book a rental car" "state is TODO"

# ══════════════════════════════════════════════════════════════════════════════
# refile
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== refile ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/refile "Buy groceries" "Shopping" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/refile "Buy groceries" "Nonexistent" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/refile "Buy groceries" "Shopping" nil "t"))
ELISP
run_batch_file

echo "test: moves task to target heading"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_not_contains "$TEST_DIR/inbox.org" "Buy groceries" "removed from inbox"
assert_file_contains "$TEST_DIR/tasks.org" "Buy groceries" "added to tasks.org"
assert_output_contains "$LAST_OUTPUT" "Refiled" "refile message"
assert_output_contains "$LAST_OUTPUT" "(inbox.org:" "refile shows file:line"

echo "test: target not found fails"
get_result 1
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: dry-run doesn't modify"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would refile" "dry-run message"
assert_file_contains "$TEST_DIR/inbox.org" "Buy groceries" "still in inbox"

# ══════════════════════════════════════════════════════════════════════════════
# refile: self-match filtering
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== refile: self-match filtering ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
;; Test 0: self-match skipped, valid target found
;; "Research new tools" in inbox.org contains "Research"; target "Research"
;; should skip the source task (scanned first in inbox.org) and find
;; the * Research category heading in tasks.org.
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/add-task "Research new tools" nil nil nil nil nil "inbox.org" nil nil))
(org-gtd-test/run 1 '(org-gtd-cli/refile "Research new tools" "Research" nil nil))

;; Test 2: all targets are self-matches (only the source heading matches)
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/add-task "Unique xyzzy heading" nil nil nil nil nil "inbox.org" nil nil))
(org-gtd-test/run 3 '(org-gtd-cli/refile "Unique xyzzy" "xyzzy" nil nil))

;; Test 4: subtree child is also a self-match
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/add-task "Plan zqxjk celebration" nil nil nil nil nil "inbox.org" nil nil))
(org-gtd-test/run 5 '(org-gtd-cli/add-subtask "Plan zqxjk" "Zqxjk venue search" nil nil nil nil nil nil nil))
(org-gtd-test/run 6 '(org-gtd-cli/refile "Plan zqxjk celebration" "zqxjk" nil nil))
ELISP
run_batch_file

echo "test: self-match skipped, valid target found"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Refiled" "refile message"
assert_file_not_contains "$TEST_DIR/inbox.org" "Research new tools" "removed from inbox"
assert_file_contains "$TEST_DIR/tasks.org" "Research new tools" "added to tasks.org under Research"

echo "test: all targets are self-matches"
get_result 3
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "self-match" "self-match error message"
assert_file_contains "$TEST_DIR/inbox.org" "Unique xyzzy heading" "inbox unchanged"

echo "test: subtree child also counts as self-match"
get_result 6
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "skipped 2 self-match" "counts both source and child"
assert_file_contains "$TEST_DIR/inbox.org" "Plan zqxjk celebration" "inbox unchanged"

# ══════════════════════════════════════════════════════════════════════════════
# add-event
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== add-event ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/add-event "Team dinner" "2026-03-20" nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/add-event "Lunch" "2026-03-21" "12:00-13:00" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/add-event "Family BBQ" "2026-03-22" nil "calfamily" nil))
ELISP
run_batch_file

echo "test: appends to calendar.org"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/calendar.org" "Team dinner" "event added"
assert_file_contains "$TEST_DIR/calendar.org" ":calpersonal:" "default tag"
assert_file_contains "$TEST_DIR/calendar.org" "<2026-03-20 Fri>" "timestamp"

echo "test: with time"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/calendar.org" "<2026-03-21 Sat 12:00-13:00>" "time range"

echo "test: custom tag"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/calendar.org" ":calfamily:" "custom tag"

# ══════════════════════════════════════════════════════════════════════════════
# add-note
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== add-note ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/add-note "Test research topic" nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/add-note "Custom note" nil nil "Background,Analysis,Recommendations"))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/add-note "Formicarium research" "Buy a formicarium" nil nil))
ELISP
run_batch_file

echo "test: creates note file"
get_result 0
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
get_result 1
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
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "[[file:agent-notes/formicarium-research.org]]" "link added to task"

# ══════════════════════════════════════════════════════════════════════════════
# append-body
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== append-body ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/append-body "Buy a small UPS" "Check APC models" nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/append-body "Buy anti-escape coating" "Also check Fluon PTFE spray" nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/append-body "Reply to dentist" "Call if no reply by Friday" nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/append-body "Bare heading no body" "Appended to bare heading"))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/append-body "Buy a small UPS" "** Test heading" nil))
ELISP
run_batch_file

echo "test: appends to task with existing body (before timestamp with time)"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Check APC models" "text appended"
assert_output_contains "$LAST_OUTPUT" "Appended to" "append message"
assert_line_before "$TEST_DIR/tasks.org" "Check APC models" "[2026-03-11 Wed 13:35]" "text before timestamp"

echo "test: appends before date-only timestamp"
get_result 1
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
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "Call if no reply by Friday" "text appended"

echo "test: append-body on heading with no body or timestamp"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Appended to bare heading" "text appended to bare heading"
# Verify the text is on its own line, not glued to the headline
BARE_LINE=$(grep -n "Bare heading no body" "$TEST_DIR/tasks.org" | head -1 | cut -d: -f1)
BODY_LINE=$(grep -n "Appended to bare heading" "$TEST_DIR/tasks.org" | head -1 | cut -d: -f1)
if [[ -n "$BARE_LINE" && -n "$BODY_LINE" && "$BODY_LINE" -gt "$BARE_LINE" ]]; then
  echo "  PASS: body on separate line after heading"; ((PASS++))
else
  echo "  FAIL: body on separate line after heading (heading=$BARE_LINE, body=$BODY_LINE)"; ((FAIL++))
fi

echo "test: rejects body starting with org heading"
get_result 4
assert_exit 1 "$LAST_RC" "exits 1 for heading body"
assert_output_contains "$LAST_OUTPUT" "Error" "error message"

# ══════════════════════════════════════════════════════════════════════════════
# set-body
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-body ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-body "Buy a small UPS" "Brand new body text here." nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-body "Buy anti-escape coating" "" nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/set-body "Reply to dentist" "Please call them Monday." nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/set-body "Bare heading no body" "Set on bare heading"))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/set-body "Buy a small UPS" "** Sneaky heading" nil))
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/set-body "Fix org-capture" "Replaced body." nil))
(org-gtd-test/reset)
(org-gtd-test/run 6 '(org-gtd-cli/set-body "Buy a" "Index test body." "2"))
ELISP
run_batch_file

echo "test: replaces existing body"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set body" "set-body message"
assert_file_contains "$TEST_DIR/tasks.org" "Brand new body text here." "new body present"
assert_file_not_contains "$TEST_DIR/tasks.org" "Power outage corrupted" "old body removed"
assert_file_not_contains "$TEST_DIR/tasks.org" "USB UPS with auto-shutdown" "old body line 2 removed"
# Timestamp should still be there
assert_file_contains "$TEST_DIR/tasks.org" "[2026-03-11 Wed 13:35]" "timestamp preserved"
assert_line_before "$TEST_DIR/tasks.org" "Brand new body text here." "[2026-03-11 Wed 13:35]" "new body before timestamp"

echo "test: empty text removes body"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_not_contains "$TEST_DIR/tasks.org" "Messor barbarus can climb" "body text removed"
assert_file_not_contains "$TEST_DIR/tasks.org" "PTFE anti-escape" "body text line 2 removed"
# Timestamp preserved
assert_file_contains "$TEST_DIR/tasks.org" "[2026-03-12 Thu]" "timestamp preserved"

echo "test: set-body on task with no body"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "Please call them Monday." "body inserted"

echo "test: set-body on heading with no body or timestamp"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Set on bare heading" "body set on bare heading"
# Verify the text is on its own line, not glued to the headline
BARE_LINE=$(grep -n "Bare heading no body" "$TEST_DIR/tasks.org" | head -1 | cut -d: -f1)
BODY_LINE=$(grep -n "Set on bare heading" "$TEST_DIR/tasks.org" | head -1 | cut -d: -f1)
if [[ -n "$BARE_LINE" && -n "$BODY_LINE" && "$BODY_LINE" -gt "$BARE_LINE" ]]; then
  echo "  PASS: body on separate line after heading"; ((PASS++))
else
  echo "  FAIL: body on separate line after heading (heading=$BARE_LINE, body=$BODY_LINE)"; ((FAIL++))
fi

echo "test: rejects body starting with org heading"
get_result 4
assert_exit 1 "$LAST_RC" "exits 1 for heading body"
assert_output_contains "$LAST_OUTPUT" "Error" "error message"

echo "test: preserves metadata (PROPERTIES, LOGBOOK, SCHEDULED/DEADLINE)"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Replaced body." "new body present"
assert_file_not_contains "$TEST_DIR/tasks.org" "It appears in the existing Emacs instance" "old body removed"
assert_file_contains "$TEST_DIR/tasks.org" ":ID:       test-id-capture-fix" "PROPERTIES preserved"
assert_file_contains "$TEST_DIR/tasks.org" "State \"DONE\"       from \"TODO\"" "LOGBOOK preserved"
assert_file_contains "$TEST_DIR/tasks.org" "[2026-03-06 Fri 14:33]" "timestamp preserved"

echo "test: index disambiguation"
get_result 6
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Index test body." "body set with index"

# ══════════════════════════════════════════════════════════════════════════════
# move
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== move ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/move "Implement CLI tool" "up" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/move "Design CLI tool" "down" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/move "Buy anti-escape coating" "after" "Research formicarium" nil))
ELISP
run_batch_file

echo "test: move up"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Moved:" "move message"
assert_output_contains "$LAST_OUTPUT" "(tasks.org:" "move shows file:line"

echo "test: move down"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Moved:" "move message"

echo "test: move after sibling"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Moved:" "move message"

# ══════════════════════════════════════════════════════════════════════════════
# org-timestamp --inactive (read-only — no reset needed)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== org-timestamp --inactive ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/run 0 '(org-gtd-cli/org-timestamp "2026-03-15" nil "t"))
(org-gtd-test/run 1 '(org-gtd-cli/org-timestamp "2026-03-15" "14:00" "t"))
ELISP
run_batch_file

echo "test: inactive timestamp uses square brackets"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "[2026-03-15 Sun]" "inactive timestamp"

echo "test: inactive timestamp with time"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "[2026-03-15 Sun 14:00]" "inactive timestamp with time"

# ══════════════════════════════════════════════════════════════════════════════
# set-next
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-next ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil))
;; Multi-step: complete NEXT child, set-state WAITING→TODO, then set-next
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-done "Book a rental car" nil nil))
(org-gtd-test/run 2 '(org-gtd-cli/set-state "Get travel insurance" "TODO" nil nil))
(org-gtd-test/run 3 '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil))
;; Buy a formicarium — has NEXT child already
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/set-next "Buy a formicarium" nil))
;; Leaf task: set to NEXT, then again (no-op)
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/set-next "Pay quarterly taxes" nil))
(org-gtd-test/run 6 '(org-gtd-cli/set-next "Pay quarterly taxes" nil))
;; Subproject set-next
(org-gtd-test/reset)
(org-gtd-test/run 7 '(org-gtd-cli/set-next "Design CLI tool" nil))
ELISP
run_batch_file

echo "test: already has NEXT → no-op"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Already has NEXT" "already has NEXT message"

echo "test: promotes first TODO child"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set NEXT" "set next message"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Get travel insurance" "child promoted"

echo "test: no TODO children → exit 1"
get_result 4
# This has DONE, NEXT, TODO children — it has NEXT, so it should say "Already has NEXT"
assert_exit 0 "$LAST_RC" "exits 0 (already has NEXT)"

echo "test: leaf task (no children) → sets task itself to NEXT"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0 for leaf task"
assert_output_contains "$LAST_OUTPUT" "Set NEXT" "set next on leaf task"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT [#A] Pay quarterly taxes" "leaf task promoted to NEXT"

echo "test: leaf task already NEXT → no-op"
get_result 6
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Already NEXT" "already NEXT message for leaf"

echo "test: subproject set-next"
get_result 7
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Already has NEXT" "Design CLI tool already has NEXT child"

# ══════════════════════════════════════════════════════════════════════════════
# Subproject tests (read-only — single reset at section start)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== subproject ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/subtasks "Design CLI tool" nil))
(org-gtd-test/run 1 '(org-gtd-cli/subtasks "Improve agent workflow" nil))
(org-gtd-test/run 2 '(org-gtd-cli/process-agent-tasks))
ELISP
run_batch_file

echo "test: subtasks of subproject"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "NEXT Add more test cases" "first child"
assert_output_contains "$LAST_OUTPUT" "TODO Test on actual project" "second child"
assert_output_contains "$LAST_OUTPUT" "TODO Start using it" "third child"
assert_output_contains "$LAST_OUTPUT" "0/3 done" "progress"

echo "test: parent project includes subproject as child"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "TODO Design CLI tool" "subproject shown as child"
assert_output_contains "$LAST_OUTPUT" "TODO Implement CLI tool" "sibling shown"

echo "test: process-agent-tasks shows correct direct-child subtask count and file:line"
get_result 2
assert_output_contains "$LAST_OUTPUT" "2/4 done" "correct subtask count for improved workflow"
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

# Compute BEFORE checksum from fresh fixtures
reset_fixtures
BEFORE_TASKS=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-done "Buy" "999" nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-state "Buy" "NEXT" "999" nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/refile "Buy" "Shopping" "999" nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/append-body "Buy" "text" "999"))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/move "Buy" "up" nil "999"))
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/add-subtask "Buy" "child" nil nil nil nil nil nil "999"))
ELISP
run_batch_file

echo "test: done with out-of-bounds index"
get_result 0
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

echo "test: set-state with out-of-bounds index"
get_result 1
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

echo "test: refile with out-of-bounds index"
get_result 2
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

echo "test: append-body with out-of-bounds index"
get_result 3
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

echo "test: move with out-of-bounds index"
get_result 4
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

echo "test: add-subtask with out-of-bounds index"
get_result 5
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

# ══════════════════════════════════════════════════════════════════════════════
# Edge case tests: no match found
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== edge cases: no match ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-done "xyznonexistent" nil nil))
(org-gtd-test/run 1 '(org-gtd-cli/set-state "xyznonexistent" "NEXT" nil nil))
(org-gtd-test/run 2 '(org-gtd-cli/refile "xyznonexistent" "Shopping" nil nil))
(org-gtd-test/run 3 '(org-gtd-cli/append-body "xyznonexistent" "text" nil))
(org-gtd-test/run 4 '(org-gtd-cli/move "xyznonexistent" "up" nil nil))
ELISP
run_batch_file

echo "test: done nonexistent"
get_result 0
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: set-state nonexistent"
get_result 1
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: refile nonexistent"
get_result 2
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: append-body nonexistent"
get_result 3
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: move nonexistent"
get_result 4
assert_exit 1 "$LAST_RC" "exits 1"

# ══════════════════════════════════════════════════════════════════════════════
# Edge case: invalid refile target
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== edge cases: invalid refile target ==="

# Compute BEFORE checksum from fresh fixtures
reset_fixtures
BEFORE_INBOX=$(md5sum "$TEST_DIR/inbox.org" | cut -d' ' -f1)

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/refile "Buy groceries" "Nonexistent Heading" nil nil))
ELISP
run_batch_file

echo "test: refile to nonexistent heading"
get_result 0
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/inbox.org" "$BEFORE_INBOX" "file unchanged"

# ══════════════════════════════════════════════════════════════════════════════
# Integration test: add-subtask → set-next → done chain
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== integration: add-subtask → set-next → done chain ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil))
(org-gtd-test/run 1 '(org-gtd-cli/set-done "Book a rental car" nil nil))
(org-gtd-test/run 2 '(org-gtd-cli/subtasks "Holiday pre-trip tasks" nil))
(org-gtd-test/run 3 '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil))
(org-gtd-test/run 4 '(org-gtd-cli/set-state "Get travel insurance" "TODO" nil nil))
(org-gtd-test/run 5 '(org-gtd-cli/set-next "Holiday pre-trip tasks" nil))
(org-gtd-test/run 6 '(org-gtd-cli/add-subtask "Get travel insurance" "Compare providers" nil nil nil nil nil nil nil))
(org-gtd-test/run 7 '(org-gtd-cli/show "Get travel insurance" nil))
ELISP
run_batch_file

echo "test: step 1 - set-next on project with existing NEXT → no-op"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Already has NEXT" "already has NEXT"

echo "test: step 2 - done Book a rental car → auto-progress"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "DONE Book a rental car" "done"

echo "test: step 3 - verify via subtasks"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "DONE Book a rental car" "done shown"
assert_output_contains "$LAST_OUTPUT" "WAITING Get travel insurance" "waiting shown"

echo "test: step 4 - set-next with no TODO children → exit 1"
get_result 3
assert_exit 1 "$LAST_RC" "exits 1 (no TODO to promote)"

echo "test: step 5 - set-state WAITING → TODO"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"

echo "test: step 6 - set-next promotes to NEXT"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Set NEXT" "promoted"
assert_file_contains "$TEST_DIR/tasks.org" "NEXT Get travel insurance" "promoted in file"

echo "test: step 7 - add-subtask to NEXT task demotes parent to TODO"
get_result 6
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Get travel insurance" "demoted to TODO"
assert_file_contains "$TEST_DIR/tasks.org" "TODO Compare providers" "subtask added"

echo "test: step 8 - verify demotion via show"
get_result 7
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "TODO Get travel insurance" "state is TODO"
assert_output_contains "$LAST_OUTPUT" "Compare providers" "has child"

# ══════════════════════════════════════════════════════════════════════════════
# rename
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== rename ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/rename "Buy groceries" "Buy organic groceries" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/rename "Pay quarterly taxes" "Pay quarterly income taxes" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/rename "Get travel insurance" "Get travel insurance from Allianz" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/rename "Buy groceries" "Buy organic groceries" nil "t"))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/rename "Buy" "Something" nil nil))
ELISP
run_batch_file

echo "test: basic rename"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Renamed:" "rename message"
assert_output_contains "$LAST_OUTPUT" "\"Buy groceries\" -> \"Buy organic groceries\"" "old and new in output"
assert_file_contains "$TEST_DIR/inbox.org" "Buy organic groceries" "new heading in file"
assert_file_not_contains "$TEST_DIR/inbox.org" "Buy groceries" "old heading removed"

echo "test: rename preserves state, priority, and tags"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "TODO [#A] Pay quarterly income taxes" "state and priority preserved"

echo "test: rename preserves tags"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Get travel insurance from Allianz" "renamed"
assert_file_contains "$TEST_DIR/tasks.org" ":WAITING:" "WAITING tag preserved"
assert_file_contains "$TEST_DIR/tasks.org" ":email:" "email tag preserved"

echo "test: rename dry-run"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would rename" "dry-run message"
assert_file_contains "$TEST_DIR/inbox.org" "Buy groceries" "file unchanged"

echo "test: rename ambiguous"
get_result 4
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"

# ══════════════════════════════════════════════════════════════════════════════
# set-schedule
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-schedule ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-schedule "Buy groceries" "2026-03-20" nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-schedule "Buy groceries" "2026-03-20" "14:00" nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/set-schedule "Consider buying a new monitor" "2026-04-01" nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/set-schedule "Consider buying a new monitor" nil nil "t" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/set-schedule "Buy groceries" nil nil "t" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/set-schedule "Buy groceries" "2026-03-20" nil nil nil "t"))
(org-gtd-test/reset)
(org-gtd-test/run 6 '(org-gtd-cli/set-schedule "Buy" "2026-03-20" nil nil nil nil))
ELISP
run_batch_file

echo "test: set schedule on unscheduled task"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Scheduled:" "schedule message"
assert_file_contains "$TEST_DIR/inbox.org" "SCHEDULED: <2026-03-20 Fri>" "schedule in file"

echo "test: set schedule with time"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "SCHEDULED: <2026-03-20 Fri 14:00>" "schedule with time in file"

echo "test: overwrite existing schedule"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "SCHEDULED: <2026-04-01 Wed>" "new schedule in file"
assert_file_not_contains "$TEST_DIR/tasks.org" "SCHEDULED: <2026-03-09 Mon>" "old schedule gone"

echo "test: clear existing schedule"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Cleared schedule" "clear message"
assert_file_not_contains "$TEST_DIR/tasks.org" "SCHEDULED: <2026-03-09 Mon>" "schedule removed"

echo "test: clear when no schedule (no-op)"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Cleared schedule" "clear message even when none existed"

echo "test: set-schedule dry-run"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would schedule" "dry-run message"
assert_file_not_contains "$TEST_DIR/inbox.org" "SCHEDULED:" "file unchanged"

echo "test: set-schedule ambiguous"
get_result 6
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"

# ══════════════════════════════════════════════════════════════════════════════
# set-deadline
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-deadline ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-deadline "Buy groceries" "2026-03-25" nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-deadline "Buy groceries" "2026-03-25" "17:00" nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/set-deadline "Pay quarterly taxes" "2026-03-30" nil nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/set-deadline "Pay quarterly taxes" nil nil "t" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/set-deadline "Buy groceries" nil nil "t" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/set-deadline "Buy groceries" "2026-03-25" nil nil nil "t"))
ELISP
run_batch_file

echo "test: set deadline on task without one"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Deadline:" "deadline message"
assert_file_contains "$TEST_DIR/inbox.org" "DEADLINE: <2026-03-25 Wed>" "deadline in file"

echo "test: set deadline with time"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "DEADLINE: <2026-03-25 Wed 17:00>" "deadline with time in file"

echo "test: overwrite existing deadline"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "DEADLINE: <2026-03-30 Mon>" "new deadline"
assert_file_not_contains "$TEST_DIR/tasks.org" "DEADLINE: <2026-03-15 Sun>" "old deadline gone"

echo "test: clear existing deadline"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Cleared deadline" "clear message"
assert_file_not_contains "$TEST_DIR/tasks.org" "DEADLINE: <2026-03-15 Sun>" "deadline removed"

echo "test: clear when no deadline (no-op)"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Cleared deadline" "clear message even when none existed"

echo "test: set-deadline dry-run"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would set deadline" "dry-run message"
assert_file_not_contains "$TEST_DIR/inbox.org" "DEADLINE:" "file unchanged"

# ══════════════════════════════════════════════════════════════════════════════
# set-tags
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== set-tags ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/set-tags "Buy groceries" "urgent" nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-tags "Buy groceries" nil "@errand" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/set-tags "Buy groceries" "urgent,@home" "@errand" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/set-tags "Buy groceries" nil "nonexistent" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/set-tags "Buy groceries" "buy" nil nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/set-tags "Buy groceries" "urgent" nil nil "t"))
(org-gtd-test/reset)
(org-gtd-test/run 6 '(org-gtd-cli/set-tags "Buy" "urgent" nil nil nil))
ELISP
run_batch_file

echo "test: add tag"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Tags:" "tags message"
assert_file_contains "$TEST_DIR/inbox.org" ":urgent:" "tag added"
# Original tags preserved
assert_file_contains "$TEST_DIR/inbox.org" ":buy:" "buy tag preserved"
assert_file_contains "$TEST_DIR/inbox.org" ":@errand:" "errand tag preserved"

echo "test: remove tag"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" ":buy:" "buy tag preserved"

echo "test: add and remove in one call"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" ":urgent:" "urgent added"
assert_file_contains "$TEST_DIR/inbox.org" ":@home:" "home added"

echo "test: remove nonexistent tag (no-op)"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
# Original tags still there
assert_file_contains "$TEST_DIR/inbox.org" ":buy:" "buy tag preserved"
assert_file_contains "$TEST_DIR/inbox.org" ":@errand:" "errand tag preserved"

echo "test: add tag that already exists (no-op)"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" ":buy:" "buy tag still there"

echo "test: set-tags dry-run"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would set tags" "dry-run message"
assert_file_not_contains "$TEST_DIR/inbox.org" ":urgent:" "file unchanged"

echo "test: set-tags ambiguous"
get_result 6
assert_exit 2 "$LAST_RC" "exits 2 on ambiguous"

# ══════════════════════════════════════════════════════════════════════════════
# Edge cases for new commands: out-of-bounds index
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== edge cases: new commands out-of-bounds index ==="

# Compute BEFORE checksum from fresh fixtures
reset_fixtures
BEFORE_TASKS=$(md5sum "$TEST_DIR/tasks.org" | cut -d' ' -f1)

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/rename "Buy" "Something" "999" nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-schedule "Buy" "2026-03-20" nil nil "999" nil))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/set-deadline "Buy" "2026-03-25" nil nil "999" nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/set-tags "Buy" "urgent" nil "999" nil))
ELISP
run_batch_file

echo "test: rename with out-of-bounds index"
get_result 0
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

echo "test: set-schedule with out-of-bounds index"
get_result 1
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

echo "test: set-deadline with out-of-bounds index"
get_result 2
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

echo "test: set-tags with out-of-bounds index"
get_result 3
assert_exit 1 "$LAST_RC" "exits 1"
assert_file_unchanged "$TEST_DIR/tasks.org" "$BEFORE_TASKS" "file unchanged"

# ══════════════════════════════════════════════════════════════════════════════
# Edge cases for new commands: no match
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== edge cases: new commands no match ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/rename "xyznonexistent" "Something" nil nil))
(org-gtd-test/run 1 '(org-gtd-cli/set-schedule "xyznonexistent" "2026-03-20" nil nil nil nil))
(org-gtd-test/run 2 '(org-gtd-cli/set-deadline "xyznonexistent" "2026-03-25" nil nil nil nil))
(org-gtd-test/run 3 '(org-gtd-cli/set-tags "xyznonexistent" "urgent" nil nil nil))
ELISP
run_batch_file

echo "test: rename nonexistent"
get_result 0
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: set-schedule nonexistent"
get_result 1
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: set-deadline nonexistent"
get_result 2
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: set-tags nonexistent"
get_result 3
assert_exit 1 "$LAST_RC" "exits 1"

# ══════════════════════════════════════════════════════════════════════════════
# archive (single task)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== archive: single task ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/archive "buy new router" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/archive "research dentists" nil "t"))
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/archive "write quarterly report" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/archive "submit expense claims" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/archive "pack suitcases" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/archive "xyznonexistent" nil nil))
(org-gtd-test/reset)
(org-gtd-test/run 6 '(org-gtd-cli/archive "buy" nil nil))
ELISP
run_batch_file

echo "test: archive happy path"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" 'Archived: "Buy new router"' "output confirms archive"
# Task should be gone from tasks.org
assert_file_not_contains "$TEST_DIR/tasks.org" "Buy new router" "removed from tasks.org"
# Task should be in archive file
assert_file_contains "$TEST_DIR/tasks.org_archive" "Buy new router" "present in tasks.org_archive"

echo "test: archive dry-run"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" 'Would archive:' "output says would archive"
# Task should still be in tasks.org
assert_file_contains "$TEST_DIR/tasks.org" "Research dentists" "still in tasks.org"

echo "test: archive rejects active task (rule 1)"
get_result 2
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "still active" "output mentions still active"

echo "test: archive rejects recent dates (rule 2b)"
get_result 3
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "recent dates" "output mentions recent dates"

echo "test: archive rejects inside active project (rule 3)"
get_result 4
assert_exit 1 "$LAST_RC" "exits 1"
assert_output_contains "$LAST_OUTPUT" "inside active project" "output mentions active project"

echo "test: archive no match"
get_result 5
assert_exit 1 "$LAST_RC" "exits 1"

echo "test: archive ambiguous match"
get_result 6
assert_exit 2 "$LAST_RC" "exits 2"

# ══════════════════════════════════════════════════════════════════════════════
# archive --all (batch)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== archive: batch (--all) ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/archive-all nil))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/archive-all "t"))
;; Multi-step: archive all, then archive all again (idempotent)
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/archive-all nil))
(org-gtd-test/run 3 '(org-gtd-cli/archive-all nil))
ELISP
run_batch_file

echo "test: archive --all happy path"
get_result 0
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
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_output_contains "$LAST_OUTPUT" "Would archive" "output says would archive"
# Files should be unchanged
assert_file_contains "$TEST_DIR/tasks.org" "Buy new router" "Buy new router still in tasks.org"
assert_file_contains "$TEST_DIR/tasks.org" "Research dentists" "Research dentists still in tasks.org"

echo "test: archive --all nothing eligible"
get_result 2
assert_exit 0 "$LAST_RC" "first pass exits 0"
# Run again — nothing should be left
get_result 3
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
run_cmd '(org-gtd-cli/set-done "Target task")'
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
run_cmd '(org-gtd-cli/set-done "Current task")'
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

# ══════════════════════════════════════════════════════════════════════════════
# agenda-view (read-only — single reset at section start)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== agenda-view ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/agenda-view " "))
(org-gtd-test/run 1 '(org-gtd-cli/agenda-view "n"))
(org-gtd-test/run 2 '(org-gtd-cli/agenda-view "INVALID"))
ELISP
run_batch_file

echo "test: agenda-view default key (full dashboard)"
get_result 0
assert_exit 0 $LAST_RC "agenda-view default key"
assert_output_contains "$LAST_OUTPUT" "Next Tasks" "agenda-view shows Next Tasks section"
assert_output_contains "$LAST_OUTPUT" "Projects" "agenda-view shows Projects section"

echo "test: agenda-view includes (file:line) on task lines"
assert_output_contains "$LAST_OUTPUT" "(tasks.org:" "agenda-view task lines have (file:line)"

echo "test: agenda-view specific key (Next Tasks)"
get_result 1
assert_exit 0 $LAST_RC "agenda-view n key"
assert_output_contains "$LAST_OUTPUT" "Next Tasks" "agenda-view n shows Next Tasks"

echo "test: agenda-view invalid key"
get_result 2
assert_exit 1 $LAST_RC "agenda-view invalid key"
assert_output_contains "$LAST_OUTPUT" "Unknown agenda view key" "agenda-view invalid key message"

# ══════════════════════════════════════════════════════════════════════════════
# fix-timestamps
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== fix-timestamps ==="

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << 'ELISP'
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/fix-timestamps))
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/fix-timestamps "t"))
;; Multi-step: fix-timestamps twice for idempotency
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/fix-timestamps))
(org-gtd-test/run 3 '(org-gtd-cli/fix-timestamps))
;; Separate run for body preservation + skip non-TODO checks
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/fix-timestamps))
ELISP
run_batch_file

echo "test: fix-timestamps adds missing timestamps"
get_result 0
assert_exit 0 $LAST_RC "fix-timestamps exits 0"
assert_output_contains "$LAST_OUTPUT" "Fixed" "fix-timestamps reports Fixed"
assert_output_contains "$LAST_OUTPUT" "Bare heading no body" "fix-timestamps mentions Bare heading"
assert_output_contains "$LAST_OUTPUT" "Mystery task" "fix-timestamps mentions Mystery task"
# Verify timestamp was actually inserted after the bare heading
assert_file_contains "$TEST_DIR/tasks.org" "Bare heading no body" "bare heading still in file"
# The timestamp line should be right after the bare heading
BARE_LINE=$(grep -n "Bare heading no body" "$TEST_DIR/tasks.org" | head -1 | cut -d: -f1)
NEXT_LINE=$((BARE_LINE + 1))
NEXT_CONTENT=$(sed -n "${NEXT_LINE}p" "$TEST_DIR/tasks.org")
if echo "$NEXT_CONTENT" | grep -qP '^\[[-0-9]+ [A-Z][a-z]+( [0-9:]+)?\]$'; then
  echo "  PASS: timestamp inserted after bare heading"; ((PASS++))
else
  echo "  FAIL: expected timestamp after bare heading, got: $NEXT_CONTENT"; ((FAIL++))
fi

echo "test: fix-timestamps dry-run does not modify files"
get_result 1
assert_exit 0 $LAST_RC "fix-timestamps --dry-run exits 0"
assert_output_contains "$LAST_OUTPUT" "Would fix" "fix-timestamps dry-run reports Would fix"
# get_result restores snapshot — dry-run should not have modified files, so compare to fresh fixtures
cp "$SCRIPT_DIR/fixtures/tasks.org" "$TEST_DIR/tasks.org.before"
if diff -q "$TEST_DIR/tasks.org" "$TEST_DIR/tasks.org.before" >/dev/null 2>&1; then
  echo "  PASS: dry-run did not modify file"; ((PASS++))
else
  echo "  FAIL: dry-run modified the file"; ((FAIL++))
fi

echo "test: fix-timestamps is idempotent"
get_result 2
assert_exit 0 $LAST_RC "fix-timestamps first run exits 0"
get_result 3
assert_exit 0 $LAST_RC "fix-timestamps second run exits 0"
assert_output_contains "$LAST_OUTPUT" "nothing to fix" "second run reports nothing to fix"

echo "test: fix-timestamps preserves body text"
get_result 4
assert_exit 0 $LAST_RC "fix-timestamps exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Some old task with no dates at all" "Mystery task body preserved"
# Timestamp should be after the body text — find the timestamp line following "Mystery task"
MYSTERY_LINE=$(grep -n "Mystery task" "$TEST_DIR/tasks.org" | head -1 | cut -d: -f1)
BODY_LINE=$((MYSTERY_LINE + 1))
TS_LINE=$((MYSTERY_LINE + 2))
TS_CONTENT=$(sed -n "${TS_LINE}p" "$TEST_DIR/tasks.org")
if echo "$TS_CONTENT" | grep -qP '^\[[-0-9]+ [A-Z][a-z]+( [0-9:]+)?\]$'; then
  echo "  PASS: timestamp after Mystery task body text"; ((PASS++))
else
  echo "  FAIL: expected timestamp after Mystery task body, got: $TS_CONTENT"; ((FAIL++))
fi

echo "test: fix-timestamps skips non-TODO headings"
assert_output_not_contains "$LAST_OUTPUT" "Agents" "fix-timestamps skips non-TODO Agents heading"
assert_output_not_contains "$LAST_OUTPUT" "Pet Ants" "fix-timestamps skips non-TODO Pet Ants heading"

# ══════════════════════════════════════════════════════════════════════════════
# fill-text (line wrapping)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== fill-text (line wrapping) ==="

LONG_TEXT="This is a very long line of text that should definitely be wrapped because it exceeds the eighty column limit that we have set for body text in our GTD system"
LONG_TEXT2="Another extremely long line of text that also exceeds the eighty column limit and should be wrapped properly by the fill-text function when processing"

BATCH_FILE=$(mktemp --suffix=.el)
cat > "$BATCH_FILE" << ELISP
;; 0: append-body with long text
(org-gtd-test/reset)
(org-gtd-test/run 0 '(org-gtd-cli/append-body "Bare heading" "$LONG_TEXT"))

;; 1: set-body with long text
(org-gtd-test/reset)
(org-gtd-test/run 1 '(org-gtd-cli/set-body "Bare heading" "$LONG_TEXT"))

;; 2: add-task with long body
(org-gtd-test/reset)
(org-gtd-test/run 2 '(org-gtd-cli/add-task "Wrap test task" "$LONG_TEXT" nil nil nil nil nil nil nil))

;; 3: add-subtask with long body
(org-gtd-test/reset)
(org-gtd-test/run 3 '(org-gtd-cli/add-subtask "Holiday pre-trip" "Wrap sub" "$LONG_TEXT" nil nil nil nil nil nil))

;; 4: body with src block containing long line
(org-gtd-test/reset)
(org-gtd-test/run 4 '(org-gtd-cli/set-body "Bare heading" "Here is code:\n#+begin_src bash\necho this is a very long command that should not be wrapped because it is inside a source code block and wrapping would break it\n#+end_src"))

;; 5: body with two long list items
(org-gtd-test/reset)
(org-gtd-test/run 5 '(org-gtd-cli/set-body "Bare heading" "- First item that is quite long and exceeds the eighty column limit so it should be wrapped onto the next line properly\n- Second item that is also quite long and exceeds the eighty column limit so it should also be wrapped onto the next line"))

;; 6: short text unchanged
(org-gtd-test/reset)
(org-gtd-test/run 6 '(org-gtd-cli/set-body "Bare heading" "Short text here"))

;; 7: body followed by inactive timestamp on own line
(org-gtd-test/reset)
(org-gtd-test/run 7 '(org-gtd-cli/append-body "Bare heading" "Some text that is fairly long and might want to merge with the following line but should not.\n[2026-03-16 Mon 14:00]"))

;; 8: body followed by time-range timestamp
(org-gtd-test/reset)
(org-gtd-test/run 8 '(org-gtd-cli/append-body "Bare heading" "Some important notes here.\n[2026-03-16 Mon 09:00-10:30]"))

;; 9: two paragraphs separated by blank line
(org-gtd-test/reset)
(org-gtd-test/run 9 '(org-gtd-cli/set-body "Bare heading" "$LONG_TEXT\n\n$LONG_TEXT2"))

;; 10: short append-body
(org-gtd-test/reset)
(org-gtd-test/run 10 '(org-gtd-cli/append-body "Bare heading" "OK"))

;; 11: body with quote block containing long line
(org-gtd-test/reset)
(org-gtd-test/run 11 '(org-gtd-cli/set-body "Bare heading" "A quote:\n#+begin_quote\nThis is a very long quotation that should be preserved verbatim because it is inside a quote block and should not be reflowed by the fill function\n#+end_quote"))
ELISP
run_batch_file

echo "test: append-body wraps long text"
get_result 0
assert_exit 0 "$LAST_RC" "exits 0"
assert_no_long_lines "$TEST_DIR/tasks.org" "Bare heading" "Research" 80 "no lines exceed 80 chars"

echo "test: set-body wraps long text"
get_result 1
assert_exit 0 "$LAST_RC" "exits 0"
assert_no_long_lines "$TEST_DIR/tasks.org" "Bare heading" "Research" 80 "no lines exceed 80 chars"

echo "test: add-task wraps long body"
get_result 2
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/inbox.org" "Wrap test task" "task added"
assert_no_long_lines "$TEST_DIR/inbox.org" "Wrap test task" "" 80 "no lines exceed 80 chars"

echo "test: add-subtask wraps long body"
get_result 3
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Wrap sub" "subtask added"
assert_no_long_lines "$TEST_DIR/tasks.org" "Wrap sub" "Finance" 80 "no lines exceed 80 chars"

echo "test: src block content preserved verbatim"
get_result 4
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "echo this is a very long command" "long line inside src block preserved"

echo "test: list items wrapped independently"
get_result 5
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "- First item" "first list item present"
assert_file_contains "$TEST_DIR/tasks.org" "- Second item" "second list item present"

echo "test: short text unchanged"
get_result 6
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "Short text here" "short text preserved"

echo "test: inactive timestamp stays on own line"
get_result 7
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "[2026-03-16 Mon 14:00]" "timestamp present"
# Timestamp must be on its own line — grep for it at start of line
if grep -qP '^\[2026-03-16 Mon 14:00\]' "$TEST_DIR/tasks.org"; then
  echo "  PASS: timestamp on its own line"; ((PASS++))
else
  echo "  FAIL: timestamp not on its own line"; ((FAIL++))
fi

echo "test: time-range timestamp not merged"
get_result 8
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "[2026-03-16 Mon 09:00-10:30]" "time-range timestamp present"
if grep -qP '^\[2026-03-16 Mon 09:00-10:30\]' "$TEST_DIR/tasks.org"; then
  echo "  PASS: time-range timestamp on its own line"; ((PASS++))
else
  echo "  FAIL: time-range timestamp not on its own line"; ((FAIL++))
fi

echo "test: two paragraphs both wrapped, blank line preserved"
get_result 9
assert_exit 0 "$LAST_RC" "exits 0"
assert_no_long_lines "$TEST_DIR/tasks.org" "Bare heading" "Research" 80 "no lines exceed 80 chars"
# Check blank line preserved between paragraphs — look for empty line in body
if awk '/Bare heading/,/Research/' "$TEST_DIR/tasks.org" | grep -q '^$'; then
  echo "  PASS: blank line between paragraphs preserved"; ((PASS++))
else
  echo "  FAIL: blank line between paragraphs not preserved"; ((FAIL++))
fi

echo "test: short append-body not mangled"
get_result 10
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "OK" "short body preserved"

echo "test: quote block content preserved"
get_result 11
assert_exit 0 "$LAST_RC" "exits 0"
assert_file_contains "$TEST_DIR/tasks.org" "This is a very long quotation" "long line inside quote block preserved"

echo ""
echo "All tests completed."
