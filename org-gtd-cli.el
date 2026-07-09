;;; org-gtd-cli.el --- CLI interface for org-mode GTD system -*- lexical-binding: t; -*-

;; Standalone org-mode GTD tool for batch mode (emacs --batch -q).
;; No Doom Emacs, no external packages — pure built-in org-mode + cl-lib.
;;
;; Shared GTD config (TODO keywords, tags, project detection, skip functions,
;; agenda commands) is loaded from gtd-core.el via `-l` before this file.

(require 'org)
(require 'org-agenda)
(require 'org-archive)
(require 'org-id)
(require 'cl-lib)

;; Load `userlock' eagerly: it is NOT preloaded, and the C file-lock code
;; autoloads it on the first supersession event.  If that first event happens
;; inside `org-gtd-cli/daemon-dispatch', loading the file redefines
;; `ask-user-about-supersession-threat' and silently clobbers the cl-letf
;; shadow installed there — resurrecting the interactive prompt the shadow
;; exists to suppress.  (`load' because userlock.el has no `provide'.)
(load "userlock" nil t)

;; ══════════════════════════════════════════════════════════════════════════════
;; CLI-specific configuration
;; ══════════════════════════════════════════════════════════════════════════════

;; Prevent lock file conflicts with running Doom instance
(setq create-lockfiles nil)

;; Don't maintain org-id's global location registry. Mutations lazily create a
;; stable `:ID:' via `org-id-get-create' (see `org-gtd-cli/maybe-create-id'),
;; whose `org-id-add-location' wants to read `org-id-locations-file' — which
;; lives under our throwaway `user-emacs-directory' and never exists, producing
;; the harmless "Could not read 'org-id-locations' ... setting it to nil"
;; warning on every batch mutation (and once per daemon start). We resolve IDs
;; by scanning agenda files directly (`org-gtd-cli/find-task-by-id'), never via
;; the registry, so disabling it makes `org-id-add-location' a no-op — killing
;; the warning while `org-id-get-create' still writes the `:ID:' property.
(setq org-id-track-globally nil)

;; Suppress "Saving file..."/"Wrote ..." chatter from `save-buffer'. In daemon
;; mode the Lisp `message' is rebound to capture stderr (see
;; `org-gtd-cli/daemon-dispatch'), but `save-buffer'/`write-region' emit those
;; notices through the *C-level* message primitive, which bypasses that Lisp
;; redefinition and leaks onto the daemon's stdout — corrupting `--json' output.
;; `save-silently' makes `basic-save-buffer' skip the Lisp notice AND wrap the
;; write in `with-suppressed-message' (binding `inhibit-message'), which the
;; C-level "Wrote" message respects. Does not reproduce in batch (C messages go
;; to stderr there), so it is daemon-mode-specific.
(setq save-silently t)

;; Optional fixed reference date for archive recency checks (deterministic
;; tests against static fixtures). ORG_GTD_CLI_NOW="YYYY-MM-DD" pins "now";
;; unset = live clock. See `gtd/now-reference' in gtd-core.el.
(let ((now (getenv "ORG_GTD_CLI_NOW")))
  (when (and now (not (string-empty-p now)))
    (setq gtd/now-reference (org-time-string-to-time now))))

;; Isolated user-emacs-directory (set by caller, but ensure a default)
(unless (file-directory-p user-emacs-directory)
  (make-directory user-emacs-directory t))

;; Org directory from environment or default
(setq org-directory (or (getenv "ORG_DIRECTORY")
                        (expand-file-name "~/org/")))
;; Ensure trailing slash
(unless (string-suffix-p "/" org-directory)
  (setq org-directory (concat org-directory "/")))

(setq org-agenda-files (list org-directory)
      org-default-notes-file (concat org-directory "inbox.org")
      org-startup-with-inline-images nil)

;; Override DEFER from core: drop @ to avoid interactive note prompt in batch
(setq org-todo-keywords
      '((sequence "TODO(t)" "NEXT(n)" "|" "DONE(d!/!)")
        (sequence "WAITING(w/!)" "DEFER(f/!)" "|" "CANCELLED(c/!)")))

;; ══════════════════════════════════════════════════════════════════════════════
;; JSON mode
;; ══════════════════════════════════════════════════════════════════════════════

(defvar org-gtd-cli/json-mode
  (equal (getenv "ORG_GTD_CLI_JSON") "1")
  "When non-nil, output JSON instead of human-readable text.")

(defvar org-gtd-cli/full-mode
  (equal (getenv "ORG_GTD_CLI_FULL") "1")
  "When non-nil, include body text in list commands (search, subtasks, agenda).")

(defvar org-gtd-cli/forced-id nil
  "When non-nil, `org-gtd-cli/find-task' resolves by this org :ID:
instead of by substring.")

(defvar org-gtd-cli/forced-create-id nil
  "When non-nil, `org-gtd-cli/find-task' ensures the resolved task
has an org id (lazy create).")

(defun org-gtd-cli/json-encode (alist)
  "Serialize ALIST to a JSON string safe for `princ'/`message' output.
`json-serialize' returns a *unibyte* UTF-8 byte string.  Writing that raw
double-encodes every non-ASCII byte under a non-UTF-8 locale — which is
exactly the environment the CLI runs in (systemd services and the bwrap
sandbox start Emacs with no LANG), producing invalid JSON like
\"\\342\\200\\224\" / mojibake for em-dashes and accents.  Decoding the
bytes back to a multibyte string makes `princ' emit clean UTF-8 regardless
of locale, and keeps the daemon-dispatch capture (which rebinds
`standard-output') working — unlike raw byte writers such as
`send-string-to-terminal'."
  (decode-coding-string (json-serialize alist) 'utf-8))

(defun org-gtd-cli/output (alist)
  "Output ALIST as JSON (json-mode) or do nothing (text mode).
In JSON mode, serializes ALIST with `org-gtd-cli/json-encode' and prints to
stdout.  In text mode, this is a no-op — callers handle their own text output."
  (when org-gtd-cli/json-mode
    (princ (org-gtd-cli/json-encode alist))
    (princ "\n")))

;; ══════════════════════════════════════════════════════════════════════════════
;; Daemon mode support
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/revert-org-buffers ()
  "Revert org buffers whose files have changed on disk.
Called before each daemon-dispatch to ensure fresh file contents."
  (dolist (buf (buffer-list))
    (when (and (buffer-file-name buf)
               (string-suffix-p ".org" (buffer-file-name buf))
               (or (buffer-modified-p buf)
                   (not (verify-visited-file-modtime buf))))
      (with-current-buffer buf
        (revert-buffer t t t)))))

(defun org-gtd-cli/daemon-dispatch (body-fn json-mode-p full-mode-p org-dir stdout-file stderr-file exit-file)
  "Evaluate BODY-FN with output captured to temp files.
Used by emacsclient --eval in daemon mode.

JSON-MODE-P sets `org-gtd-cli/json-mode' for this call.
FULL-MODE-P sets `org-gtd-cli/full-mode' for this call.
ORG-DIR sets `org-directory' and `org-agenda-files' for this call.
STDOUT-FILE receives princ output, STDERR-FILE receives message output,
EXIT-FILE receives the numeric exit code (from kill-emacs calls)."
  ;; Update org-directory per call (may differ between invocations)
  (setq org-directory org-dir)
  (unless (string-suffix-p "/" org-directory)
    (setq org-directory (concat org-directory "/")))
  (setq org-agenda-files (list org-directory))
  (setq org-gtd-cli/json-mode json-mode-p)
  (setq org-gtd-cli/full-mode full-mode-p)
  (let ((org-gtd-cli--exit-code 0)
        (stderr-msgs '()))
    ;; If a file's mtime changes while a call is in flight (Doom auto-save or
    ;; an external process editing files under the daemon), Emacs raises interactive
    ;; supersession prompts — minibuffer reads that block the headless daemon
    ;; forever, queueing every later emacsclient call behind them.  The revert
    ;; below makes the buffer authoritative, so suppress the prompts and let
    ;; saves overwrite the file.  The C-level buffer-modification check
    ;; (`lock-file', reached from `prepare_to_modify_buffer' — including
    ;; during the revert itself) calls this function; return nil instead of
    ;; prompting or signaling `file-supersession' so the edit proceeds.
    (cl-letf (((symbol-function 'ask-user-about-supersession-threat)
               (lambda (_filename) nil)))
      (org-gtd-cli/revert-org-buffers)
      (with-temp-file stdout-file
        (let ((standard-output (current-buffer)))
          (cl-letf (((symbol-function 'kill-emacs)
                     (lambda (&optional code)
                       (setq org-gtd-cli--exit-code (or code 0))
                       (throw 'org-gtd-cli-exit nil)))
                    ((symbol-function 'message)
                     (lambda (fmt &rest args)
                       (when fmt
                         (push (apply #'format fmt args) stderr-msgs))))
                    ;; Second supersession path: `basic-save-buffer' has its
                    ;; own inline "has changed since visited or saved.  Save
                    ;; anyway?" `yes-or-no-p' (as does `find-file-noselect'),
                    ;; gated on this predicate.  Claiming the buffer is in
                    ;; sync skips those prompts too.  Only shadowed around
                    ;; BODY-FN — the revert above needs the real predicate to
                    ;; detect stale buffers.
                    ((symbol-function 'verify-visited-file-modtime)
                     (lambda (&optional _buf) t)))
            (catch 'org-gtd-cli-exit
              (funcall body-fn))))))
    (with-temp-file stderr-file
      (dolist (msg (nreverse stderr-msgs))
        (insert msg "\n")))
    (with-temp-file exit-file
      (insert (number-to-string org-gtd-cli--exit-code))))
  nil)

;; ══════════════════════════════════════════════════════════════════════════════
;; Error output
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/error (fmt &rest args)
  "Write a diagnostic/error message.
In Emacs batch mode, `message' writes to stderr while `princ' writes to stdout.
In JSON mode, outputs {\"error\": \"...\"} to STDOUT (so that `... 2>/dev/null
| jq' and `json.loads(stdout)' see the error object); the `--json' contract is
that stdout always carries exactly one JSON object — data, error-with-hint, or
ambiguous-matches — while stderr carries only opaque Emacs diagnostics.
In text mode, the message goes to stderr.
Use this for errors, warnings, and hints — never for command output data."
  (if org-gtd-cli/json-mode
      (let ((msg (apply #'format fmt args)))
        (princ (org-gtd-cli/json-encode `((error . ,msg))))
        (princ "\n"))
    (apply #'message fmt args)))

;; ══════════════════════════════════════════════════════════════════════════════
;; Body text validation
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/validate-body-text (text)
  "Error and exit if TEXT contains org headings that would corrupt the file."
  (when (and text (not (string-empty-p text))
             (or (string-match-p "\\`\\*+ " text)
                 (string-match-p "\n\\*+ " text)))
    (org-gtd-cli/error
     "%s"
     (concat
      "Error: body text contains org headings (\"* \" at start of line), "
      "which would corrupt\nthe file structure. Use \"- list items\" instead "
      "of headings. For structured content\nwith sections, use: "
      "org-gtd-cli add-note --link-task \"task heading\" --title \"...\"\n"))
    (kill-emacs 1)))

;; ══════════════════════════════════════════════════════════════════════════════
;; Sibling reordering by state
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/state-sort-priority (state)
  "Return sort priority for STATE. Lower = earlier among siblings."
  (cond
   ((null state) 5)
   ((member state org-done-keywords) 0)
   ((string= state "NEXT") 1)
   ((string= state "TODO") 2)
   ((string= state "WAITING") 3)
   ((string= state "DEFER") 4)
   (t 5)))

(defun org-gtd-cli/reorder-siblings-by-state ()
  "Sort siblings of heading at point by state priority.
Point must be on a child heading (level > 1). Only sorts when all
siblings have TODO keywords (skips if organizational headings are mixed in)."
  (org-back-to-heading 'invisible-ok)
  (when (> (org-current-level) 1)
    (let ((level (org-current-level))
          (all-tasks t))
      ;; Check that all siblings at this level have TODO keywords
      (save-excursion
        (org-up-heading-safe)
        (let ((parent-end (save-excursion (org-end-of-subtree t) (point))))
          (save-excursion
            (forward-line 1)
            (while (and all-tasks (< (point) parent-end)
                        (re-search-forward org-heading-regexp parent-end t))
              (when (and (= (org-current-level) level)
                         (not (org-get-todo-state)))
                (setq all-tasks nil))))
          (when all-tasks
            (org-sort-entries nil ?f
                             (lambda ()
                               (number-to-string
                                (org-gtd-cli/state-sort-priority
                                 (org-get-todo-state)))))))))))

;; ══════════════════════════════════════════════════════════════════════════════
;; Shared helpers
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/relative-filename (filepath)
  "Return FILEPATH relative to org-directory."
  (file-relative-name filepath org-directory))

(defalias 'org-gtd-cli/heading-path-at-point #'gtd/heading-path-at-point
  "Compatibility alias.  See `gtd/heading-path-at-point' in gtd-core.el.")

(defun org-gtd-cli/strip-markup (s)
  "Strip org markup from S for fuzzy matching.
Strips org links and paired emphasis markers."
  (let ((result s))
    ;; Strip org links: two-part links keep description, bare links keep target
    (setq result (replace-regexp-in-string
                  (concat "\\[" "\\[" "\\([^][]*\\)" "\\]"
                          "\\[" "\\([^][]*\\)" "\\]" "\\]")
                  "\\2" result))
    (setq result (replace-regexp-in-string
                  (concat "\\[" "\\[" "\\([^][]*\\)" "\\]" "\\]")
                  "\\1" result))
    ;; Strip paired emphasis markers
    (dolist (marker '("*" "/" "_" "=" "~" "+"))
      (setq result
            (replace-regexp-in-string
             (concat (regexp-quote marker)
                     "\\([^ \t\n]\\(?:[^\n]*?[^ \t\n]\\)?\\)"
                     (regexp-quote marker))
             "\\1" result)))
    result))

(defun org-gtd-cli/match-tag-filter (tag-filter tags)
  "Check if TAGS satisfy TAG-FILTER.
TAG-FILTER wire format: AND groups joined by |, OR alternatives
within a group joined by comma.  + within a group is equivalent
to |.  Example: \"@agent|@errand,@phone\" means the task must have
@agent AND must have either @errand or @phone.
TAGS is a list of tag strings for the heading."
  (let ((and-groups (split-string tag-filter "[|+]")))
    (cl-every (lambda (group)
                (let ((or-tags (split-string group ",")))
                  (cl-some (lambda (tag)
                             (member tag tags))
                           or-tags)))
              and-groups)))

(defun org-gtd-cli/get-explicit-priority ()
  "Return the priority letter if explicitly set on heading at point, or nil.
`org-entry-get' returns the default priority (B) even when no cookie is set;
this function only returns a value when a [#X] cookie is actually present."
  (let ((pri (nth 3 (org-heading-components))))
    (when pri (char-to-string pri))))

(defun org-gtd-cli/strip-priority-cookie (s)
  "Strip priority cookies like [#A], [#B], [#C] from S.
Agents sometimes paste headings including the priority cookie."
  (replace-regexp-in-string "\\[#[A-C]\\] ?" "" s))

(defun org-gtd-cli/strip-logbook (s)
  "Remove :LOGBOOK:...:END: drawer blocks from string S.
Tolerates leading whitespace on the drawer markers, so indented drawers
(e.g. under nested subtasks in a full-subtree dump) are stripped too."
  (let ((result s))
    (while (string-match "^[ \t]*:LOGBOOK:[ \t]*\n\\(?:.*\n\\)*?[ \t]*:END:[ \t]*\n?" result)
      (setq result (replace-match "" t t result)))
    (string-trim result)))

(defun org-gtd-cli/get-body-at-point ()
  "Extract body text of heading at point.
Returns the body string, or nil if empty.  Strips LOGBOOK drawers
and trailing creation timestamp.  Point must be on a heading line."
  (let* ((level (org-current-level))
         (child-level (1+ level))
         (subtree-end (save-excursion (org-end-of-subtree t) (point)))
         (body-start (save-excursion (org-end-of-meta-data t) (point)))
         (body-end (save-excursion
                     (goto-char (if (> body-start subtree-end) subtree-end body-start))
                     (if (re-search-forward
                          (format "^\\*\\{%d,\\} " child-level)
                          subtree-end t)
                         (line-beginning-position)
                       subtree-end)))
         (raw-body (string-trim (buffer-substring-no-properties
                                 (min body-start body-end) body-end)))
         (no-logbook (org-gtd-cli/strip-logbook raw-body))
         (body (if (string-match "\\[[-0-9]+ [A-Z][a-z]+\\( [0-9:]+\\)?\\]\\'" no-logbook)
                   (string-trim (substring no-logbook 0 (match-beginning 0)))
                 no-logbook)))
    (if (string-empty-p body) nil body)))

(defun org-gtd-cli/parse-session-entries ()
  "Parse agent session entries from LOGBOOK drawer at point.
Returns a list of alists with keys agent, session_id, timestamp.
Point must be on a heading line."
  (let ((entries '())
        (subtree-end (save-excursion (org-end-of-subtree t) (point)))
        (meta-end (save-excursion (org-end-of-meta-data t) (point))))
    (save-excursion
      (goto-char (line-beginning-position))
      (when (re-search-forward "^[ \t]*:LOGBOOK:" meta-end t)
        (let ((drawer-end (save-excursion
                            (re-search-forward "^[ \t]*:END:" subtree-end t))))
          (when drawer-end
            (while (re-search-forward
                    "^- Agent session \\([a-z_]+\\):\\([^ ]+\\) \\[\\([^]]+\\)\\]"
                    drawer-end t)
              (push `((agent . ,(match-string 1))
                      (session_id . ,(match-string 2))
                      (timestamp . ,(match-string 3)))
                    entries))))))
    (nreverse entries)))

(defun org-gtd-cli/add-session-id (substring session-id &optional index)
  "Add an agent session ID to the LOGBOOK drawer of a task.
SESSION-ID should be in the format agent:uuid.
Idempotent: if SESSION-ID already exists, this is a no-op."
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (subtree-end (save-excursion (org-end-of-subtree t) (point)))
              (meta-end (save-excursion (org-end-of-meta-data t) (point)))
              (timestamp (format-time-string "[%Y-%m-%d %a %H:%M]"))
              (entry-line (format "- Agent session %s %s" session-id timestamp))
              (already-exists nil))
         ;; Check for existing session ID (idempotent)
         (save-excursion
           (goto-char (line-beginning-position))
           (when (re-search-forward "^[ \t]*:LOGBOOK:" meta-end t)
             (let ((drawer-end (save-excursion
                                 (re-search-forward "^[ \t]*:END:" subtree-end t))))
               (when (and drawer-end
                          (re-search-forward
                           (regexp-quote session-id)
                           drawer-end t))
                 (setq already-exists t)))))
         (if already-exists
             (progn
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/output
                    `((version . 1)
                      (command . "add-session-id")
                      (status . "no-op")
                      (heading . ,heading)
                      (file . ,rel-file)
                      (message . "Session ID already recorded")))
                 (princ (format "Session ID already recorded for: %s (%s)\n"
                                heading rel-file))))
           ;; Find or create LOGBOOK drawer and insert entry
           (save-excursion
             (goto-char (line-beginning-position))
             (if (re-search-forward "^[ \t]*:LOGBOOK:" meta-end t)
                 ;; Existing LOGBOOK — insert after :LOGBOOK: line
                 (progn
                   (forward-line 1)
                   (insert entry-line "\n"))
               ;; No LOGBOOK — create one after properties/scheduling
               (goto-char (cdr buf-pos))
               (org-end-of-meta-data)
               (when (> (point) subtree-end)
                 (goto-char subtree-end))
               (insert ":LOGBOOK:\n" entry-line "\n:END:\n")))
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/output
                `((version . 1)
                  (command . "add-session-id")
                  (status . "added")
                  (heading . ,heading)
                  (file . ,rel-file)
                  (session_id . ,session-id)))
             (princ (format "Added session %s to: %s (%s)\n"
                            session-id heading rel-file)))))))))

(defun org-gtd-cli/get-session-ids (substring &optional index)
  "Get all agent session IDs from a task's LOGBOOK drawer."
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (entries (org-gtd-cli/parse-session-entries)))
         (if org-gtd-cli/json-mode
             (org-gtd-cli/output
              `((version . 1)
                (command . "get-session-ids")
                (heading . ,heading)
                (file . ,rel-file)
                (sessions . ,(apply #'vector entries))))
           (if entries
               (dolist (e entries)
                 (princ (format "%s:%s [%s]\n"
                                (cdr (assq 'agent e))
                                (cdr (assq 'session_id e))
                                (cdr (assq 'timestamp e)))))
             (princ "No session IDs found.\n"))))))))

(defun org-gtd-cli/find-task-by-id (id)
  "Find the FIRST heading whose org :ID: equals ID across all agenda files.
Returns a cons (buffer . position).  Matches ANY heading — category
headings, DONE tasks, and active tasks alike — since the dashboard
addresses arbitrary rows.  On no match, mirrors the no-match arm of
`org-gtd-cli/find-task': emits a JSON error (json-mode) or text error,
then exits 1."
  (catch 'found
    (dolist (file (org-agenda-files))
      (when (file-exists-p file)
        (with-current-buffer (find-file-noselect file)
          (org-with-wide-buffer
           (goto-char (point-min))
           (while (re-search-forward org-heading-regexp nil t)
             (when (equal (org-entry-get nil "ID") id)
               (throw 'found (cons (current-buffer)
                                   (line-beginning-position)))))))))
    (let ((hint "Use a task id from list/show output, or address by SUBSTR."))
      (if org-gtd-cli/json-mode
          ;; JSON: error+hint object goes to stdout (see `org-gtd-cli/error').
          (org-gtd-cli/output
           `((error . ,(format "No task found with id \"%s\"" id))
             (hint . ,hint)))
        (org-gtd-cli/error "No task found with id \"%s\"" id)
        (org-gtd-cli/error "Hint: %s" hint)))
    (kill-emacs 1)))

(defun org-gtd-cli/maybe-create-id (buf-pos)
  "If `org-gtd-cli/forced-create-id', ensure an org id at BUF-POS (idempotent).
Return BUF-POS.  `org-id-get-create' is a no-op when the entry already
has an id, so this never dirties the buffer on re-find."
  (when org-gtd-cli/forced-create-id
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (org-id-get-create))))
  buf-pos)

(defun org-gtd-cli/find-task (substring &optional index include-done exact)
  "Find a task by SUBSTRING match across all agenda files.
Returns a cons (buffer . position) or exits with appropriate code.
If INDEX is non-nil, select the Nth match (1-based).
If INCLUDE-DONE is non-nil, also match done tasks.
If EXACT is non-nil, require full heading match instead of substring.
When `org-gtd-cli/forced-id' is non-nil, resolve by org :ID: instead of
SUBSTRING (ignoring INDEX/INCLUDE-DONE/EXACT).  When
`org-gtd-cli/forced-create-id' is non-nil, the resolved task is given an
org id if it lacks one (lazy create, idempotent)."
  (if org-gtd-cli/forced-id
      (org-gtd-cli/maybe-create-id
       (org-gtd-cli/find-task-by-id org-gtd-cli/forced-id))
    (setq substring (org-gtd-cli/strip-priority-cookie substring))
    (let ((matches '()))
      (dolist (file (org-agenda-files))
        (when (file-exists-p file)
          (with-current-buffer (find-file-noselect file)
            (org-with-wide-buffer
             (goto-char (point-min))
             (while (re-search-forward org-heading-regexp nil t)
               (let* ((state (org-get-todo-state))
                      (heading (org-get-heading t t t t))
                      (pos (line-beginning-position)))
                 (when (and state
                            (or include-done
                                (not (member state org-done-keywords)))
                            (if exact
                                (or (string= (downcase substring) (downcase heading))
                                    (string= (downcase substring)
                                             (downcase (org-gtd-cli/strip-markup heading))))
                              (or (string-match-p (regexp-quote substring)
                                                  (downcase heading))
                                  (string-match-p (regexp-quote substring)
                                                  (downcase (org-gtd-cli/strip-markup heading))))))
                   (push (list (current-buffer) pos
                               state heading
                               (org-gtd-cli/relative-filename file))
                         matches))))))))
      (setq matches (nreverse matches))
      (cond
       ((null matches)
        (let* ((cat-paths (delete-dups
                           (mapcar (lambda (m) (nth 3 m))
                                   (org-gtd-cli/find-category-matches substring))))
               (hint (cond
                      ((null cat-paths)
                       "Try a shorter substring, or use 'search' for partial matches.")
                      ((null (cdr cat-paths))
                       (format (concat "\"%s\" matches a category heading, not a task. "
                                       "To add a task under it: "
                                       "org-gtd-cli add-task --category \"%s\"")
                               substring (car cat-paths)))
                      (t
                       (format (concat "\"%s\" matches category headings, not tasks: %s. "
                                       "To add a task under one: "
                                       "org-gtd-cli add-task --category \"%s\"")
                               substring
                               (mapconcat (lambda (p) (format "\"%s\"" p))
                                          (seq-take cat-paths 3) ", ")
                               (car cat-paths))))))
          (if org-gtd-cli/json-mode
              ;; JSON: error+hint object goes to stdout (see `org-gtd-cli/error').
              (org-gtd-cli/output
               `((error . ,(format "No task found matching \"%s\"" substring))
                 (hint . ,hint)))
            (org-gtd-cli/error "No task found matching \"%s\"" substring)
            (org-gtd-cli/error "Hint: %s" hint)))
        (kill-emacs 1))
       ((and (= (length matches) 1) (not index))
        (org-gtd-cli/maybe-create-id
         (cons (nth 0 (car matches)) (nth 1 (car matches)))))
       ((and index (> index 0) (<= index (length matches)))
        (let ((m (nth (1- index) matches)))
          (org-gtd-cli/maybe-create-id (cons (nth 0 m) (nth 1 m)))))
       ((and index (or (<= index 0) (> index (length matches))))
        (org-gtd-cli/error "Index %d out of range (1-%d)" index (length matches))
        (kill-emacs 1))
       (t
        (if org-gtd-cli/json-mode
            ;; JSON: match list on stdout, hint on stderr
            (let ((match-list '())
                  (i 1))
              (dolist (m matches)
                (let* ((mbuf (nth 0 m)) (mpos (nth 1 m))
                       (parent (with-current-buffer mbuf
                                 (org-with-wide-buffer
                                  (goto-char mpos)
                                  (and (org-up-heading-safe)
                                       (org-get-heading t t t t)))))
                       (path (with-current-buffer mbuf
                               (org-with-wide-buffer
                                (goto-char mpos)
                                (org-gtd-cli/heading-path-at-point)))))
                  (push `((index . ,i) (heading . ,(nth 3 m))
                          (state . ,(nth 2 m))
                          (file . ,(nth 4 m))
                          (parent . ,(or parent :null))
                          (path . ,path))
                        match-list))
                (cl-incf i))
              (org-gtd-cli/output
               `((error . "Multiple matches")
                 (matches . ,(apply #'vector (nreverse match-list)))
                 (hint . "Use --index N to select one."))))
          ;; Text: all on stderr
          (org-gtd-cli/error "Multiple matches:")
          (let ((i 1))
            (dolist (m matches)
              (org-gtd-cli/error "[%d] %s %s (%s)"
                                 i (nth 2 m) (nth 3 m) (nth 4 m))
              (cl-incf i)))
          (org-gtd-cli/error "\nUse --index N to select one."))
        (kill-emacs 2))))))

(defun org-gtd-cli/make-timestamp (date-str &optional time-str active)
  "Create an org timestamp from DATE-STR (YYYY-MM-DD) and optional TIME-STR.
If ACTIVE is non-nil, use angle brackets; otherwise square brackets."
  (let* ((parts (split-string date-str "-"))
         (year (string-to-number (nth 0 parts)))
         (month (string-to-number (nth 1 parts)))
         (day (string-to-number (nth 2 parts)))
         (dow (calendar-day-name (list month day year) t))
         (open (if active "<" "["))
         (close (if active ">" "]")))
    (if time-str
        (format "%s%s %s %s%s" open date-str dow time-str close)
      (format "%s%s %s%s" open date-str dow close))))

(defun org-gtd-cli/current-inactive-timestamp ()
  "Return current date/time as an inactive org timestamp."
  (format-time-string "[%Y-%m-%d %a %H:%M]"))

(defun org-gtd-cli/fill-text (text)
  "Fill TEXT to 100 columns, respecting org syntax (blocks, lists, timestamps)."
  (if (or (null text) (string-empty-p text))
      text
    (with-temp-buffer
      (org-mode)
      (insert text)
      (let ((fill-column 100))
        ;; Phase 1: Insert blank line before standalone timestamps so
        ;; org-element treats them as separate elements.  Mark inserted
        ;; newlines with a text property for precise removal later.
        (goto-char (point-min))
        (while (not (eobp))
          (when (and (looking-at "\\[[-0-9]+ [A-Z][a-z]+[^]\n]*\\][ \t]*$")
                     (not (bobp))
                     (save-excursion
                       (forward-line -1)
                       (not (looking-at "[ \t]*$"))))
            (beginning-of-line)
            (insert "\n")
            (put-text-property (1- (point)) (point) 'fill-sep t))
          (forward-line 1))
        ;; Phase 2: Fill non-block content with org-fill-paragraph
        ;; (handles lists, adaptive fill, etc.)
        (goto-char (point-min))
        (while (not (eobp))
          (cond
           ;; Skip blocks verbatim
           ((looking-at "[ \t]*#\\+begin_")
            (forward-line 1)
            (while (and (not (eobp))
                        (not (looking-at "[ \t]*#\\+end_")))
              (forward-line 1))
            (when (not (eobp)) (forward-line 1)))
           ;; Skip block end markers, standalone timestamps, empty lines
           ((or (looking-at "[ \t]*#\\+end_")
                (looking-at "\\[[-0-9]+ [A-Z][a-z]+")
                (looking-at "[ \t]*$"))
            (forward-line 1))
           (t
            ;; Check if the paragraph contains an org link — filling a
            ;; paragraph with [[...][...]] links can break link syntax
            ;; when the link exceeds fill-column.
            (if (save-excursion
                  (catch 'found
                    (while (and (not (eobp))
                                (not (looking-at "[ \t]*$"))
                                (not (looking-at "[ \t]*#\\+begin_"))
                                (not (looking-at "\\[[-0-9]+ [A-Z][a-z]+")))
                      (when (looking-at ".*\\[\\[")
                        (throw 'found t))
                      (forward-line 1))
                    nil))
                ;; Paragraph has org link — skip without filling
                (while (and (not (eobp))
                            (not (looking-at "[ \t]*$"))
                            (not (looking-at "[ \t]*#\\+begin_"))
                            (not (looking-at "\\[[-0-9]+ [A-Z][a-z]+")))
                  (forward-line 1))
              ;; No link — fill normally
              (org-fill-paragraph)
              (forward-line 1)))))
        ;; Phase 3: Remove the blank lines we inserted (by text property)
        (let ((pos (point-min)))
          (while (setq pos (text-property-any pos (point-max) 'fill-sep t))
            (delete-region pos (1+ pos)))))
      (string-trim-right (buffer-string)))))

(defun org-gtd-cli/slugify (title)
  "Convert TITLE to a filename slug."
  (let ((slug (downcase title)))
    (setq slug (replace-regexp-in-string "[^a-z0-9 -]" "" slug))
    (setq slug (replace-regexp-in-string " +" "-" slug))
    (setq slug (replace-regexp-in-string "-+" "-" slug))
    (setq slug (replace-regexp-in-string "^-\\|-$" "" slug))
    slug))

(defun org-gtd-cli/format-tags (tags-csv)
  "Format a comma-separated TAGS-CSV string as org tags :tag1:tag2:."
  (when (and tags-csv (not (string-empty-p tags-csv)))
    (let ((tags (split-string tags-csv ",")))
      (concat ":" (mapconcat #'identity tags ":") ":"))))

(defun org-gtd-cli/parse-index (index-str)
  "Parse INDEX-STR to integer, return nil if nil or empty."
  (when (and index-str (not (string-empty-p index-str))
             (not (equal index-str "nil")))
    (string-to-number index-str)))

(defun org-gtd-cli/build-entry (level state title &optional priority tags-csv
                                       schedule deadline body
                                       schedule-time deadline-time)
  "Build an org entry string.
LEVEL is the heading depth (number of stars).
Returns the entry text (without trailing newline at very end)."
  (let ((parts '()))
    ;; Heading line
    (let ((heading (make-string level ?*)))
      (setq heading (concat heading " " (or state "TODO")))
      (when (and priority (not (string-empty-p priority)))
        (setq heading (concat heading " [#" priority "]")))
      (setq heading (concat heading " " title))
      (let ((tag-str (org-gtd-cli/format-tags tags-csv)))
        (when tag-str
          (setq heading (concat heading " " tag-str))))
      (push heading parts))
    ;; SCHEDULED/DEADLINE
    (when (and schedule (not (string-empty-p schedule)))
      (push (concat "SCHEDULED: "
                    (org-gtd-cli/make-timestamp schedule schedule-time t))
            parts))
    (when (and deadline (not (string-empty-p deadline)))
      (push (concat "DEADLINE: "
                    (org-gtd-cli/make-timestamp deadline deadline-time t))
            parts))
    ;; Body
    (when (and body (not (string-empty-p body)))
      (push body parts))
    ;; Creation timestamp
    (push (org-gtd-cli/current-inactive-timestamp) parts)
    (mapconcat #'identity (nreverse parts) "\n")))

;; ══════════════════════════════════════════════════════════════════════════════
;; Commands
;; ══════════════════════════════════════════════════════════════════════════════

;; --- org-timestamp ---

(defun org-gtd-cli/org-timestamp (date-str &optional time-str inactive)
  "Output a correctly formatted org timestamp.
If INACTIVE is non-nil, use square brackets (inactive timestamp)."
  (condition-case err
      (let ((ts (org-gtd-cli/make-timestamp date-str time-str (not inactive))))
        (princ (concat ts "\n"))
        (kill-emacs 0))
    (error
     (org-gtd-cli/error "Error: invalid date \"%s\": %s" date-str (error-message-string err))
     (kill-emacs 1))))

;; --- agenda ---

(defun org-gtd-cli/agenda (&optional states-csv tags-match from-date to-date)
  "Query tasks across all org files."
  (let ((state-filter (when (and states-csv (not (string-empty-p states-csv))
                                (not (equal states-csv "nil")))
                        (split-string states-csv ",")))
        (tag-filter (when (and tags-match (not (string-empty-p tags-match))
                               (not (equal tags-match "nil")))
                      tags-match))
        (from-time (when (and from-date (not (string-empty-p from-date))
                              (not (equal from-date "nil")))
                     (org-time-string-to-time
                      (org-gtd-cli/make-timestamp from-date nil t))))
        (to-time (when (and to-date (not (string-empty-p to-date))
                            (not (equal to-date "nil")))
                   (org-time-string-to-time
                    (org-gtd-cli/make-timestamp to-date nil t))))
        (results '()))
    (dolist (file (org-agenda-files))
      (when (file-exists-p file)
        (with-current-buffer (find-file-noselect file)
          (org-with-wide-buffer
           (goto-char (point-min))
           (while (re-search-forward org-heading-regexp nil t)
             (let* ((state (org-get-todo-state))
                    (heading (org-get-heading t t t t))
                    (priority-char (org-gtd-cli/get-explicit-priority))
                    (tags (org-get-tags))
                    (tags-str (when tags (concat ":" (mapconcat #'identity tags ":") ":")))
                    (scheduled (org-entry-get nil "SCHEDULED"))
                    (deadline (org-entry-get nil "DEADLINE"))
                    (rel-file (org-gtd-cli/relative-filename file)))
               ;; Filter: must have a TODO state
               (when (and state
                          ;; State filter
                          (if state-filter
                              (member state state-filter)
                            ;; Default: non-done
                            (not (member state org-done-keywords)))
                          ;; Tag filter (AND/OR: | for AND groups, , for OR within group)
                          (or (not tag-filter)
                              (org-gtd-cli/match-tag-filter tag-filter tags))
                          ;; Date range filter (on scheduled or deadline)
                          ;; When date filters are active, tasks without
                          ;; any date are excluded (they can't be in range)
                          (or (not from-time)
                              (let ((s-time (when scheduled
                                              (org-time-string-to-time scheduled)))
                                    (d-time (when deadline
                                              (org-time-string-to-time deadline))))
                                (or (and s-time (not (time-less-p s-time from-time)))
                                    (and d-time (not (time-less-p d-time from-time))))))
                          (or (not to-time)
                              (let ((s-time (when scheduled
                                              (org-time-string-to-time scheduled)))
                                    (d-time (when deadline
                                              (org-time-string-to-time deadline))))
                                (or (and s-time (time-less-p s-time
                                                             (time-add to-time (seconds-to-time 86400))))
                                    (and d-time (time-less-p d-time
                                                             (time-add to-time (seconds-to-time 86400))))))))
                 (let ((parent-heading
                        (save-excursion
                          (if (org-up-heading-safe)
                              (org-get-heading t t t t)
                            nil)))
                       (is-project
                        (org-gtd-cli/has-todo-children-p)))
                   (let ((body-text (when org-gtd-cli/full-mode
                                      (org-gtd-cli/get-body-at-point)))
                         (props (org-gtd-cli/properties-at-point))
                         (id (org-entry-get nil "ID")))
                     (push (list state heading priority-char
                                 (vconcat (mapcar #'identity tags))
                                 tags-str rel-file scheduled deadline
                                 parent-heading is-project body-text props id)
                           results))))))))))
    (setq results (nreverse results))
    (if org-gtd-cli/json-mode
        (org-gtd-cli/output-agenda-json results)
      (dolist (r results)
        (princ (org-gtd-cli/format-agenda-line r))
        (when (and org-gtd-cli/full-mode (nth 10 r))
          (princ (concat "    " (replace-regexp-in-string
                                 "\n" "\n    " (nth 10 r))
                         "\n\n")))))
    (kill-emacs 0)))

(defun org-gtd-cli/has-todo-children-p ()
  "Check if heading at point has direct children with TODO keywords."
  (let ((child-level (1+ (org-current-level)))
        (subtree-end (save-excursion (org-end-of-subtree t) (point)))
        (found nil))
    (save-excursion
      (forward-line 1)
      (while (and (not found) (< (point) subtree-end)
                  (re-search-forward org-heading-regexp subtree-end t))
        (when (and (= (org-current-level) child-level)
                   (org-get-todo-state))
          (setq found t))))
    found))

(defun org-gtd-cli/output-agenda-json (results)
  "Output RESULTS as JSON for the agenda command."
  (let ((tasks '()))
    (dolist (r results)
      (let ((task `((heading . ,(nth 1 r))
                    (state . ,(nth 0 r))
                    (priority . ,(or (nth 2 r) :null))
                    (tags . ,(or (nth 3 r) []))
                    (id . ,(or (nth 12 r) :null))
                    (file . ,(nth 5 r))
                    (scheduled . ,(or (nth 6 r) :null))
                    (deadline . ,(or (nth 7 r) :null))
                    (parent . ,(or (nth 8 r) :null))
                    (is_project . ,(if (nth 9 r) t :false))
                    (properties . ,(nth 11 r)))))
        (when org-gtd-cli/full-mode
          (setq task (append task `((body . ,(or (nth 10 r) :null))))))
        (push task tasks)))
    (org-gtd-cli/output
     `((version . 1)
       (command . "agenda")
       (tasks . ,(apply #'vector (nreverse tasks)))
       (count . ,(length results))))))

(defun org-gtd-cli/format-agenda-line (r)
  "Format a single agenda result R as a text line."
  (let ((state (nth 0 r))
        (heading (nth 1 r))
        (priority-char (nth 2 r))
        (tags-str (nth 4 r))
        (rel-file (nth 5 r))
        (scheduled (nth 6 r))
        (deadline (nth 7 r)))
    (concat state
            (when (and priority-char (not (string= priority-char "B")))
              (concat " [#" priority-char "]"))
            " " heading
            (when tags-str (concat " " tags-str))
            (format " (%s)" rel-file)
            (when scheduled (concat " S:" scheduled))
            (when deadline (concat " D:" deadline))
            "\n")))

;; --- search ---

(defun org-gtd-cli/search (substring &optional states-csv tag-filter file-name)
  "Search for tasks matching SUBSTRING in heading text.
Unlike `find-task' (which treats multiple matches as an error),
search intentionally returns all matches with exit code 0.
STATES-CSV defaults to \"TODO,NEXT\".  \"all\" means no state filter.
TAG-FILTER limits to tasks with that tag (supports inheritance).
FILE-NAME restricts search to a single file in org-directory."
  ;; Normalize: nil/empty SUBSTR means match all headings (filter-only mode)
  (when (and substring (not (string-empty-p substring)))
    (setq substring (org-gtd-cli/strip-priority-cookie substring)))
  (let* ((state-filter
          (cond
           ((or (null states-csv) (string-empty-p states-csv)
                (equal states-csv "nil"))
            '("TODO" "NEXT"))
           ((equal (downcase states-csv) "all") nil)
           (t (split-string states-csv ","))))
         (tag-filter (when (and tag-filter (not (string-empty-p tag-filter))
                                (not (equal tag-filter "nil")))
                       tag-filter))
         (files (if (and file-name (not (string-empty-p file-name))
                         (not (equal file-name "nil")))
                    (let ((f (expand-file-name file-name org-directory)))
                      (unless (file-exists-p f)
                        (org-gtd-cli/error "Error: file not found: %s" file-name)
                        (kill-emacs 1))
                      (list f))
                  (org-agenda-files)))
         (matches '()))
    (dolist (file files)
      (when (file-exists-p file)
        (with-current-buffer (find-file-noselect file)
          (org-with-wide-buffer
           (goto-char (point-min))
           (while (re-search-forward org-heading-regexp nil t)
             (let* ((state (org-get-todo-state))
                    (heading (org-get-heading t t t t))
                    (tags (org-get-tags))
                    (rel-file (org-gtd-cli/relative-filename file)))
               (when (and state
                          (or (null state-filter)
                              (member state state-filter))
                          ;; Substring filter (skip when no SUBSTR)
                          (or (null substring) (string-empty-p substring)
                              (string-match-p (regexp-quote substring)
                                              heading)
                              (string-match-p (regexp-quote substring)
                                              (org-gtd-cli/strip-markup heading)))
                          (or (not tag-filter)
                              (org-gtd-cli/match-tag-filter tag-filter tags)))
                 (let ((parent-heading
                        (save-excursion
                          (if (org-up-heading-safe)
                              (org-get-heading t t t t)
                            nil)))
                       (is-project
                        (save-excursion
                          (let ((child-level (1+ (org-current-level)))
                                (subtree-end (save-excursion (org-end-of-subtree t) (point)))
                                (found nil))
                            (save-excursion
                              (forward-line 1)
                              (while (and (not found) (< (point) subtree-end)
                                          (re-search-forward org-heading-regexp subtree-end t))
                                (when (and (= (org-current-level) child-level)
                                           (org-get-todo-state))
                                  (setq found t))))
                            found))))
                   (let ((body-text (when org-gtd-cli/full-mode
                                      (org-gtd-cli/get-body-at-point)))
                         (props (org-gtd-cli/properties-at-point))
                         (id (org-entry-get nil "ID")))
                     (push (list state heading rel-file
                                 (vconcat (mapcar #'identity tags))
                                 parent-heading is-project body-text props id)
                           matches))))))))))
    (setq matches (nreverse matches))
    (if org-gtd-cli/json-mode
        (let ((tasks '())
              (i 1))
          (dolist (m matches)
            (let* ((state (nth 0 m))
                   (heading (nth 1 m))
                   (rel-file (nth 2 m))
                   (tags (nth 3 m))
                   (parent (nth 4 m))
                   (is-project (nth 5 m))
                   (body (nth 6 m))
                   (props (nth 7 m))
                   (id (nth 8 m))
                   (task `((index . ,i)
                           (heading . ,heading)
                           (state . ,state)
                           (tags . ,(or tags []))
                           (id . ,(or id :null))
                           (file . ,rel-file)
                           (parent . ,(or parent :null))
                           (is_project . ,(if is-project t :false))
                           (properties . ,props))))
              (when org-gtd-cli/full-mode
                (setq task (append task `((body . ,(or body :null))))))
              (push task tasks)
              (cl-incf i)))
          (org-gtd-cli/output
           `((version . 1)
             (command . "search")
             (tasks . ,(apply #'vector (nreverse tasks)))
             (count . ,(length matches)))))
      ;; Text mode
      (if (null matches)
          (org-gtd-cli/error "No matches.")
        (let ((i 1))
          (dolist (m matches)
            (princ (format "[%d] %s %s (%s)\n"
                           i (nth 0 m) (nth 1 m) (nth 2 m)))
            (when (and org-gtd-cli/full-mode (nth 6 m))
              (princ (concat "    " (replace-regexp-in-string
                                     "\n" "\n    " (nth 6 m))
                             "\n\n")))
            (cl-incf i))))))
  (kill-emacs 0))

;; --- show ---

(defun org-gtd-cli/show (substring &optional index plain)
  "Show full content of a task.
When PLAIN is non-nil, show only the heading hierarchy with TODO
state and priority — no tags, body, drawers, or planning lines."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-plain (and plain (not (equal plain "nil"))
                        (not (string-empty-p plain))))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (if org-gtd-cli/json-mode
           (org-gtd-cli/show-json)
         (let* ((file (buffer-file-name))
                (rel-file (org-gtd-cli/relative-filename file)))
           (princ (format "(%s)\n" rel-file))
           (if is-plain
               (let* ((base-level (org-current-level))
                      (subtree-end (save-excursion (org-end-of-subtree t) (point))))
                 (while (< (point) subtree-end)
                   (let* ((level (org-current-level))
                          (indent (make-string (* 2 (- level base-level)) ?\s))
                          (heading (org-get-heading t nil nil t)))
                     (princ (format "%s%s\n" indent heading)))
                   (unless (outline-next-heading)
                     (goto-char subtree-end))))
             (let* ((beg (point))
                    (end (save-excursion (org-end-of-subtree t) (point)))
                    ;; Strip LOGBOOK drawers (state-change history) — noise in a
                    ;; human-readable dump, and JSON `show' already omits them.
                    (content (org-gtd-cli/strip-logbook
                              (buffer-substring-no-properties beg end))))
               (princ content)
               (princ "\n"))))))))
  (kill-emacs 0))

(defconst org-gtd-cli/properties-exclude '("CATEGORY")
  "Standard properties to omit from the generic `properties' JSON field.
`org-entry-properties' with `standard' auto-injects CATEGORY (a derived
value, not part of the user's :PROPERTIES: drawer), so we drop it. Special
properties (TODO/PRIORITY/TAGS/SCHEDULED/DEADLINE/...) are already surfaced
as first-class fields and never appear among `standard' properties.")

(defun org-gtd-cli/properties-at-point ()
  "Return the heading's :PROPERTIES: drawer as a JSON object (hash-table).
Keys are property names (strings), values their string contents. Excludes
`org-gtd-cli/properties-exclude'. An entry with no user properties yields an
empty object (`{}'), not null. Serializing a hash-table (rather than an
alist) both avoids the symbol-key requirement of `json-serialize' and gives
a clean `{}' for the empty case. Not inherited — only the entry's own drawer."
  (let ((h (make-hash-table :test 'equal)))
    (dolist (pair (org-entry-properties nil 'standard))
      (unless (member (car pair) org-gtd-cli/properties-exclude)
        (puthash (car pair) (cdr pair) h)))
    h))

(defun org-gtd-cli/task-alist-at-point ()
  "Build a full task alist for the heading at point.
Returns an alist with the same schema as show --json output,
minus the version/command wrapper."
  (let* ((heading (org-get-heading t t t t))
         (state (org-get-todo-state))
         (priority-char (org-gtd-cli/get-explicit-priority))
         (tags (org-get-tags))
         (id (org-entry-get nil "ID"))
         (file (buffer-file-name))
         (rel-file (org-gtd-cli/relative-filename file))
         (scheduled (org-entry-get nil "SCHEDULED"))
         (deadline (org-entry-get nil "DEADLINE"))
         (parent-heading (save-excursion
                           (if (org-up-heading-safe)
                               (org-get-heading t t t t)
                             nil)))
         (level (org-current-level))
         (child-level (1+ level))
         (subtree-end (save-excursion (org-end-of-subtree t) (point)))
         (body (org-gtd-cli/get-body-at-point))
         (sessions (org-gtd-cli/parse-session-entries))
         ;; Collect subtasks
         (children '())
         (done-count 0)
         (total-count 0))
    (save-excursion
      (forward-line 1)
      (while (and (< (point) subtree-end)
                  (re-search-forward org-heading-regexp subtree-end t))
        (when (= (org-current-level) child-level)
          (let* ((child-state (org-get-todo-state))
                 (child-heading (org-get-heading t t t t))
                 (child-priority (org-gtd-cli/get-explicit-priority))
                 (child-tags (org-get-tags))
                 (child-id (org-entry-get nil "ID"))
                 (child-scheduled (org-entry-get nil "SCHEDULED"))
                 (child-deadline (org-entry-get nil "DEADLINE"))
                 (child-is-project (org-gtd-cli/has-todo-children-p)))
            (when child-state
              (cl-incf total-count)
              (when (member child-state org-done-keywords)
                (cl-incf done-count)))
            (push `((heading . ,child-heading)
                    (state . ,(or child-state :null))
                    (priority . ,(or child-priority :null))
                    (tags . ,(vconcat (mapcar #'identity child-tags)))
                    (id . ,(or child-id :null))
                    (scheduled . ,(or child-scheduled :null))
                    (deadline . ,(or child-deadline :null))
                    (is_project . ,(if child-is-project t :false)))
                  children)))))
    (let ((is-project (> total-count 0)))
      `((heading . ,heading)
        (state . ,(or state :null))
        (priority . ,(or priority-char :null))
        (tags . ,(vconcat (mapcar #'identity tags)))
        (id . ,(or id :null))
        (file . ,rel-file)
        (scheduled . ,(or scheduled :null))
        (deadline . ,(or deadline :null))
        (parent . ,(or parent-heading :null))
        (is_project . ,(if is-project t :false))
        (properties . ,(org-gtd-cli/properties-at-point))
        (body . ,(or body :null))
        (sessions . ,(apply #'vector sessions))
        (subtasks . ,(apply #'vector (nreverse children)))
        (progress . ,(if is-project
                         `((done . ,done-count) (total . ,total-count))
                       :null))))))

(defun org-gtd-cli/show-json ()
  "Output JSON for the show command from point."
  (org-gtd-cli/output
   (append `((version . 1) (command . "show"))
           (org-gtd-cli/task-alist-at-point))))

(defun org-gtd-cli/mutation-output (alist heading-or-buf-pos)
  "Output ALIST as JSON mutation response, enriched with full task state.
HEADING-OR-BUF-POS is either a heading string (used to re-find the
task after mutations that may reorder entries) or a (buffer . position)
cons.  Pass nil to skip the task field.  In JSON mode, adds a `task'
field with the full task state (same schema as show --json)."
  (when org-gtd-cli/json-mode
    (let ((task-data
           (cond
            ((null heading-or-buf-pos) nil)
            ((stringp heading-or-buf-pos)
             ;; Re-find by heading text (handles reordering).  Use exact
             ;; matching, and intercept `kill-emacs' (which `find-task'
             ;; calls on no-match/ambiguity and `condition-case' does NOT
             ;; catch) so a failed re-find degrades to omitting the task
             ;; field — the mutation is already saved at this point, so
             ;; exiting with an error here would misreport success as
             ;; failure.  `message'/`standard-output' are silenced so
             ;; find-task's error JSON doesn't pollute the real output.
             (condition-case nil
                 (catch 'org-gtd-cli--refind-failed
                   (cl-letf (((symbol-function 'kill-emacs)
                              (lambda (&optional _code)
                                (throw 'org-gtd-cli--refind-failed nil)))
                             ((symbol-function 'message) #'ignore)
                             (standard-output #'ignore))
                     (let ((bp (org-gtd-cli/find-task heading-or-buf-pos nil t t)))
                       (with-current-buffer (car bp)
                         (org-with-wide-buffer
                          (goto-char (cdr bp))
                          (org-gtd-cli/task-alist-at-point))))))
               (error nil)))
            (t
             ;; Direct buf-pos
             (with-current-buffer (car heading-or-buf-pos)
               (org-with-wide-buffer
                (goto-char (cdr heading-or-buf-pos))
                (org-gtd-cli/task-alist-at-point)))))))
      (org-gtd-cli/output
       (if task-data
           (append alist `((task . ,task-data)))
         alist)))))

;; --- subtasks ---

(defun org-gtd-cli/subtasks (substring &optional index)
  "List subtasks of a project."
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (file (buffer-file-name))
              (rel-file (org-gtd-cli/relative-filename file))
              (level (org-current-level))
              (child-level (1+ level))
              (subtree-end (save-excursion (org-end-of-subtree t) (point)))
              (children '())
              (done-count 0)
              (total-count 0))
         (save-excursion
           (forward-line 1)
           (while (and (< (point) subtree-end)
                       (re-search-forward org-heading-regexp subtree-end t))
             (when (= (org-current-level) child-level)
               (let* ((child-state (org-get-todo-state))
                      (child-heading (org-get-heading t t t t))
                      (child-scheduled (org-entry-get nil "SCHEDULED"))
                      (child-deadline (org-entry-get nil "DEADLINE"))
                      (child-priority (org-gtd-cli/get-explicit-priority))
                      (child-tags (org-get-tags))
                      (child-id (org-entry-get nil "ID"))
                      (child-is-project (org-gtd-cli/has-todo-children-p)))
                 (cl-incf total-count)
                 (when (and child-state (member child-state org-done-keywords))
                   (cl-incf done-count))
                 (let ((child-body (when org-gtd-cli/full-mode
                                     (org-gtd-cli/get-body-at-point))))
                   (push (list (or child-state "")
                               child-heading
                               child-scheduled
                               child-deadline
                               child-priority
                               child-tags
                               child-is-project
                               child-body
                               child-id)
                         children))))))
         (if (= total-count 0)
             (progn
               (org-gtd-cli/error "Task \"%s\" has no subtasks" heading)
               (kill-emacs 1))
           (if org-gtd-cli/json-mode
               (org-gtd-cli/output-subtasks-json
                heading (org-get-todo-state) rel-file
                (save-excursion
                  (if (org-up-heading-safe)
                      (org-get-heading t t t t)
                    nil))
                children done-count total-count
                (org-entry-get nil "ID"))
             (princ (format "Project: %s (%s)\n" heading rel-file))
             (dolist (child (nreverse children))
               (let ((line-str (concat "  " (nth 0 child) " " (nth 1 child)
                                       " (" rel-file ")")))
                 (when (and (nth 4 child) (not (string= (nth 4 child) "B")))
                   (setq line-str (concat line-str " [#" (nth 4 child) "]")))
                 (when (nth 3 child)
                   (setq line-str (concat line-str "  D:" (nth 3 child))))
                 (when (nth 2 child)
                   (setq line-str (concat line-str "  S:" (nth 2 child))))
                 (princ (concat line-str "\n"))
                 (when (and org-gtd-cli/full-mode (nth 7 child))
                   (princ (concat "      " (replace-regexp-in-string
                                            "\n" "\n      " (nth 7 child))
                                  "\n\n")))))
             (princ (format "\nProgress: %d/%d done\n" done-count total-count))))))))
  (kill-emacs 0))

(defun org-gtd-cli/output-subtasks-json (heading state rel-file parent children done-count total-count &optional id)
  "Output JSON for the subtasks command.
ID is the parent heading's own org :ID: (or nil)."
  (let ((subtask-list '()))
    (dolist (child (nreverse children))
      (let ((subtask `((heading . ,(nth 1 child))
                       (state . ,(or (nth 0 child) :null))
                       (priority . ,(let ((p (nth 4 child)))
                                      (if (and p (not (string-empty-p p))) p :null)))
                       (tags . ,(vconcat (mapcar #'identity (nth 5 child))))
                       (id . ,(or (nth 8 child) :null))
                       (scheduled . ,(or (nth 2 child) :null))
                       (deadline . ,(or (nth 3 child) :null))
                       (is_project . ,(if (nth 6 child) t :false)))))
        (when org-gtd-cli/full-mode
          (setq subtask (append subtask `((body . ,(or (nth 7 child) :null))))))
        (push subtask subtask-list)))
    (org-gtd-cli/output
     `((version . 1)
       (command . "subtasks")
       (heading . ,heading)
       (state . ,(or state :null))
       (id . ,(or id :null))
       (file . ,rel-file)
       (parent . ,(or parent :null))
       (progress . ((done . ,done-count) (total . ,total-count)))
       (subtasks . ,(apply #'vector (nreverse subtask-list)))))))

;; --- categories ---

(defun org-gtd-cli/categories (&optional file-name)
  "Show the category tree for an org file.
Displays plain (non-TODO) headings as full paths, stopping at the
first TODO heading in each branch. Useful for finding refile targets.
FILE-NAME defaults to \"tasks.org\".

In JSON mode each `categories' element is an object with `path' (the
full slash-separated heading path) and `heading' (the leaf heading),
so callers can walk `.categories[].heading'. The text output is one
line per category: \"<path> (<file>)\"."
  (let* ((target (or (and file-name
                          (not (equal file-name "nil"))
                          (not (string-empty-p file-name))
                          file-name)
                     "tasks.org"))
         (file (expand-file-name target org-directory))
         (found nil))
    (unless (file-exists-p file)
      (org-gtd-cli/error "File not found: %s" target)
      (kill-emacs 1))
    (let ((categories '()))
      (with-current-buffer (find-file-noselect file)
        (org-with-wide-buffer
         (goto-char (point-min))
         (while (re-search-forward org-heading-regexp nil t)
           (let ((state (org-get-todo-state)))
             (unless state
               (setq found t)
               (push `((path . ,(org-gtd-cli/heading-path-at-point))
                       (heading . ,(org-get-heading t t t t)))
                     categories))))))
      (if org-gtd-cli/json-mode
          (org-gtd-cli/output
           `((version . 1)
             (command . "categories")
             (file . ,(if found
                          (org-gtd-cli/relative-filename file)
                        target))
             (categories . ,(apply #'vector (nreverse categories)))))
        (if found
            (let ((rel-file (org-gtd-cli/relative-filename file)))
              (dolist (cat (nreverse categories))
                (princ (format "%s (%s)\n" (alist-get 'path cat) rel-file))))
          (org-gtd-cli/error "No categories found")))))
  (kill-emacs 0))

;; --- projects ---

(defun org-gtd-cli/projects ()
  "List all active projects with category paths and progress counts.
An active project is a heading with a non-done TODO keyword that has
at least one direct child with a TODO keyword."
  (let ((results '()))
    (dolist (file (org-agenda-files))
      (when (file-exists-p file)
        (with-current-buffer (find-file-noselect file)
          (org-with-wide-buffer
           (let ((rel-file (org-gtd-cli/relative-filename file)))
             (goto-char (point-min))
             (while (re-search-forward org-heading-regexp nil t)
               (let ((state (org-get-todo-state)))
                 (when (and state
                            (member state org-todo-keywords-1)
                            (not (member state org-done-keywords)))
                   (let* ((level (org-current-level))
                          (child-level (1+ level))
                          (subtree-end (save-excursion (org-end-of-subtree t) (point)))
                          (done-count 0)
                          (total-count 0))
                     (save-excursion
                       (forward-line 1)
                       (while (and (< (point) subtree-end)
                                   (re-search-forward org-heading-regexp subtree-end t))
                         (when (= (org-current-level) child-level)
                           (let ((child-state (org-get-todo-state)))
                             (when (and child-state
                                        (member child-state org-todo-keywords-1))
                               (cl-incf total-count)
                               (when (member child-state org-done-keywords)
                                 (cl-incf done-count)))))))
                     (when (> total-count 0)
                       (let ((heading (org-get-heading t t t t))
                             (path (org-gtd-cli/heading-path-at-point))
                             (tags (org-get-tags))
                             (id (org-entry-get nil "ID"))
                             (parent (save-excursion
                                       (if (org-up-heading-safe)
                                           (org-get-heading t t t t)
                                         nil))))
                         (push (list heading path state tags
                                     rel-file parent done-count total-count id)
                               results))))))))))))
    (setq results (nreverse results))
    (if org-gtd-cli/json-mode
        (org-gtd-cli/output-projects-json results)
      (if results
          (dolist (r results)
            (princ (format "%s (%s) [%d/%d]\n"
                           (nth 1 r) (nth 4 r) (nth 6 r) (nth 7 r))))
        (org-gtd-cli/error "No projects."))))
  (kill-emacs 0))

(defun org-gtd-cli/output-projects-json (results)
  "Output RESULTS as JSON for the projects command."
  (let ((projects '()))
    (dolist (r results)
      (push `((heading . ,(nth 0 r))
              (path . ,(nth 1 r))
              (state . ,(nth 2 r))
              (tags . ,(vconcat (mapcar #'identity (nth 3 r))))
              (id . ,(or (nth 8 r) :null))
              (file . ,(nth 4 r))
              (parent . ,(or (nth 5 r) :null))
              (progress . ((done . ,(nth 6 r)) (total . ,(nth 7 r)))))
            projects))
    (org-gtd-cli/output
     `((version . 1)
       (command . "projects")
       (projects . ,(apply #'vector (nreverse projects)))
       (count . ,(length results))))))

;; --- outline ---

(defun org-gtd-cli/outline-node-at-point ()
  "Build the per-heading node alist for the outline at point (sans children).
Point must be on a heading line in a widened org buffer.  Tags are
INHERITED (`org-get-tags', NOT no-inherit) per the dashboard JSON
convention.  The node's own raw org `body' (via
`org-gtd-cli/get-body-at-point', matching the null/trim conventions of
`show' and `subtasks --full') is emitted only when `org-gtd-cli/full-mode'
is non-nil.  Calendar events — state-less headings whose own body carries
a plain active timestamp (`<...>', excluding SCHEDULED:/DEADLINE: planning
lines, which `get-body-at-point' already drops) — are typed with
`is_event' true and `is_category' false.  The first active timestamp of
ANY heading's own body is surfaced as `timestamp' (null when none),
regardless of full-mode."
  (let* ((heading (org-get-heading t t t t))
         (level (org-current-level))
         (state (org-get-todo-state))
         (priority (org-gtd-cli/get-explicit-priority))
         (tags (org-get-tags))
         (id (org-entry-get nil "ID"))
         (body (org-gtd-cli/get-body-at-point))
         (timestamp
          (and body
               (string-match "<[0-9]\\{4\\}-[0-9]\\{2\\}-[0-9]\\{2\\}[^>\n]*>" body)
               (match-string 0 body)))
         (stateless (null state))
         (is-event (and stateless timestamp t))
         (is-category (and stateless (not is-event)))
         (is-project (and state (org-gtd-cli/has-todo-children-p)))
         (progress
          (if is-project
              (let ((child-level (1+ level))
                    (subtree-end (save-excursion (org-end-of-subtree t) (point)))
                    (done-count 0)
                    (total-count 0))
                (save-excursion
                  (forward-line 1)
                  (while (and (< (point) subtree-end)
                              (re-search-forward org-heading-regexp subtree-end t))
                    (when (= (org-current-level) child-level)
                      (let ((child-state (org-get-todo-state)))
                        (when (and child-state
                                   (member child-state org-todo-keywords-1))
                          (cl-incf total-count)
                          (when (member child-state org-done-keywords)
                            (cl-incf done-count)))))))
                `((done . ,done-count) (total . ,total-count)))
            :null)))
    `((heading . ,heading)
      (level . ,level)
      (todo_state . ,(or state :null))
      (priority . ,(or priority :null))
      (tags . ,(vconcat (mapcar #'identity tags)))
      (is_category . ,(if is-category t :false))
      (is_event . ,(if is-event t :false))
      (is_project . ,(if is-project t :false))
      (progress . ,progress)
      (id . ,(or id :null))
      (timestamp . ,(or timestamp :null))
      (body . ,(if org-gtd-cli/full-mode (or body :null) :null)))))

(defun org-gtd-cli/outline-tree (file)
  "Return the nested outline of FILE as a vector of top-level node alists.
Walks the widened buffer once, maintaining a stack of open ancestors
keyed by level.  Each heading's children are accumulated in document
order, then finalized into a (children . [..]) field appended to the
node alist.  A `record' here is the cons (DATA . REVERSED-CHILD-RECORDS)."
  (with-current-buffer (find-file-noselect file)
    (org-with-wide-buffer
     (let ((roots '())        ; reversed list of top-level records
           (stack '()))       ; list of (level . record), innermost first
       (goto-char (point-min))
       (while (re-search-forward org-heading-regexp nil t)
         (goto-char (line-beginning-position))
         (let* ((level (org-current-level))
                (record (cons (org-gtd-cli/outline-node-at-point) '())))
           ;; Pop ancestors that are not shallower than this heading.
           (while (and stack (>= (car (car stack)) level))
             (setq stack (cdr stack)))
           (if stack
               ;; Attach to nearest open ancestor (prepend; reversed later).
               (let ((parent (cdr (car stack))))
                 (setcdr parent (cons record (cdr parent))))
             (push record roots))
           (push (cons level record) stack))
         (forward-line 1))
       (apply #'vector
              (mapcar #'org-gtd-cli/outline-finalize (nreverse roots)))))))

(defun org-gtd-cli/outline-finalize (record)
  "Convert a tree RECORD (DATA . REVERSED-CHILD-RECORDS) to its node alist."
  (let ((data (car record))
        (children (nreverse (cdr record))))
    (append data
            `((children . ,(apply #'vector
                                  (mapcar #'org-gtd-cli/outline-finalize
                                          children)))))))

(defun org-gtd-cli/outline (&optional file-name)
  "Emit the full nested outline of an org file as JSON.
Interleaves category headings (plain, no TODO keyword), calendar events
(state-less headings whose body carries a plain active timestamp), and
tasks.  FILE-NAME defaults to \"tasks.org\"; resolved relative to
`org-directory'.  This is a read — it never creates org ids.  Honors
`org-gtd-cli/full-mode' (emits each node's own raw org `body').  In text
mode, prints a minimal indented heading tree."
  (let* ((target (or (and file-name
                          (not (equal file-name "nil"))
                          (not (string-empty-p file-name))
                          file-name)
                     "tasks.org"))
         (file (expand-file-name target org-directory)))
    (unless (file-exists-p file)
      (org-gtd-cli/error "File not found: %s" target)
      (kill-emacs 1))
    (let ((nodes (org-gtd-cli/outline-tree file)))
      (if org-gtd-cli/json-mode
          (org-gtd-cli/output
           `((version . 1)
             (command . "outline")
             (file . ,(org-gtd-cli/relative-filename file))
             (nodes . ,nodes)))
        (org-gtd-cli/outline-print-text nodes))))
  (kill-emacs 0))

(defun org-gtd-cli/outline-print-text (nodes)
  "Print NODES (a vector of node alists) as an indented heading tree."
  (mapc
   (lambda (node)
     (let ((level (alist-get 'level node))
           (heading (alist-get 'heading node))
           (state (alist-get 'todo_state node)))
       (princ (format "%s%s%s\n"
                      (make-string (* 2 (1- level)) ?\s)
                      (if (stringp state) (concat state " ") "")
                      heading)))
     (org-gtd-cli/outline-print-text (alist-get 'children node)))
   nodes))

;; ══════════════════════════════════════════════════════════════════════════════
;; Shared org→HTML render helper + path-scoped render-file
;; ══════════════════════════════════════════════════════════════════════════════

(declare-function org-html-link "ox-html" (link desc info))
(declare-function org-export-create-backend "ox" (&rest rest))
(declare-function org-export-string-as "ox"
                  (string backend &optional body-only ext-plist))

;; Declared special (bodyless defvar) so the byte-compiler binds them
;; DYNAMICALLY in `org-gtd-cli/render-org-string' below.  They are defined in
;; ox-html/ox, which are not loaded when this file is compiled; without these
;; forward declarations a compiled `let'/`let*' would create a useless lexical
;; local and the export would run with the global htmlize output type instead
;; of the `css' face classes the dashboard needs.
(defvar org-html-htmlize-output-type)
(defvar org-export-with-broken-links)

(defvar org-gtd-cli/render--links nil
  "Dynamic accumulator of link metadata during `org-gtd-cli/render-org-string'.
Bound freshly per render.  Each entry is a JSON-ready alist with keys
`index' (0-based, mapping to the matching DOM anchor's
`data-org-link-index'), `type', `raw', and `text'.")

(defun org-gtd-cli/attr-escape (s)
  "Escape S for safe interpolation inside a double-quoted HTML attribute."
  (setq s (replace-regexp-in-string "&" "&amp;" s t t))
  (setq s (replace-regexp-in-string "<" "&lt;" s t t))
  (setq s (replace-regexp-in-string ">" "&gt;" s t t))
  (setq s (replace-regexp-in-string "\"" "&quot;" s t t))
  s)

(defun org-gtd-cli/htmlize-available-p ()
  "Return non-nil if the `htmlize' package can be loaded.
Used to decide whether src blocks can be fontified with `css' face
classes; when nil the exporter degrades to a plain <pre> (no crash)."
  (and (locate-library "htmlize")
       (require 'htmlize nil t)
       t))

(defun org-gtd-cli/html-link (link desc info)
  "Transcode a LINK to HTML, tagging the anchor with its raw org target.
DESC is the transcoded description, INFO the export communication
channel.  `ox-html' mangles hrefs (file:x.org -> x.html), so the client
must never route off `href': every emitted <a> is stamped with
`data-org-link-index', `data-org-link-type' and `data-org-link-raw' (the
original org target, e.g. \"file:x.org::*Heading\", with the ::search
suffix preserved), and the same record is pushed onto
`org-gtd-cli/render--links'.  Broken or unresolvable id:/fuzzy/search
links (whose targets do not exist
in this standalone export) would otherwise abort the export or be dropped
by `ox''s broken-link handling before this transcoder finishes; catching
any resolution error here keeps them as tagged anchors (using the raw org
target as a placeholder href) so the client's in-app link resolution still
works.  Resolution has no side effects on failure (e.g. an id: lookup with
`org-id-track-globally' off errors immediately without scanning)."
  (let* ((type (org-element-property :type link))
         (raw (org-element-property :raw-link link))
         (index (length org-gtd-cli/render--links))
         (html (condition-case nil
                   (org-html-link link desc info)
                 (error
                  (format "<a href=\"%s\">%s</a>"
                          (org-gtd-cli/attr-escape (or raw ""))
                          (or desc (org-gtd-cli/attr-escape (or raw ""))))))))
    (push `((index . ,index)
            (type . ,(or type :null))
            (raw . ,(or raw :null))
            (text . ,(if desc (substring-no-properties desc) :null)))
          org-gtd-cli/render--links)
    (if (and (stringp html) (string-match "<a " html))
        (replace-match
         (format (concat "<a data-org-link-index=\"%d\""
                         " data-org-link-type=\"%s\""
                         " data-org-link-raw=\"%s\" ")
                 index
                 (org-gtd-cli/attr-escape (or type ""))
                 (org-gtd-cli/attr-escape (or raw "")))
         t t html)
      html)))

(defun org-gtd-cli/render-org-string (org-string)
  "Render ORG-STRING to body-only HTML with per-link metadata.
Returns a plist (:html HTML-STRING :links LINKS-VECTOR):
- HTML is a body-only `ox-html' export (no <head>/<html> wrapper, TOC and
  section numbers off).  Src blocks are fontified with `css' face classes
  (org-keyword, org-string, …) when `htmlize' is available, degrading to a
  plain <pre> otherwise.
- LINKS is a vector of JSON-ready alists (index/type/raw/text), one per
  link in document order; each entry's `index' matches the
  `data-org-link-index' on the corresponding <a> so a client can recover
  every link's original org target without parsing `href' (see
  `org-gtd-cli/html-link').
This is the shared helper behind `render-file'; it never touches the file
system or GTD semantics."
  (require 'ox-html)
  (require 'org-id)
  (let* ((org-gtd-cli/render--links nil)
         (org-html-htmlize-output-type
          (if (org-gtd-cli/htmlize-available-p) 'css nil))
         (org-export-with-broken-links t)
         (backend (org-export-create-backend
                   :parent 'html
                   :transcoders '((link . org-gtd-cli/html-link))))
         ;; This CLI disables org-id's global registry (`org-id-track-globally'
         ;; nil, see top of file), which makes an unresolvable id: link's
         ;; `org-id-find' raise a hard `error' ("turn on org-id-track-globally")
         ;; that would abort the whole export.  Neutralize the registry rebuild
         ;; so it returns nil instead: the id: link then degrades to a broken
         ;; link, tagged as a raw anchor by `org-gtd-cli/html-link'.  No scan,
         ;; no side effect.
         (html (cl-letf (((symbol-function 'org-id-update-id-locations) #'ignore))
                 (org-export-string-as
                  (or org-string "") backend t
                  '(:with-toc nil :section-numbers nil :with-broken-links t)))))
    (list :html (string-trim html)
          :links (apply #'vector (nreverse org-gtd-cli/render--links)))))

(defun org-gtd-cli/render-file-reject (msg hint)
  "Emit a structured render-file error (MSG + HINT) and exit 1.
Mirrors the {error, hint} JSON shape used by `org-gtd-cli/find-task'."
  (if org-gtd-cli/json-mode
      (org-gtd-cli/output `((error . ,msg) (hint . ,hint)))
    (org-gtd-cli/error "%s" msg)
    (org-gtd-cli/error "Hint: %s" hint))
  (kill-emacs 1))

(defun org-gtd-cli/render-file (path)
  "Render the org file at PATH (under `org-directory') to body-only HTML.
PATH is resolved relative to `org-directory'; absolute paths are allowed
only when they canonicalize inside it.  This is the first command taking a
caller-supplied path, so it is path-scoped: after expanding and
`file-truename'-resolving both PATH and `org-directory', it REJECTS
\(structured error, exit 1) any PATH that
 (a) escapes `org-directory' after symlink resolution,
 (b) does not end in .org, or
 (c) does not exist as a regular file.
Whole-file rendering only (no subtree selectors).  In JSON mode emits
{version, command, file, body_html, links, content_hash}; `content_hash'
is \"sha256-<hex>\" over the raw source bytes (for the dashboard's
hash-caching).  In text mode prints the HTML."
  (let* ((org-dir-true (file-name-as-directory
                        (file-truename
                         (directory-file-name (expand-file-name org-directory)))))
         (raw-path (or path ""))
         (candidate (expand-file-name raw-path org-directory))
         (candidate-true (file-truename candidate)))
    ;; (b) must be a .org file (checked on the resolved name so a symlink to a
    ;; non-org target is rejected too).
    (unless (string-suffix-p ".org" candidate-true)
      (org-gtd-cli/render-file-reject
       (format "Not an org file: %s" raw-path)
       "render-file only renders files whose name ends in .org."))
    ;; (a) must stay inside org-directory after symlink resolution.  The
    ;; trailing slash on ORG-DIR-TRUE makes this a proper path-component
    ;; prefix (so a sibling like .../org-evil cannot match .../org).
    (unless (string-prefix-p org-dir-true candidate-true)
      (org-gtd-cli/render-file-reject
       (format "Path escapes org-directory: %s" raw-path)
       "render-file only reads files inside ORG_DIRECTORY."))
    ;; (c) must exist as a regular file.
    (unless (file-regular-p candidate-true)
      (org-gtd-cli/render-file-reject
       (format "File not found: %s" raw-path)
       "Check the path (it is resolved relative to ORG_DIRECTORY)."))
    ;; Read once, literally: hash the raw source bytes and decode the same
    ;; bytes as UTF-8 for the exporter.  `content_hash' must be sha256 of the
    ;; source bytes (matching a client's own hash of the file), not of Emacs's
    ;; internal multibyte representation.
    (let (hash org-text)
      (with-temp-buffer
        (set-buffer-multibyte nil)
        (insert-file-contents-literally candidate-true)
        (let ((bytes (buffer-string)))
          (setq hash (concat "sha256-" (secure-hash 'sha256 bytes)))
          (setq org-text (decode-coding-string bytes 'utf-8))))
      (let* ((rendered (org-gtd-cli/render-org-string org-text))
             (html (plist-get rendered :html))
             (links (plist-get rendered :links)))
        (if org-gtd-cli/json-mode
            (org-gtd-cli/output
             `((version . 1)
               (command . "render-file")
               (file . ,(file-relative-name candidate-true org-dir-true))
               (body_html . ,html)
               (links . ,links)
               (content_hash . ,hash)))
          (princ html)
          (princ "\n")))))
  (kill-emacs 0))

;; --- list-tags ---

(defun org-gtd-cli/list-tags ()
  "List every tag in use across the org files with usage counts.
Counts tags literally present on each headline — no inheritance —
so agents can see which tags exist (and where they are dominant)
before inventing new ones.  Includes every headline: TODO tasks in
any state (including DONE) and plain category/note headings."
  (let ((counts (make-hash-table :test #'equal)))
    (dolist (file (org-agenda-files))
      (when (file-exists-p file)
        (with-current-buffer (find-file-noselect file)
          (org-with-wide-buffer
           (goto-char (point-min))
           (while (re-search-forward org-heading-regexp nil t)
             (dolist (tag (org-get-tags nil t))
               (puthash tag (1+ (gethash tag counts 0)) counts)))))))
    (let ((results '()))
      (maphash (lambda (tag count) (push (cons tag count) results)) counts)
      ;; Sort by count descending, ties alphabetically
      (setq results (sort results
                          (lambda (a b)
                            (if (/= (cdr a) (cdr b))
                                (> (cdr a) (cdr b))
                              (string< (car a) (car b))))))
      (if org-gtd-cli/json-mode
          (org-gtd-cli/output
           `((version . 1)
             (command . "list-tags")
             (tags . ,(vconcat (mapcar (lambda (r)
                                         `((tag . ,(car r))
                                           (count . ,(cdr r))))
                                       results)))
             (count . ,(length results))))
        (if (null results)
            (org-gtd-cli/error "No tags found.")
          (dolist (r results)
            (princ (format "%4d %s\n" (cdr r) (car r))))))))
  (kill-emacs 0))

;; --- add-task ---

(defun org-gtd-cli/add-task (title &optional body tags-csv schedule deadline
                                    priority file category state time-str)
  "Add a TODO task."
  (let* ((target-file
          (cond
           ((and category (not (string-empty-p category))
                 (not (equal category "nil")))
            (concat org-directory "tasks.org"))
           ((and file (not (string-empty-p file))
                 (not (equal file "nil")))
            (concat org-directory file))
           (t (concat org-directory "inbox.org"))))
         (use-category (and category (not (string-empty-p category))
                            (not (equal category "nil"))))
         (todo-state (if (and state (not (string-empty-p state))
                              (not (equal state "nil")))
                         state "TODO"))
         ;; Normalize nil strings
         (body (when (and body (not (string-empty-p body))
                          (not (equal body "nil")))
                 body))
         (tags-csv (when (and tags-csv (not (string-empty-p tags-csv))
                              (not (equal tags-csv "nil")))
                     tags-csv))
         (schedule (when (and schedule (not (string-empty-p schedule))
                              (not (equal schedule "nil")))
                     schedule))
         (deadline (when (and deadline (not (string-empty-p deadline))
                              (not (equal deadline "nil")))
                     deadline))
         (time-str (when (and time-str (not (string-empty-p time-str))
                              (not (equal time-str "nil")))
                     time-str))
         (priority (when (and priority (not (string-empty-p priority))
                              (not (equal priority "nil")))
                     priority)))
    (when (and time-str (not schedule) (not deadline))
      (org-gtd-cli/error "Error: --time requires --schedule or --deadline")
      (kill-emacs 1))
    (when (and time-str schedule deadline)
      (org-gtd-cli/error
       "Error: --time with both --schedule and --deadline is ambiguous; set the second timestamp's time via set-schedule or set-deadline.")
      (kill-emacs 1))
    (when body (setq body (org-gtd-cli/fill-text body)))
    (org-gtd-cli/validate-body-text body)
    ;; `add-task' always files a freestanding task (inbox, a file, or under a
    ;; non-TODO category heading) — never inside a project's sibling list. NEXT
    ;; is project-internal, so reject it here rather than create an invalid
    ;; standalone NEXT. Use `add-subtask' + `set-next' to make a project's NEXT.
    (when (equal todo-state "NEXT")
      (org-gtd-cli/reject-next title))
    (unless (file-exists-p target-file)
      (org-gtd-cli/error "Error: file not found: %s" target-file)
      (kill-emacs 1))
    (let (matched-path)
      (with-current-buffer (find-file-noselect target-file)
        (org-with-wide-buffer
         (if use-category
             ;; Find the category heading and insert under it
             (let* ((cat-parts (split-string category "/" t))
                    (matches '()))
               (goto-char (point-min))
               (while (re-search-forward org-heading-regexp nil t)
                 (unless (org-get-todo-state)
                   (let ((heading (org-get-heading t t t t)))
                     (if (= (length cat-parts) 1)
                         ;; Single segment: substring match
                         (when (string-match-p (regexp-quote (car cat-parts)) heading)
                           (push (list (point) (org-current-level)
                                       (org-gtd-cli/heading-path-at-point))
                                 matches))
                       ;; Multi-segment path: match last segment, verify ancestors
                       (when (string-match-p (regexp-quote (car (last cat-parts))) heading)
                         (let ((path-match t)
                               (parts (butlast cat-parts)))
                           (save-excursion
                             (dolist (part (reverse parts))
                               (unless (and (org-up-heading-safe)
                                            (string-match-p (regexp-quote part)
                                                            (org-get-heading t t t t)))
                                 (setq path-match nil))))
                           (when path-match
                             (push (list (point) (org-current-level)
                                         (org-gtd-cli/heading-path-at-point))
                                   matches))))))))
               (setq matches (nreverse matches))
               (cond
                ((null matches)
                 (org-gtd-cli/error "Error: category heading \"%s\" not found in %s"
                                    category (org-gtd-cli/relative-filename target-file))
                 (kill-emacs 1))
                ((> (length matches) 1)
                 (org-gtd-cli/error "Multiple category matches for \"%s\":" category)
                 (let ((idx 1))
                   (dolist (m matches)
                     (org-gtd-cli/error "[%d] %s (%s)"
                                        idx (nth 2 m)
                                        (org-gtd-cli/relative-filename target-file))
                     (cl-incf idx)))
                 (org-gtd-cli/error "Use a more specific path (e.g. --category \"Parent/Child\").")
                 (kill-emacs 2)))
               ;; Single match — go to it and insert
               (let ((match (car matches)))
                 (setq matched-path (nth 2 match))
                 (goto-char (car match))
                 (let ((target-level (1+ (nth 1 match))))
                   (org-end-of-subtree t)
                   (unless (bolp) (insert "\n"))
                   (insert (org-gtd-cli/build-entry
                            target-level todo-state title
                            priority tags-csv schedule deadline body
                            (when schedule time-str)
                            (when deadline time-str))
                           "\n")
                   ;; Remove orphaned blank lines at insertion point
                   (while (and (not (eobp)) (looking-at-p "\n"))
                     (delete-char 1)))))
           ;; Append to end of file
           (goto-char (point-max))
           (unless (bolp) (insert "\n"))
           (insert "\n" (org-gtd-cli/build-entry
                         1 todo-state title
                         priority tags-csv schedule deadline body
                         (when schedule time-str)
                         (when deadline time-str)))
           (insert "\n")))
        (save-buffer))
      (if org-gtd-cli/json-mode
          (org-gtd-cli/output
           `((version . 1) (command . "add-task")
             (heading . ,title) (state . ,todo-state)
             (file . ,(org-gtd-cli/relative-filename target-file))
             (category . ,(or matched-path :null))))
        (let ((display-target
               (if use-category
                   (format "%s/%s"
                           (org-gtd-cli/relative-filename target-file)
                           matched-path)
                 (org-gtd-cli/relative-filename target-file))))
          (princ (format "Added: %s -> %s (%s)\n" title display-target
                         (org-gtd-cli/relative-filename target-file)))))))
  (kill-emacs 0))

;; --- add-subtask ---

(defun org-gtd-cli/add-subtask (parent-substring title &optional body tags-csv
                                                  schedule deadline priority
                                                  state index)
  "Add a subtask under an existing task."
  (let* ((idx (org-gtd-cli/parse-index index))
         (todo-state (if (and state (not (string-empty-p state))
                              (not (equal state "nil")))
                         state "TODO"))
         (body (when (and body (not (string-empty-p body))
                          (not (equal body "nil")))
                 body))
         (tags-csv (when (and tags-csv (not (string-empty-p tags-csv))
                              (not (equal tags-csv "nil")))
                     tags-csv))
         (schedule (when (and schedule (not (string-empty-p schedule))
                              (not (equal schedule "nil")))
                     schedule))
         (deadline (when (and deadline (not (string-empty-p deadline))
                              (not (equal deadline "nil")))
                     deadline))
         (priority (when (and priority (not (string-empty-p priority))
                              (not (equal priority "nil")))
                     priority))
         (buf-pos (org-gtd-cli/find-task parent-substring idx t)))
    (when body (setq body (org-gtd-cli/fill-text body)))
    (org-gtd-cli/validate-body-text body)
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((parent-heading (org-get-heading t t t t))
              (parent-level (org-current-level))
              (child-level (1+ parent-level))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (parent-was-next (string= (org-get-todo-state) "NEXT")))
         ;; Demote NEXT parent to TODO (a NEXT task becoming a project should be TODO)
         (when parent-was-next
           (let ((org-inhibit-logging nil))
             (org-todo "TODO")))
         ;; Go to end of subtree
         (org-end-of-subtree t)
         (unless (bolp) (insert "\n"))
         (let ((child-pos (point)))
           (insert (org-gtd-cli/build-entry
                    child-level todo-state title
                    priority tags-csv schedule deadline body)
                   "\n")
           (save-excursion
             (goto-char child-pos)
             (org-id-get-create))
         ;; Remove orphaned blank lines at insertion point
           (while (and (not (eobp)) (looking-at-p "\n"))
             (delete-char 1))
           ;; Reorder siblings by state when the created state sorts above
           ;; plain TODO (NEXT or any done keyword). WAITING/DEFER/TODO
           ;; must preserve position (WAITING invariant, see 3f0802b).
           (when (or (string= todo-state "NEXT")
                     (member todo-state org-done-keywords))
             (save-excursion
               (goto-char child-pos)
               (org-gtd-cli/reorder-siblings-by-state)))
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1) (command . "add-subtask")
                  (heading . ,title) (state . ,todo-state)
                  (file . ,rel-file) (parent . ,parent-heading)
                  (side_effects . ,(if parent-was-next
                                       (vector `((action . "state-change")
                                                 (heading . ,parent-heading)
                                                 (old_state . "NEXT")
                                                 (new_state . "TODO")))
                                     [])))
                title)
             (princ (format "Added subtask: \"%s\" under \"%s\" (%s)\n"
                            title parent-heading rel-file))))))))
  (kill-emacs 0))

;; --- add-event ---

(defun org-gtd-cli/file-calendar-id ()
  "Return the file-level \"#+PROPERTY: calendar-id <value>\" in the current buffer.
Returns nil when the keyword is absent.  Scans the buffer text directly
rather than going through org's keyword cache
(`org-set-regexps-and-options' runs once at mode init), so the lookup
always reflects the buffer's current contents."
  (org-with-wide-buffer
   (goto-char (point-min))
   (let ((case-fold-search t))
     (when (re-search-forward
            "^#\\+PROPERTY:[ \t]+calendar-id[ \t]+\\(.+?\\)[ \t]*$" nil t)
       (match-string-no-properties 1)))))

(defun org-gtd-cli/add-event (title date &optional time tag file end-date)
  "Add a calendar event.  When END-DATE is given, create a date range."
  (let* ((target-file (if (and file (not (string-empty-p file))
                               (not (equal file "nil")))
                          (concat org-directory file)
                        (concat org-directory "calendar.org")))
         (cal-tag (cond
                   ((and tag (not (string-empty-p tag))
                         (not (equal tag "nil")))
                    tag)
                   ((not (and file (not (string-empty-p file))
                              (not (equal file "nil"))))
                    "calpersonal")
                   (t nil)))
         (time-str (when (and time (not (string-empty-p time))
                              (not (equal time "nil")))
                     time))
         (timestamp (if (and end-date (not (string-empty-p end-date))
                              (not (equal end-date "nil")))
                        (concat (org-gtd-cli/make-timestamp date time-str t)
                                "--"
                                (org-gtd-cli/make-timestamp end-date nil t))
                      (org-gtd-cli/make-timestamp date time-str t)))
         (gcal-calendar-id nil))
    (unless (file-exists-p target-file)
      (org-gtd-cli/error "Error: file not found: %s" target-file)
      (kill-emacs 1))
    (with-current-buffer (find-file-noselect target-file)
      ;; A file-level "#+PROPERTY: calendar-id <id>" in the target file
      ;; opts it into org-gcal sync format; without it the event is a
      ;; plain heading + timestamp.
      (setq gcal-calendar-id (org-gtd-cli/file-calendar-id))
      (org-with-wide-buffer
       (goto-char (point-max))
       (unless (bolp) (insert "\n"))
       (if gcal-calendar-id
           (insert (format "\n* %s%s\n:PROPERTIES:\n:calendar-id: %s\n:END:\n:org-gcal:\n%s\n:END:\n"
                           title
                           (if cal-tag (format " :%s:" cal-tag) "")
                           gcal-calendar-id
                           timestamp))
         (insert (format "\n* %s%s\n%s\n"
                         title
                         (if cal-tag (format " :%s:" cal-tag) "")
                         timestamp))))
      (save-buffer))
    (if org-gtd-cli/json-mode
        (let ((end-date-val (when (and end-date (not (string-empty-p end-date))
                                       (not (equal end-date "nil")))
                              end-date)))
          (org-gtd-cli/output
           `((version . 1) (command . "add-event")
             (heading . ,title)
             (file . ,(org-gtd-cli/relative-filename target-file))
             (date . ,date)
             (time . ,(or time-str :null))
             (end_date . ,(or end-date-val :null))
             (tag . ,(or cal-tag :null))
             (calendar_id . ,(or gcal-calendar-id :null)))))
      (princ (format "Added event: %s -> %s\n"
                     title (org-gtd-cli/relative-filename target-file)))))
  (kill-emacs 0))

;; --- add-note ---

(defun org-gtd-cli/add-note (title &optional link-task tags-csv sections-csv)
  "Create an org note file with metadata."
  (let* ((slug (org-gtd-cli/slugify title))
         (notes-dir (concat org-directory "agent-notes/"))
         (note-file (concat notes-dir slug ".org"))
         (sections (if (and sections-csv (not (string-empty-p sections-csv))
                            (not (equal sections-csv "nil")))
                       (split-string sections-csv ",")
                     '("Summary" "Findings" "Sources")))
         (filetags (if (and tags-csv (not (string-empty-p tags-csv))
                            (not (equal tags-csv "nil")))
                       (concat ":" (mapconcat #'identity
                                              (split-string tags-csv ",")
                                              ":") ":")
                     ":research:"))
         (date-str (format-time-string "[%Y-%m-%d %a]")))
    (when (string-empty-p slug)
      (org-gtd-cli/error
       "Error: title %S produces an empty filename slug. Slugs keep only ASCII letters, digits, spaces and hyphens; include at least one ASCII letter or digit in the title."
       title)
      (kill-emacs 1))
    (when (file-exists-p note-file)
      (org-gtd-cli/error
       "Error: note file already exists: %s. Refusing to overwrite; choose a different title or edit the existing note."
       note-file)
      (kill-emacs 1))
    (unless (file-directory-p notes-dir)
      (make-directory notes-dir t))
    (with-temp-file note-file
      (insert (format "#+title: %s\n" title))
      (insert (format "#+date: %s\n" date-str))
      (insert (format "#+filetags: %s\n" filetags))
      (dolist (section sections)
        (insert (format "\n* %s\n" (string-trim section)))))
    ;; Link from task if requested
    (when (and link-task (not (string-empty-p link-task))
               (not (equal link-task "nil")))
      (let ((buf-pos (org-gtd-cli/find-task link-task nil t)))
        (with-current-buffer (car buf-pos)
          (org-with-wide-buffer
           (goto-char (cdr buf-pos))
           (let ((level (org-current-level))
                 (subtree-end (save-excursion (org-end-of-subtree t) (point))))
             ;; Find insertion point: after heading/planning/body, before children
             (org-end-of-meta-data t)
             ;; Clamp: org-end-of-meta-data can overshoot past subtree-end
             ;; for deeply nested leaf headings (level 6+)
             (when (> (point) subtree-end)
               (goto-char subtree-end))
             (let ((insert-point (point)))
               ;; Check if there are child headings
               (when (re-search-forward
                      (format "^\\*\\{%d,\\} " (1+ level))
                      subtree-end t)
                 (setq insert-point (line-beginning-position)))
               (goto-char insert-point)
               (insert (format "Research file: [[file:agent-notes/%s.org]]\n" slug))))
          (save-buffer)))))
    (if org-gtd-cli/json-mode
        (let ((linked (and link-task (not (string-empty-p link-task))
                           (not (equal link-task "nil"))))
              (rel-note (org-gtd-cli/relative-filename note-file)))
          (org-gtd-cli/output
           `((version . 1) (command . "add-note")
             (heading . ,title) (file . ,rel-note)
             (sections . ,(vconcat sections))
             (linked_task . ,(if linked link-task :null))
             (side_effects . ,(if linked
                                  (vector `((action . "append-body")
                                            (heading . ,link-task)
                                            (text . ,(format "Research file: [[file:agent-notes/%s.org]]" slug))))
                                [])))))
      (princ (format "Created: %s\n" note-file))))
  (kill-emacs 0))

;; --- append-body ---

(defun org-gtd-cli/append-body (substring text &optional index)
  "Append text to an existing task's body."
  (org-gtd-cli/validate-body-text text)
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (level (org-current-level))
              (subtree-end (save-excursion (org-end-of-subtree t) (point))))
         ;; Find insertion point: after body text, before creation timestamp,
         ;; before first child heading.
         ;; Clamp: when there's no body/metadata (e.g. Orgzly-created tasks),
         ;; org-end-of-meta-data can overshoot past subtree-end.
         (org-end-of-meta-data t)
         (when (> (point) subtree-end)
           (goto-char subtree-end))
         (let* ((body-start (point))
                (body-end (save-excursion
                            (if (re-search-forward
                                 (format "^\\*\\{%d,\\} " (1+ level))
                                 subtree-end t)
                                (line-beginning-position)
                              subtree-end)))
                (insert-point body-end))
           ;; Scan body for last inactive timestamp line and insert before it
           (save-excursion
             (goto-char body-start)
             (let ((last-ts-pos nil))
               (while (and (< (point) body-end)
                           (not (eobp)))
                 (when (looking-at "^\\[[-0-9]+ [A-Z][a-z]+\\( [0-9:]+\\)?\\]$")
                   (setq last-ts-pos (point)))
                 (forward-line 1))
               (when last-ts-pos
                 (setq insert-point last-ts-pos))))
           (setq text (org-gtd-cli/fill-text text))
           (goto-char insert-point)
           ;; Ensure we start on a fresh line (no-body headings end without newline)
           (unless (bolp) (insert "\n"))
           (insert text "\n"))
         (save-buffer)
         (if org-gtd-cli/json-mode
             (org-gtd-cli/mutation-output
              `((version . 1)
                (command . "append-body")
                (heading . ,heading)
                (file . ,rel-file))
              buf-pos)
           (princ (format "Appended to: \"%s\" (%s)\n" heading rel-file)))))))
  (kill-emacs 0))

;; --- set-body ---

(defun org-gtd-cli/set-body (substring text &optional index)
  "Replace the body of an existing task, or remove it if TEXT is empty."
  (org-gtd-cli/validate-body-text text)
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (level (org-current-level))
              (subtree-end (save-excursion (org-end-of-subtree t) (point))))
         ;; Clamp: when there's no body/metadata (e.g. Orgzly-created tasks),
         ;; org-end-of-meta-data can overshoot past subtree-end.
         (org-end-of-meta-data t)
         (when (> (point) subtree-end)
           (goto-char subtree-end))
         (let* ((body-start (point))
                (body-end (save-excursion
                            (if (re-search-forward
                                 (format "^\\*\\{%d,\\} " (1+ level))
                                 subtree-end t)
                                (line-beginning-position)
                              subtree-end)))
                (ts-start nil))
           ;; Find trailing inactive timestamp line
           (save-excursion
             (goto-char body-start)
             (let ((last-ts-pos nil))
               (while (and (< (point) body-end)
                           (not (eobp)))
                 (when (looking-at "^\\[[-0-9]+ [A-Z][a-z]+\\( [0-9:]+\\)?\\]$")
                   (setq last-ts-pos (point)))
                 (forward-line 1))
               (when last-ts-pos
                 (setq ts-start last-ts-pos))))
           ;; Delete existing body (everything from body-start to timestamp or body-end)
           (let ((delete-end (or ts-start body-end)))
             (when (< body-start delete-end)
               (delete-region body-start delete-end)))
           ;; Insert new text if non-empty
           (when (not (string-empty-p text))
             (setq text (org-gtd-cli/fill-text text))
             (goto-char body-start)
             ;; Ensure we start on a fresh line (no-body headings end without newline)
             (unless (bolp) (insert "\n"))
             (insert text "\n")))
         (save-buffer)
         (if org-gtd-cli/json-mode
             (org-gtd-cli/mutation-output
              `((version . 1)
                (command . "set-body")
                (heading . ,heading)
                (file . ,rel-file))
              buf-pos)
           (princ (format "Set body: \"%s\" (%s)\n" heading rel-file)))))))
  (kill-emacs 0))

;; --- done: auto-progress helpers ---

(defun org-gtd-cli/auto-progress (rel-file)
  "Run project-aware auto-progress from point (a just-completed task).
Returns a list of message strings in printing order."
  (let ((msgs '()))
    (when (gtd/is-project-subtree-p)
      (let ((all-sibling-states
             (save-excursion
               (org-up-heading-safe)
               (when (org-goto-first-child)
                 (let ((states (list (org-get-todo-state))))
                   (while (org-get-next-sibling)
                     (push (org-get-todo-state) states))
                   states)))))
        (when all-sibling-states
          (cond
           ;; A sibling is already active — do nothing. NEXT and WAITING both
           ;; count as "in motion" (matching `gtd/has-active-in-subtree-p' and
           ;; the stuck-project skip functions): a project with a WAITING child
           ;; is not stuck, so we must NOT promote a later TODO past it.
           ((seq-some (lambda (s) (member s '("NEXT" "WAITING"))) all-sibling-states))
           ;; All siblings done — leave parent open for manual review.
           ;; We deliberately do NOT auto-complete the project (and therefore
           ;; do not cascade to grandparents): the last subtask being done
           ;; doesn't mean the project is done — more work may simply not be
           ;; filed as subtasks yet. Closure is a manual decision via an
           ;; explicit `set-done` on the project heading.
           ((not (cl-find-if
                  (lambda (s) (and s (not (member s org-done-keywords))))
                  all-sibling-states))
            (save-excursion
              (org-up-heading-safe)
              (when (org-entry-is-todo-p)
                (setq msgs (nconc msgs
                  (list (format "  All subtasks done — project left open for review: \"%s\" (%s)\n"
                                (org-get-heading t t t t) rel-file)))))))
           ;; Default — promote next actionable task (project-aware)
           (t
            (save-excursion
              (let ((promoted nil))
                (while (and (not promoted) (org-get-next-sibling))
                  (when (equal (org-get-todo-state) "TODO")
                    (if (not (gtd/is-project-p))
                        (let ((h (org-get-heading t t t t)))
                          (org-todo "NEXT")
                          (setq msgs (nconc msgs
                            (list (format "  Auto-progressed: \"%s\" -> NEXT (%s)\n"
                                          h rel-file))))
                          (setq promoted t))
                      (if (gtd/has-active-in-subtree-p)
                          nil
                        (let* ((sub-heading (org-get-heading t t t t))
                               (child-heading (gtd/promote-first-child-task)))
                          (when child-heading
                            (setq msgs (nconc msgs
                              (list (format "  Auto-progressed: \"%s\" -> NEXT (in subproject \"%s\") (%s)\n"
                                            child-heading sub-heading rel-file))))
                            (setq promoted t))))))))))))))
    msgs))

(defun org-gtd-cli/auto-progress-preview (rel-file)
  "Preview auto-progress from point (task simulated as DONE).
Returns a list of message strings in printing order."
  (let ((msgs '()))
    (when (gtd/is-project-subtree-p)
      (let ((saved-pos (point))
            (has-active nil)
            (all-done t))
        ;; Scan ALL siblings, treating saved-pos as DONE
        (save-excursion
          (org-up-heading-safe)
          (when (org-goto-first-child)
            (catch 'scanned
              (while t
                (let ((s (if (= (point) saved-pos) "DONE" (org-get-todo-state))))
                  ;; NEXT or WAITING both count as "active" (see the real
                  ;; `org-gtd-cli/auto-progress' guard) — keep the preview honest.
                  (when (member s '("NEXT" "WAITING")) (setq has-active t))
                  (when (and s (not (member s org-done-keywords)))
                    (setq all-done nil)))
                (unless (org-get-next-sibling) (throw 'scanned nil))))))
        (cond
         (has-active nil)
         ;; All done → parent left open for manual review (no auto-close, no cascade)
         (all-done
          (save-excursion
            (org-up-heading-safe)
            (when (org-entry-is-todo-p)
              (setq msgs (nconc msgs
                (list (format "  All subtasks done — project would be left open for review: \"%s\" (%s)\n"
                              (org-get-heading t t t t) rel-file)))))))
         ;; Default — promotion preview (no state changes)
         (t
          (save-excursion
            (let ((found nil))
              (while (and (not found) (org-get-next-sibling))
                (when (equal (org-get-todo-state) "TODO")
                  (if (not (gtd/is-project-p))
                      (progn
                        (setq msgs (nconc msgs
                          (list (format "  Would auto-progress: \"%s\" -> NEXT\n"
                                        (org-get-heading t t t t)))))
                        (setq found t))
                    (unless (gtd/has-active-in-subtree-p)
                      (let ((sub-heading (org-get-heading t t t t)))
                        (save-excursion
                          (when (org-goto-first-child)
                            (catch 'done
                              (while t
                                (when (and (equal (org-get-todo-state) "TODO")
                                           (not (gtd/is-project-p)))
                                  (setq msgs (nconc msgs
                                    (list (format "  Would auto-progress: \"%s\" -> NEXT (in subproject \"%s\")\n"
                                                  (org-get-heading t t t t) sub-heading))))
                                  (throw 'done t))
                                (unless (org-get-next-sibling) (throw 'done nil)))))))
                      (setq found t)))))))))))
    msgs))

;; --- done ---

(defun org-gtd-cli/set-done (substring &optional index dry-run)
  "Mark a task as DONE."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (buf-pos (org-gtd-cli/find-task substring idx)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (old-state (org-get-todo-state)))
         (if is-dry-run
             ;; Dry-run can't call `org-todo', so detect a blocked transition
             ;; up front with `org-entry-blocked-p' (same predicate
             ;; `org-enforce-todo-dependencies' uses).  A blocked parent must
             ;; NOT preview success.
             (if (org-entry-blocked-p)
                 (let ((blocker (and (boundp 'org-block-entry-blocking)
                                     (stringp org-block-entry-blocking)
                                     org-block-entry-blocking)))
                   (if org-gtd-cli/json-mode
                       (org-gtd-cli/output
                        `((error . ,(format "Cannot mark \"%s\" DONE: blocked by an incomplete subtask" heading))
                          (hint . ,(if blocker
                                       (format "Complete or cancel \"%s\" first, then retry." blocker)
                                     "Complete or cancel its open subtasks first, then retry."))
                          (dry_run . t)
                          (exit_code . 1)))
                     (if blocker
                         (org-gtd-cli/error
                          "Would be blocked: cannot mark \"%s\" DONE — blocked by an incomplete subtask\nHint: Complete or cancel \"%s\" first, then retry."
                          heading blocker)
                       (org-gtd-cli/error
                        "Would be blocked: cannot mark \"%s\" DONE — blocked by an incomplete subtask\nHint: Complete or cancel its open subtasks first, then retry."
                        heading)))
                   (kill-emacs 1))
               (if org-gtd-cli/json-mode
                   (let ((result `((version . 1)
                                   (command . "set-done")
                                   (heading . ,heading)
                                   (file . ,rel-file)
                                   (old_state . ,old-state)
                                   (new_state . "DONE")
                                   (dry_run . t)
                                   (side_effects . ,(org-gtd-cli/parse-side-effects-preview
                                                     (org-gtd-cli/auto-progress-preview rel-file)
                                                     rel-file)))))
                     (org-gtd-cli/output result))
                 (princ (format "Would mark done: %s (%s)\n" heading rel-file))
                 (dolist (msg (org-gtd-cli/auto-progress-preview rel-file))
                   (princ msg))))
           ;; Real path
           (let ((org-inhibit-logging nil))
             (org-todo "DONE"))
           ;; `org-todo' on an entry blocked by `org-enforce-todo-dependencies'
           ;; (e.g. a project with incomplete children) does NOT signal a
           ;; catchable error — it emits a "blocked" notice via `message' and
           ;; leaves the state unchanged.  Verify the transition actually took
           ;; before reporting success, otherwise we would falsely claim DONE,
           ;; run auto-progress, and persist stray changes.
           (if (not (equal (org-get-todo-state) "DONE"))
               (let ((blocker (and (boundp 'org-block-entry-blocking)
                                   (stringp org-block-entry-blocking)
                                   org-block-entry-blocking)))
                 (if org-gtd-cli/json-mode
                     ;; JSON: error+hint object goes to stdout (see
                     ;; `org-gtd-cli/error').
                     (org-gtd-cli/output
                      `((error . ,(format "Cannot mark \"%s\" DONE: blocked by an incomplete subtask" heading))
                        (hint . ,(if blocker
                                     (format "Complete or cancel \"%s\" first, then retry." blocker)
                                   "Complete or cancel its open subtasks first, then retry."))
                        (exit_code . 1)))
                   (if blocker
                       (org-gtd-cli/error
                        "Cannot mark \"%s\" DONE: blocked by an incomplete subtask\nHint: Complete or cancel \"%s\" first, then retry."
                        heading blocker)
                     (org-gtd-cli/error
                      "Cannot mark \"%s\" DONE: blocked by an incomplete subtask\nHint: Complete or cancel its open subtasks first, then retry."
                      heading)))
                 (kill-emacs 1))
             (let ((auto-msgs (org-gtd-cli/auto-progress rel-file)))
               (save-excursion
                 (goto-char (cdr buf-pos))
                 (org-gtd-cli/reorder-siblings-by-state))
               (save-buffer)
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/mutation-output
                    `((version . 1)
                      (command . "set-done")
                      (heading . ,heading)
                      (file . ,rel-file)
                      (old_state . ,old-state)
                      (new_state . "DONE")
                      (side_effects . ,(org-gtd-cli/parse-side-effects auto-msgs)))
                    heading)
                 (princ (format "Done: %s (%s)\n" heading rel-file))
                 (dolist (msg auto-msgs)
                   (princ msg))))))))))
  (kill-emacs 0))

(defun org-gtd-cli/parse-side-effects (msgs)
  "Parse auto-progress message strings into a JSON-serializable
side_effects vector."
  (let ((effects '()))
    (dolist (msg msgs)
      (cond
       ((string-match "Auto-completed project: \"\\([^\"]+\\)\" (\\([^)]+\\))" msg)
        (push `((action . "state-change")
                (heading . ,(match-string 1 msg))
                (old_state . "TODO")
                (new_state . "DONE")
                (file . ,(match-string 2 msg)))
              effects))
       ;; Subproject drill-in must be matched before the generic NEXT clause:
       ;; the generic regex also matches this message but captures the
       ;; "in subproject ..." segment as the file.
       ((string-match "Auto-progressed: \"\\([^\"]+\\)\" -> NEXT (in subproject \"\\([^\"]+\\)\") (\\([^)]+\\))" msg)
        (push `((action . "state-change")
                (heading . ,(match-string 1 msg))
                (old_state . "TODO")
                (new_state . "NEXT")
                (file . ,(match-string 3 msg)))
              effects))
       ((string-match "Auto-progressed: \"\\([^\"]+\\)\" -> NEXT (\\([^)]+\\))" msg)
        (push `((action . "state-change")
                (heading . ,(match-string 1 msg))
                (old_state . "TODO")
                (new_state . "NEXT")
                (file . ,(match-string 2 msg)))
              effects))
       ((string-match "All subtasks done — project left open for review: \"\\([^\"]+\\)\" (\\([^)]+\\))" msg)
        (push `((action . "project-needs-review")
                (heading . ,(match-string 1 msg))
                (file . ,(match-string 2 msg)))
              effects))))
    (apply #'vector (nreverse effects))))

(defun org-gtd-cli/parse-side-effects-preview (msgs rel-file)
  "Parse auto-progress PREVIEW message strings into a side_effects vector.
Mirrors `org-gtd-cli/parse-side-effects' for the dry-run wording produced by
`org-gtd-cli/auto-progress-preview'.  Preview messages omit the file, so
REL-FILE supplies the file field — auto-progress only ever touches siblings
within the just-completed task's own file."
  (let ((effects '()))
    (dolist (msg msgs)
      (cond
       ((string-match "Would auto-progress: \"\\([^\"]+\\)\" -> NEXT" msg)
        (push `((action . "state-change")
                (heading . ,(match-string 1 msg))
                (old_state . "TODO")
                (new_state . "NEXT")
                (file . ,rel-file))
              effects))
       ((string-match "project would be left open for review: \"\\([^\"]+\\)\"" msg)
        (push `((action . "project-needs-review")
                (heading . ,(match-string 1 msg))
                (file . ,rel-file))
              effects))))
    (apply #'vector (nreverse effects))))

;; --- set-state ---

(defun org-gtd-cli/reject-next (heading)
  "Emit the \"NEXT is project-internal\" rejection for HEADING and exit 1.
NEXT marks the concrete next action *among a project's children*; a
freestanding task (filed directly under a category heading, or otherwise
not nested inside a project) must never be NEXT.  Used by `add-task',
`set-state', and `set-next' so the loophole is closed at every entry."
  (let ((err (format (concat "Cannot set NEXT on \"%s\": NEXT is only valid for an "
                             "actionable item inside a project (a TODO heading with "
                             "sub-TODO children)")
                     heading))
        (hint (concat "File it as TODO instead, or attach it to a project first "
                      "(refile it under a project heading), then set NEXT.")))
    (if org-gtd-cli/json-mode
        ;; JSON: error+hint object goes to stdout (see `org-gtd-cli/error').
        (org-gtd-cli/output `((error . ,err) (hint . ,hint) (exit_code . 1)))
      (org-gtd-cli/error "%s\nHint: %s" err hint)))
  (kill-emacs 1))

(defun org-gtd-cli/ensure-next-allowed (heading)
  "Reject NEXT when the heading at point is not a child of a project.
Point must be on the target heading.  A valid NEXT target is a task with
a TODO-keyword ancestor (`gtd/is-subproject-p'); anything else — a
freestanding category-level task or a top-level task — is rejected."
  (unless (gtd/is-subproject-p)
    (org-gtd-cli/reject-next heading)))

(defun org-gtd-cli/state-note-entry (new-state old-state reason)
  "Return an Org state-change note entry for NEW-STATE, OLD-STATE, and REASON."
  (let ((note (replace-regexp-in-string
               "\n" "\n  " (string-trim (or reason "")))))
    (format "- State %-12S from %-12S %s \\\\\n  %s"
            new-state (or old-state "") (format-time-string "[%Y-%m-%d %a %H:%M]")
            note)))

(defun org-gtd-cli/add-state-reason-note (new-state old-state reason)
  "Add REASON to the current task's state-change LOGBOOK entry.
If Org already logged the state change, convert that entry into a note.
Otherwise create a standard state-change note in the LOGBOOK drawer."
  (let* ((subtree-end (save-excursion (org-end-of-subtree t) (point)))
         (meta-end (save-excursion (org-end-of-meta-data t) (point)))
         (note (replace-regexp-in-string
                "\n" "\n  " (string-trim (or reason ""))))
         (entry (org-gtd-cli/state-note-entry new-state old-state reason))
         (converted nil))
    (save-excursion
      (goto-char (line-beginning-position))
      (when (re-search-forward "^[ \t]*:LOGBOOK:" meta-end t)
        (let ((drawer-end (save-excursion
                            (re-search-forward "^[ \t]*:END:" subtree-end t))))
          (when drawer-end
            (forward-line 1)
            (when (re-search-forward
                   (format "^[ \t]*- State[ \t]+%S[ \t]+from[ \t]+%S[ \t]+\\[[^]\n]+\\]"
                           new-state (or old-state ""))
                   drawer-end t)
              (end-of-line)
              (unless (save-excursion
                        (beginning-of-line)
                        (looking-at-p ".*\\\\\\\\[ \t]*$"))
                (insert " \\\\"))
              (insert "\n  " note)
              (setq converted t))))))
    (unless converted
      (save-excursion
        (goto-char (line-beginning-position))
        (if (re-search-forward "^[ \t]*:LOGBOOK:" meta-end t)
            (progn
              (forward-line 1)
              (insert entry "\n"))
          (goto-char (line-beginning-position))
          (org-end-of-meta-data)
          (when (> (point) subtree-end)
            (goto-char subtree-end))
          (insert ":LOGBOOK:\n" entry "\n:END:\n"))))))

(defun org-gtd-cli/set-state (substring new-state &optional index dry-run reason)
  "Change a task's TODO state."
  ;; Validate state before doing anything
  (let ((all-states (apply #'append
                           (mapcar (lambda (seq)
                                     (cl-remove-if
                                      (lambda (s) (member s '("|")))
                                      (mapcar (lambda (s)
                                                (replace-regexp-in-string "(.*)" "" s))
                                              (cdr seq))))
                                   org-todo-keywords))))
    (unless (member new-state all-states)
      (let ((valid-str (mapconcat #'identity all-states ", ")))
        (if org-gtd-cli/json-mode
            ;; JSON: error+hint object goes to stdout (see `org-gtd-cli/error').
            (org-gtd-cli/output
             `((error . ,(format "\"%s\" is not a valid state" new-state))
               (hint . ,(format "Valid states: %s" valid-str))))
          (org-gtd-cli/error "Error: \"%s\" is not a valid state" new-state)
          (org-gtd-cli/error "Valid states: %s" valid-str)))
      (kill-emacs 1)))
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (old-state (org-get-todo-state))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name))))
         ;; NEXT is project-internal: reject (don't silently coerce) when the
         ;; target isn't an actionable item inside a project. Guard the dry-run
         ;; preview too, so a previewed invalid op fails the same way.
         (when (equal new-state "NEXT")
           (org-gtd-cli/ensure-next-allowed heading))
         (if is-dry-run
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/output
                  `((version . 1)
                    (command . "set-state")
                    (heading . ,heading)
                    (file . ,rel-file)
                    (old_state . ,(or old-state :null))
                    (new_state . ,new-state)
                    (dry_run . t)))
               (princ (format "Would change: \"%s\" %s -> %s (%s)\n"
                              heading old-state new-state rel-file)))
           (let ((org-inhibit-logging nil))
             (org-todo new-state))
           (when (and reason (not (string-empty-p reason)))
             (org-gtd-cli/add-state-reason-note new-state old-state reason))
           (unless (and (equal new-state "WAITING")
                        (member old-state '("TODO" "NEXT")))
             (org-gtd-cli/reorder-siblings-by-state))
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1)
                  (command . "set-state")
                  (heading . ,heading)
                  (file . ,rel-file)
                  (old_state . ,(or old-state :null))
                  (new_state . ,new-state))
                heading)
             (princ (format "State change: \"%s\" %s -> %s (%s)\n"
                            heading old-state new-state rel-file))))))))
  (kill-emacs 0))

;; --- refile ---

(defun org-gtd-cli/find-category-matches (category)
  "Find refile targets matching CATEGORY in tasks.org.
CATEGORY is substring-matched against non-TODO headings; it may contain
`/' separators for ancestor path matching (substring on each segment).
Returns a list of matches, each of the form (BUFFER POS FILE HEADING-PATH)."
  (let* ((cat-parts (split-string category "/" t))
         (matches '())
         (cat-file (expand-file-name "tasks.org" org-directory)))
    (when (file-exists-p cat-file)
      (with-current-buffer (find-file-noselect cat-file)
        (org-with-wide-buffer
         (goto-char (point-min))
         (while (re-search-forward org-heading-regexp nil t)
           (unless (org-get-todo-state)
             (let* ((heading (org-get-heading t t t t))
                    (stripped (org-gtd-cli/strip-markup heading)))
               (if (= (length cat-parts) 1)
                   (when (or (string-match-p (regexp-quote (car cat-parts)) heading)
                             (string-match-p (regexp-quote (car cat-parts)) stripped))
                     (push (list (current-buffer) (point) cat-file
                                 (org-gtd-cli/heading-path-at-point))
                           matches))
                 ;; Multi-segment: substring match on last, substring on ancestors
                 (when (or (string-match-p (regexp-quote (car (last cat-parts))) heading)
                           (string-match-p (regexp-quote (car (last cat-parts))) stripped))
                   (let ((path-match t)
                         (parts (butlast cat-parts)))
                     (save-excursion
                       (dolist (part (reverse parts))
                         (unless (and (org-up-heading-safe)
                                      (let ((h (org-get-heading t t t t)))
                                        (or (string-match-p (regexp-quote part) h)
                                            (string-match-p (regexp-quote part)
                                                            (org-gtd-cli/strip-markup h)))))
                           (setq path-match nil))))
                     (when path-match
                       (push (list (current-buffer) (point) cat-file
                                   (org-gtd-cli/heading-path-at-point))
                             matches)))))))))))
    (nreverse matches)))

(defun org-gtd-cli/refile (substring target category &optional index dry-run)
  "Move a task to a different heading.
TARGET (--to) uses exact match on any heading across all agenda files.
CATEGORY (--category) uses substring match on non-TODO headings in tasks.org."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (buf-pos (org-gtd-cli/find-task substring idx t))
         (src-buf (car buf-pos))
         (src-start (cdr buf-pos))
         (src-end (with-current-buffer src-buf
                    (org-with-wide-buffer
                     (goto-char src-start)
                     (org-end-of-subtree t)
                     (point))))
         (use-exact (and target (not (equal target "nil")) (not (string-empty-p target))))
         (target-name (if use-exact target category)))
    ;; Find the target heading
    (let ((target-pos nil)
          (target-buf nil)
          (target-file nil))
      (if use-exact
          ;; --to: exact match on heading text (case-insensitive), any heading type
          (let ((target-parts (split-string target "/" t))
                (self-match-count 0))
            (dolist (file (org-agenda-files))
              (when (and (file-exists-p file) (not target-pos))
                (with-current-buffer (find-file-noselect file)
                  (org-with-wide-buffer
                   (goto-char (point-min))
                   (while (and (not target-pos)
                               (re-search-forward org-heading-regexp nil t))
                     (let ((in-source (and (eq (current-buffer) src-buf)
                                           (>= (point) src-start)
                                           (< (point) src-end)))
                           (heading (org-get-heading t t t t)))
                       (if (= (length target-parts) 1)
                           ;; Single segment: exact case-insensitive (with markup stripping)
                           (when (let ((dh (downcase heading))
                                       (dt (downcase (car target-parts))))
                                   (or (string= dt dh)
                                       (string= dt (downcase (org-gtd-cli/strip-markup heading)))))
                             (if in-source
                                 (cl-incf self-match-count)
                               (setq target-pos (point)
                                     target-buf (current-buffer)
                                     target-file file)))
                         ;; Multi-segment: exact match on last, exact on each ancestor
                         (when (let ((dh (downcase heading))
                                     (dt (downcase (car (last target-parts)))))
                                 (or (string= dt dh)
                                     (string= dt (downcase (org-gtd-cli/strip-markup heading)))))
                           (let ((path-match t)
                                 (parts (butlast target-parts)))
                             (save-excursion
                               (dolist (part (reverse parts))
                                 (unless (and (org-up-heading-safe)
                                              (let ((h (org-get-heading t t t t)))
                                                (or (string= (downcase part) (downcase h))
                                                    (string= (downcase part)
                                                             (downcase (org-gtd-cli/strip-markup h))))))
                                   (setq path-match nil))))
                             (when path-match
                               (if in-source
                                   (cl-incf self-match-count)
                                 (setq target-pos (point)
                                       target-buf (current-buffer)
                                       target-file file))))))))))))
            ;; Error handling for --to
            (unless target-pos
              (let ((msg (if (> self-match-count 0)
                             (format "No valid refile target for \"%s\" (skipped %d self-match%s inside source subtree)"
                                     target self-match-count (if (= self-match-count 1) "" "es"))
                           (format "Target heading \"%s\" not found" target))))
                (if org-gtd-cli/json-mode
                    ;; JSON: error+hint object goes to stdout (see `org-gtd-cli/error').
                    (org-gtd-cli/output
                     `((error . ,msg)
                       (hint . "Use 'categories' to see available targets.")))
                  (org-gtd-cli/error "Error: %s" msg)
                  (org-gtd-cli/error "Hint: use 'categories' to see available targets.")))
              (kill-emacs 1)))
        ;; --category: substring match on non-TODO headings in tasks.org only
        (let ((matches (org-gtd-cli/find-category-matches category)))
          (cond
           ((null matches)
            (if org-gtd-cli/json-mode
                ;; JSON: error+hint object goes to stdout (see `org-gtd-cli/error').
                (org-gtd-cli/output
                 `((error . ,(format "Category heading \"%s\" not found" category))
                   (hint . "Use 'categories' to see available targets.")))
              (org-gtd-cli/error "Error: category heading \"%s\" not found" category)
              (org-gtd-cli/error "Hint: use 'categories' to see available targets."))
            (kill-emacs 1))
           ((> (length matches) 1)
            (org-gtd-cli/error "Multiple category matches for \"%s\":" category)
            (let ((idx 1))
              (dolist (m matches)
                (org-gtd-cli/error "[%d] %s (%s)"
                                   idx (nth 3 m)
                                   (org-gtd-cli/relative-filename (nth 2 m)))
                (cl-incf idx)))
            (org-gtd-cli/error "Use a more specific path (e.g. --category \"Parent/Child\").")
            (kill-emacs 2))
           (t
            (let ((m (car matches)))
              (setq target-buf (nth 0 m)
                    target-pos (nth 1 m)
                    target-file (nth 2 m)))))))
      (with-current-buffer (car buf-pos)
        (org-with-wide-buffer
         (goto-char (cdr buf-pos))
         (let* ((heading (org-get-heading t t t t))
                (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
                (rel-target (org-gtd-cli/relative-filename target-file)))
           (let ((target-heading (with-current-buffer target-buf
                                    (org-with-wide-buffer
                                     (goto-char target-pos)
                                     (org-get-heading t t t t)))))
             (if is-dry-run
                 (if org-gtd-cli/json-mode
                     (org-gtd-cli/output
                      `((version . 1) (command . "refile")
                        (heading . ,heading) (file . ,rel-file)
                        (target_heading . ,target-heading)
                        (target_file . ,rel-target) (dry_run . t)))
                   (princ (format "Would refile: \"%s\" -> %s/%s (%s)\n"
                                  heading rel-target target-name rel-file)))
               ;; Perform the refile. Use a marker for target-pos so it
               ;; tracks correctly when source and target share a buffer
               ;; (deletion of the source subtree can shift positions).
               (let* ((target-marker (with-current-buffer target-buf
                                       (save-excursion
                                         (goto-char target-pos)
                                         (point-marker))))
                      (rfloc (list (org-get-heading t t t t)
                                   target-file nil target-marker)))
                 (org-refile nil nil rfloc)
                 ;; Restore GTD invariants at the destination: demote a moved
                 ;; NEXT that becomes freestanding or a duplicate NEXT sibling,
                 ;; demote a NEXT parent that has just become a project, then
                 ;; reorder destination siblings by state.
                 (org-gtd-cli/refile-repair-invariants
                  target-buf (marker-position target-marker) heading)
                 (set-marker target-marker nil))
               (save-buffer)
               (with-current-buffer target-buf (save-buffer))
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/mutation-output
                    `((version . 1) (command . "refile")
                      (heading . ,heading) (file . ,rel-file)
                      (target_heading . ,target-heading)
                      (target_file . ,rel-target))
                    nil)
                 (princ (format "Refiled: \"%s\" -> %s/%s (%s)\n"
                                heading rel-target target-name rel-file)))))))))
    (kill-emacs 0)))

(defun org-gtd-cli/refile-repair-invariants (target-buf target-pos moved-heading)
  "Restore GTD invariants after refiling MOVED-HEADING under TARGET-POS in TARGET-BUF.
Demotes the moved subtree to TODO when it is a NEXT that would be freestanding
or a duplicate NEXT sibling in its new home; demotes a NEXT parent that has just
become a project because a child was refiled under it; and reorders destination
siblings by state (DONE > NEXT > TODO > WAITING > DEFER)."
  (with-current-buffer target-buf
    (org-with-wide-buffer
     (goto-char target-pos)
     (let* ((parent-level (org-current-level))
            (child-level (1+ parent-level))
            (subtree-end (save-excursion (org-end-of-subtree t) (point)))
            (moved-pos nil))
       ;; Locate the moved subtree among the target's direct children.
       (save-excursion
         (forward-line 1)
         (while (and (not moved-pos) (< (point) subtree-end)
                     (re-search-forward org-heading-regexp subtree-end t))
           (when (and (= (org-current-level) child-level)
                      (string= (org-get-heading t t t t) moved-heading))
             (setq moved-pos (line-beginning-position)))))
       (when moved-pos
         (goto-char moved-pos)
         (let ((moved-state (org-get-todo-state)))
           ;; Demote moved NEXT when it becomes freestanding, or when a
           ;; sibling NEXT already occupies the destination project.
           (when (string= moved-state "NEXT")
             (let ((freestanding (not (gtd/is-subproject-p)))
                   (has-other-next nil))
               (save-excursion
                 (goto-char target-pos)
                 (let ((sib-end (save-excursion (org-end-of-subtree t) (point))))
                   (save-excursion
                     (forward-line 1)
                     (while (and (not has-other-next) (< (point) sib-end)
                                 (re-search-forward org-heading-regexp sib-end t))
                       (when (and (= (org-current-level) child-level)
                                  (not (= (line-beginning-position) moved-pos))
                                  (equal (org-get-todo-state) "NEXT"))
                         (setq has-other-next t))))))
               (when (or freestanding has-other-next)
                 (let ((org-inhibit-logging nil))
                   (org-todo "TODO")))))
           ;; Reorder destination siblings using the standard rank.
           (org-gtd-cli/reorder-siblings-by-state)))
       ;; Demote target parent from NEXT to TODO when it just gained a
       ;; TODO-keyword child (a NEXT that becomes a project must be TODO).
       (save-excursion
         (goto-char target-pos)
         (when (and (equal (org-get-todo-state) "NEXT")
                    (org-gtd-cli/has-todo-children-p))
           (let ((org-inhibit-logging nil))
             (org-todo "TODO"))))))))

;; --- set-next ---

(defun org-gtd-cli/set-next (substring &optional index)
  "Set a task to NEXT state.
For leaf tasks (no children), set the task itself to NEXT.
For projects (has children), promote the first TODO child to NEXT.
If the target already has a NEXT (subtask or itself), report it and exit 0."
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (level (org-current-level))
              (child-level (1+ level))
              (subtree-end (save-excursion (org-end-of-subtree t) (point)))
              (has-children nil)
              (existing-next nil)
              (first-todo-pos nil))
         ;; Scan direct children
         (save-excursion
           (forward-line 1)
           (while (and (< (point) subtree-end)
                       (re-search-forward org-heading-regexp subtree-end t))
             (when (= (org-current-level) child-level)
               (setq has-children t)
               (let ((child-state (org-get-todo-state)))
                 (when (and child-state (string= child-state "NEXT") (not existing-next))
                   (setq existing-next (org-get-heading t t t t)))
                 (when (and child-state (string= child-state "TODO") (not first-todo-pos))
                   (setq first-todo-pos (point)))))))
         ;; Subproject guard: project headings can't be NEXT.
         ;; A subproject is a heading that has children AND whose parent
         ;; also has a TODO keyword (i.e. it's nested inside a project).
         (when (and has-children
                   (save-excursion
                     (when (org-up-heading-safe)
                       (org-get-todo-state))))
           (if org-gtd-cli/json-mode
               ;; JSON: error+hint object goes to stdout (see `org-gtd-cli/error').
               (org-gtd-cli/output
                `((error . ,(format "Cannot set-next on subproject: \"%s\" has subtasks" heading))
                  (hint . "Use set-next on the parent project, or set-state on a specific subtask.")
                  (exit_code . 1)))
             (org-gtd-cli/error
              "Cannot set-next on subproject: \"%s\" has subtasks\nHint: Use set-next on the parent project, or set-state on a specific subtask."
              heading))
           (kill-emacs 1))
         (cond
          ((not has-children)
           ;; Leaf task: NEXT is only valid inside a project. Rejects both a
           ;; freestanding category-level task and a top-level task (the old
           ;; immediate-parent check let top-level tasks slip through). Do NOT
           ;; steer the user toward `set-state ... NEXT' — that path now applies
           ;; the same guard.
           (org-gtd-cli/ensure-next-allowed heading)
           ;; Leaf task: set it to NEXT directly
           (let ((current-state (org-get-todo-state)))
             (cond
              ((string= current-state "NEXT")
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/mutation-output
                    `((version . 1) (command . "set-next")
                      (heading . ,heading) (file . ,rel-file)
                      (old_state . "NEXT") (new_state . "NEXT")
                      (side_effects . []))
                    buf-pos)
                 (org-gtd-cli/error "Already NEXT: \"%s\" (%s)" heading rel-file)))
              ((not (member current-state org-not-done-keywords))
               (org-gtd-cli/error "Error: \"%s\" is in done state %s" heading current-state)
               (kill-emacs 1))
              (t
               (let ((org-inhibit-logging nil))
                 (org-todo "NEXT"))
               (save-buffer)
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/mutation-output
                    `((version . 1) (command . "set-next")
                      (heading . ,heading) (file . ,rel-file)
                      (old_state . ,current-state) (new_state . "NEXT")
                      (side_effects . []))
                    buf-pos)
                 (princ (format "Set NEXT: \"%s\" (%s)\n" heading rel-file)))))))
          (existing-next
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1) (command . "set-next")
                  (heading . ,heading) (file . ,rel-file)
                  (old_state . ,(org-get-todo-state))
                  (new_state . ,(org-get-todo-state))
                  (side_effects . []))
                buf-pos)
             (org-gtd-cli/error "Already has NEXT: \"%s\" (%s)"
                                existing-next rel-file))
           (kill-emacs 0))
          ((not first-todo-pos)
           (org-gtd-cli/error "Error: \"%s\" has no TODO children to promote" heading)
           (kill-emacs 1))
          (t
           (goto-char first-todo-pos)
           (let* ((child-heading (org-get-heading t t t t))
                  (project-state (save-excursion
                                   (goto-char (cdr buf-pos))
                                   (org-get-todo-state))))
             (let ((org-inhibit-logging nil))
               (org-todo "NEXT"))
             (org-gtd-cli/reorder-siblings-by-state)
             (save-buffer)
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/mutation-output
                  `((version . 1) (command . "set-next")
                    (heading . ,heading) (file . ,rel-file)
                    (old_state . ,project-state) (new_state . ,project-state)
                    (side_effects . ,(vector
                                     `((action . "state-change")
                                       (heading . ,child-heading)
                                       (old_state . "TODO")
                                       (new_state . "NEXT")
                                       (file . ,rel-file)))))
                  heading)
               (princ (format "Set NEXT: \"%s\" (%s)\n"
                              child-heading rel-file))))))))))
  (kill-emacs 0))

;; --- move ---

(defun org-gtd-cli/move (substring direction &optional sibling-substring index)
  "Reorder a subtask within its sibling group."
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let ((heading (org-get-heading t t t t))
             (rel-file (org-gtd-cli/relative-filename (buffer-file-name))))
         (cond
          ((string= direction "up")
           (org-move-subtree-up)
           ;; The subtree has moved, so BUF-POS is now stale.  Save first
           ;; (so a failed re-find can't lose the move) and enrich by
           ;; re-finding via the unchanged HEADING, as `set-done' does.
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1) (command . "move")
                  (heading . ,heading) (file . ,rel-file)
                  (direction . "up") (sibling . :null))
                heading)
             (princ (format "Moved: \"%s\" up (%s)\n" heading rel-file))))
          ((string= direction "down")
           (org-move-subtree-down)
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1) (command . "move")
                  (heading . ,heading) (file . ,rel-file)
                  (direction . "down") (sibling . :null))
                heading)
             (princ (format "Moved: \"%s\" down (%s)\n" heading rel-file))))
          ((or (string= direction "before") (string= direction "after"))
           ;; Find sibling
           (unless (and sibling-substring
                        (not (string-empty-p sibling-substring))
                        (not (equal sibling-substring "nil")))
             (org-gtd-cli/error "Error: --before/--after requires a sibling substring")
             (kill-emacs 1))
           (let* ((level (org-current-level))
                  (task-beg (point))
                  (task-end (save-excursion (org-end-of-subtree t) (point)))
                  (task-text (buffer-substring task-beg task-end))
                  ;; Find the parent boundary
                  (parent-end (save-excursion
                                (org-up-heading-safe)
                                (org-end-of-subtree t)
                                (point)))
                  (sibling-pos nil))
             ;; Search siblings for the target
             (save-excursion
               (org-up-heading-safe)
               (let ((search-end parent-end))
                 (forward-line 1)
                 (while (and (not sibling-pos)
                             (re-search-forward org-heading-regexp search-end t))
                   (when (and (= (org-current-level) level)
                              (not (= (line-beginning-position) task-beg))
                              (string-match-p
                               (regexp-quote (downcase sibling-substring))
                               (downcase (org-get-heading t t t t))))
                     (setq sibling-pos (line-beginning-position))))))
             (unless sibling-pos
               (org-gtd-cli/error "Error: sibling \"%s\" not found" sibling-substring)
               (kill-emacs 1))
             ;; Delete the task. Compute the sibling's post-deletion
             ;; position from the SIBLING-POS found in the bounded
             ;; search above, adjusting for the deleted region.
             ;; (Re-searching the whole buffer here could match a
             ;; same-level heading under a different parent and
             ;; silently relocate the task there.)
             (goto-char task-beg)
             (let* ((del-end (save-excursion (org-end-of-subtree t)
                                             (if (eobp) (point) (1+ (point)))))
                    (new-sibling-pos (if (> sibling-pos task-beg)
                                         (- sibling-pos (- del-end task-beg))
                                       sibling-pos)))
               (delete-region task-beg del-end)
               (if (string= direction "before")
                   (progn
                     (goto-char new-sibling-pos)
                     (insert task-text "\n"))
                 ;; after
                 (goto-char new-sibling-pos)
                 (org-end-of-subtree t)
                 (unless (eobp) (forward-char))
                 (insert task-text "\n"))))
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1) (command . "move")
                  (heading . ,heading) (file . ,rel-file)
                  (direction . ,direction) (sibling . ,sibling-substring))
                heading)
             (princ (format "Moved: \"%s\" %s \"%s\" (%s)\n"
                            heading direction sibling-substring rel-file))))
          (t
           (org-gtd-cli/error "Error: unknown direction \"%s\"" direction)
           (kill-emacs 1)))))))
  (kill-emacs 0))

;; --- rename ---

(defun org-gtd-cli/rename (substring new-title &optional index dry-run)
  "Rename a task's heading to NEW-TITLE.
Preserves TODO state, priority, and tags."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((old-heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name))))
         (if is-dry-run
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/output
                  `((version . 1)
                    (command . "rename")
                    (heading . ,new-title)
                    (old_heading . ,old-heading)
                    (file . ,rel-file)
                    (dry_run . t)))
               (princ (format "Would rename: \"%s\" -> \"%s\" (%s)\n"
                              old-heading new-title rel-file)))
           (org-edit-headline new-title)
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1)
                  (command . "rename")
                  (heading . ,new-title)
                  (old_heading . ,old-heading)
                  (file . ,rel-file))
                buf-pos)
             (princ (format "Renamed: \"%s\" -> \"%s\" (%s)\n"
                            old-heading new-title rel-file))))))))
  (kill-emacs 0))

;; --- set-schedule ---

(defun org-gtd-cli/set-schedule (substring date-str &optional time-str clear index dry-run)
  "Set or clear the SCHEDULED date on a task."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (is-clear (and clear (not (equal clear "nil"))
                        (not (string-empty-p clear))))
         (date-str (when (and date-str (not (string-empty-p date-str))
                              (not (equal date-str "nil")))
                     date-str))
         (time-str (when (and time-str (not (string-empty-p time-str))
                              (not (equal time-str "nil")))
                     time-str))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name))))
         (cond
          (is-clear
           (if is-dry-run
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/output
                    `((version . 1)
                      (command . "set-schedule")
                      (heading . ,heading)
                      (file . ,rel-file)
                      (scheduled . :null)
                      (dry_run . t)))
                 (princ (format "Would clear schedule: \"%s\" (%s)\n"
                                heading rel-file)))
             (org-schedule '(4))
             (save-buffer)
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/mutation-output
                  `((version . 1)
                    (command . "set-schedule")
                    (heading . ,heading)
                    (file . ,rel-file)
                    (scheduled . :null))
                  buf-pos)
               (princ (format "Cleared schedule: \"%s\" (%s)\n"
                              heading rel-file)))))
          (date-str
           (let ((ts (org-gtd-cli/make-timestamp date-str time-str t)))
             (if is-dry-run
                 (if org-gtd-cli/json-mode
                     (org-gtd-cli/output
                      `((version . 1)
                        (command . "set-schedule")
                        (heading . ,heading)
                        (file . ,rel-file)
                        (scheduled . ,ts)
                        (dry_run . t)))
                   (princ (format "Would schedule: \"%s\" %s (%s)\n"
                                  heading ts rel-file)))
               (org-schedule nil ts)
               (save-buffer)
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/mutation-output
                    `((version . 1)
                      (command . "set-schedule")
                      (heading . ,heading)
                      (file . ,rel-file)
                      (scheduled . ,ts))
                    buf-pos)
                 (princ (format "Scheduled: \"%s\" %s (%s)\n"
                                heading ts rel-file))))))
          (t
           (org-gtd-cli/error "Error: provide a DATE or --clear")
           (kill-emacs 1)))))))
  (kill-emacs 0))

;; --- set-deadline ---

(defun org-gtd-cli/set-deadline (substring date-str &optional time-str clear index dry-run)
  "Set or clear the DEADLINE date on a task."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (is-clear (and clear (not (equal clear "nil"))
                        (not (string-empty-p clear))))
         (date-str (when (and date-str (not (string-empty-p date-str))
                              (not (equal date-str "nil")))
                     date-str))
         (time-str (when (and time-str (not (string-empty-p time-str))
                              (not (equal time-str "nil")))
                     time-str))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name))))
         (cond
          (is-clear
           (if is-dry-run
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/output
                    `((version . 1)
                      (command . "set-deadline")
                      (heading . ,heading)
                      (file . ,rel-file)
                      (deadline . :null)
                      (dry_run . t)))
                 (princ (format "Would clear deadline: \"%s\" (%s)\n"
                                heading rel-file)))
             (org-deadline '(4))
             (save-buffer)
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/mutation-output
                  `((version . 1)
                    (command . "set-deadline")
                    (heading . ,heading)
                    (file . ,rel-file)
                    (deadline . :null))
                  buf-pos)
               (princ (format "Cleared deadline: \"%s\" (%s)\n"
                              heading rel-file)))))
          (date-str
           (let ((ts (org-gtd-cli/make-timestamp date-str time-str t)))
             (if is-dry-run
                 (if org-gtd-cli/json-mode
                     (org-gtd-cli/output
                      `((version . 1)
                        (command . "set-deadline")
                        (heading . ,heading)
                        (file . ,rel-file)
                        (deadline . ,ts)
                        (dry_run . t)))
                   (princ (format "Would set deadline: \"%s\" %s (%s)\n"
                                  heading ts rel-file)))
               (org-deadline nil ts)
               (save-buffer)
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/mutation-output
                    `((version . 1)
                      (command . "set-deadline")
                      (heading . ,heading)
                      (file . ,rel-file)
                      (deadline . ,ts))
                    buf-pos)
                 (princ (format "Deadline: \"%s\" %s (%s)\n"
                                heading ts rel-file))))))
          (t
           (org-gtd-cli/error "Error: provide a DATE or --clear")
           (kill-emacs 1)))))))
  (kill-emacs 0))

;; --- set-priority ---

(defun org-gtd-cli/set-priority (substring priority &optional clear index dry-run)
  "Set or clear the priority on an existing task.
PRIORITY should be A, B, or C.  If CLEAR is non-nil, remove the priority cookie."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (is-clear (and clear (not (equal clear "nil"))
                        (not (string-empty-p clear))))
         (priority (when (and priority (not (string-empty-p priority))
                              (not (equal priority "nil")))
                     (upcase priority)))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    ;; Validate priority value
    (when (and (not is-clear) priority
               (not (member priority '("A" "B" "C"))))
      (org-gtd-cli/error "Error: \"%s\" is not a valid priority\nValid priorities: A, B, C"
                         priority)
      (kill-emacs 1))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (old-priority (org-gtd-cli/get-explicit-priority)))
         (cond
          (is-clear
           (if is-dry-run
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/output
                    `((version . 1)
                      (command . "set-priority")
                      (heading . ,heading)
                      (file . ,rel-file)
                      (old_priority . ,(or old-priority :null))
                      (new_priority . :null)
                      (dry_run . t)))
                 (princ (format "Would clear priority: \"%s\" (%s)\n"
                                heading rel-file)))
             (condition-case nil
                 (org-priority 'remove)
               (user-error nil))  ; no-op if no priority cookie
             (save-buffer)
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/mutation-output
                  `((version . 1)
                    (command . "set-priority")
                    (heading . ,heading)
                    (file . ,rel-file)
                    (old_priority . ,(or old-priority :null))
                    (new_priority . :null))
                  buf-pos)
               (princ (format "Cleared priority: \"%s\" (%s)\n"
                              heading rel-file)))))
          (priority
           (if is-dry-run
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/output
                    `((version . 1)
                      (command . "set-priority")
                      (heading . ,heading)
                      (file . ,rel-file)
                      (old_priority . ,(or old-priority :null))
                      (new_priority . ,priority)
                      (dry_run . t)))
                 (princ (format "Would set priority: \"%s\" [#%s] -> [#%s] (%s)\n"
                                heading (or old-priority "B") priority rel-file)))
             (org-priority (string-to-char priority))
             (save-buffer)
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/mutation-output
                  `((version . 1)
                    (command . "set-priority")
                    (heading . ,heading)
                    (file . ,rel-file)
                    (old_priority . ,(or old-priority :null))
                    (new_priority . ,priority))
                  buf-pos)
               (princ (format "Priority: \"%s\" [#%s] -> [#%s] (%s)\n"
                              heading (or old-priority "B") priority rel-file)))))
          (t
           (org-gtd-cli/error "Error: provide a PRIORITY (A, B, or C) or --clear")
           (kill-emacs 1)))))))
  (kill-emacs 0))

;; --- set-tags (replace all) ---

(defun org-gtd-cli/set-tags (substring tags-csv &optional index dry-run)
  "Replace all tags on an existing task.
TAGS-CSV is a comma-separated string of tags. Empty string clears all tags."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (new-tags (if (or (null tags-csv) (string-empty-p tags-csv)
                           (equal tags-csv "nil"))
                       '()
                     (split-string tags-csv ",")))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (old-tags (org-get-tags nil t)))
         (if is-dry-run
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/output
                  `((version . 1) (command . "set-tags")
                    (heading . ,heading) (file . ,rel-file)
                    (old_tags . ,(vconcat old-tags))
                    (new_tags . ,(vconcat new-tags))
                    (dry_run . t)))
               (princ (format "Would set tags: \"%s\" %s -> %s (%s)\n"
                              heading
                              (org-gtd-cli/format-tag-str old-tags)
                              (org-gtd-cli/format-tag-str new-tags)
                              rel-file)))
           (org-set-tags new-tags)
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1) (command . "set-tags")
                  (heading . ,heading) (file . ,rel-file)
                  (old_tags . ,(vconcat old-tags))
                  (new_tags . ,(vconcat new-tags)))
                buf-pos)
             (princ (format "Tags: \"%s\" %s -> %s (%s)\n"
                            heading
                            (org-gtd-cli/format-tag-str old-tags)
                            (org-gtd-cli/format-tag-str new-tags)
                            rel-file))))))))
  (kill-emacs 0))

;; --- add-tags (append) ---

(defun org-gtd-cli/add-tags (substring tags-csv &optional index dry-run)
  "Append tags to an existing task (no duplicates).
TAGS-CSV is a comma-separated string of tags to add."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (add-list (when (and tags-csv (not (string-empty-p tags-csv))
                              (not (equal tags-csv "nil")))
                     (split-string tags-csv ",")))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (old-tags (org-get-tags nil t))
              (new-tags (copy-sequence old-tags)))
         ;; Add tags (skip duplicates)
         (dolist (tag add-list)
           (unless (member tag new-tags)
             (setq new-tags (append new-tags (list tag)))))
         (if is-dry-run
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/output
                  `((version . 1) (command . "add-tags")
                    (heading . ,heading) (file . ,rel-file)
                    (old_tags . ,(vconcat old-tags))
                    (new_tags . ,(vconcat new-tags))
                    (dry_run . t)))
               (princ (format "Would add tags: \"%s\" %s -> %s (%s)\n"
                              heading
                              (org-gtd-cli/format-tag-str old-tags)
                              (org-gtd-cli/format-tag-str new-tags)
                              rel-file)))
           (org-set-tags new-tags)
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1) (command . "add-tags")
                  (heading . ,heading) (file . ,rel-file)
                  (old_tags . ,(vconcat old-tags))
                  (new_tags . ,(vconcat new-tags)))
                buf-pos)
             (princ (format "Tags: \"%s\" %s -> %s (%s)\n"
                            heading
                            (org-gtd-cli/format-tag-str old-tags)
                            (org-gtd-cli/format-tag-str new-tags)
                            rel-file))))))))
  (kill-emacs 0))

;; --- remove-tags ---

(defun org-gtd-cli/remove-tags (substring tags-csv &optional index dry-run)
  "Remove specific tags from an existing task.
TAGS-CSV is a comma-separated string of tags to remove."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (remove-list (when (and tags-csv (not (string-empty-p tags-csv))
                                 (not (equal tags-csv "nil")))
                        (split-string tags-csv ",")))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (old-tags (org-get-tags nil t))
              (new-tags (seq-remove (lambda (tag) (member tag remove-list)) old-tags)))
         (if is-dry-run
             (if org-gtd-cli/json-mode
                 (org-gtd-cli/output
                  `((version . 1) (command . "set-tags")
                    (heading . ,heading) (file . ,rel-file)
                    (old_tags . ,(vconcat old-tags))
                    (new_tags . ,(vconcat new-tags))
                    (dry_run . t)))
               (princ (format "Would remove tags: \"%s\" %s -> %s (%s)\n"
                              heading
                              (org-gtd-cli/format-tag-str old-tags)
                              (org-gtd-cli/format-tag-str new-tags)
                              rel-file)))
           (org-set-tags new-tags)
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/mutation-output
                `((version . 1) (command . "set-tags")
                  (heading . ,heading) (file . ,rel-file)
                  (old_tags . ,(vconcat old-tags))
                  (new_tags . ,(vconcat new-tags)))
                buf-pos)
             (princ (format "Tags: \"%s\" %s -> %s (%s)\n"
                            heading
                            (org-gtd-cli/format-tag-str old-tags)
                            (org-gtd-cli/format-tag-str new-tags)
                            rel-file))))))))
  (kill-emacs 0))

(defun org-gtd-cli/format-tag-str (tags)
  "Format TAGS list as :tag1:tag2: string, or empty string if nil."
  (if tags (concat ":" (mapconcat #'identity tags ":") ":") ""))

;; --- set-property (generic property writer) ---

(defconst org-gtd-cli/reserved-properties
  '("SCHEDULED" "DEADLINE" "CLOSED" "CLOCK" "TODO" "PRIORITY"
    "TAGS" "ALLTAGS" "CATEGORY" "ITEM" "BLOCKED" "FILE")
  "Property names that have dedicated commands or special org semantics.
`set-property' refuses to write these to avoid corrupting task state;
use the dedicated command (e.g. set-schedule, set-deadline, set-tags) instead.")

(defconst org-gtd-cli/property-enums
  '(("AGENT_EFFORT" . ("light" "standard" "deep"))
    ("RUN_STATUS" . ("RUNNING" "BLOCKED" "REVIEW" "DONE" "FAILED")))
  "Alist of upcased PROPERTY-NAME -> list of allowed (canonical) values.
`set-property' rejects out-of-enum values for these keys (case-insensitive
match) and stores the canonical form. This keeps the writer generic while
giving known enum properties value-level validation. AGENT_EFFORT is the
per-task model-tier hint on @agent leaf tasks (light/standard/deep); the
tier->model mapping is deferred to the consuming SKILL — see
~/org/CLAUDE.md. RUN_STATUS is the agent-orchestration job state
machine value on VPA-owned @agent job tasks (canonical uppercase); see the
\"Job state machine\" section of notes/decisions/agent-orchestration.md.")

(defun org-gtd-cli/set-property (substring key value &optional clear index dry-run)
  "Set or clear a single org PROPERTY on an existing task.
KEY is the property name (e.g. \"AGENT_EFFORT\").  When CLEAR is non-nil
the property is removed; otherwise it is set to VALUE.  This is a generic
writer; the only value-level validation is for keys listed in
`org-gtd-cli/property-enums' (e.g. AGENT_EFFORT), whose values are checked
against the allowed set and normalized to canonical form.  Any other
per-property validation belongs in the calling command."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (is-clear (and clear (not (equal clear "nil"))
                        (not (string-empty-p clear))))
         (key (when (and key (not (string-empty-p key)) (not (equal key "nil")))
                key))
         (value (when (and value (not (string-empty-p value))
                           (not (equal value "nil")))
                  value)))
    ;; Validate KEY
    (unless key
      (org-gtd-cli/error "Error: a property KEY is required (use --key NAME)")
      (kill-emacs 1))
    (when (member (upcase key) org-gtd-cli/reserved-properties)
      (org-gtd-cli/error
       (concat "Error: \"%s\" is a reserved property with dedicated commands\n"
               "Use set-schedule/set-deadline/set-tags/set-state/etc. instead.")
       key)
      (kill-emacs 1))
    ;; Require a VALUE unless clearing
    (when (and (not is-clear) (not value))
      (org-gtd-cli/error "Error: provide --value VALUE (or --clear to remove)")
      (kill-emacs 1))
    ;; Enum validation for known properties (keeps the writer otherwise generic).
    ;; Match case-insensitively and normalize to the canonical value.
    (when (and (not is-clear) value)
      (let ((allowed (cdr (assoc (upcase key) org-gtd-cli/property-enums))))
        (when allowed
          (let ((canonical (cl-find value allowed :test #'cl-equalp)))
            (if canonical
                (setq value canonical)
              (org-gtd-cli/error
               "Error: invalid value \"%s\" for %s; allowed: %s"
               value (upcase key) (mapconcat #'identity allowed ", "))
              (kill-emacs 1))))))
    (let ((buf-pos (org-gtd-cli/find-task substring idx t)))
      (with-current-buffer (car buf-pos)
        (org-with-wide-buffer
         (goto-char (cdr buf-pos))
         (let* ((heading (org-get-heading t t t t))
                (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
                (old-value (org-entry-get nil key)))
           (cond
            (is-clear
             (if is-dry-run
                 (if org-gtd-cli/json-mode
                     (org-gtd-cli/output
                      `((version . 1) (command . "set-property")
                        (heading . ,heading) (file . ,rel-file)
                        (key . ,key)
                        (old_value . ,(or old-value :null))
                        (new_value . :null)
                        (dry_run . t)))
                   (princ (format "Would clear property %s: \"%s\" (%s)\n"
                                  key heading rel-file)))
               (org-entry-delete nil key)
               (save-buffer)
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/mutation-output
                    `((version . 1) (command . "set-property")
                      (heading . ,heading) (file . ,rel-file)
                      (key . ,key)
                      (old_value . ,(or old-value :null))
                      (new_value . :null))
                    buf-pos)
                 (princ (format "Cleared property %s: \"%s\" (%s)\n"
                                key heading rel-file)))))
            (t
             (if is-dry-run
                 (if org-gtd-cli/json-mode
                     (org-gtd-cli/output
                      `((version . 1) (command . "set-property")
                        (heading . ,heading) (file . ,rel-file)
                        (key . ,key)
                        (old_value . ,(or old-value :null))
                        (new_value . ,value)
                        (dry_run . t)))
                   (princ (format "Would set property %s: \"%s\" %s -> %s (%s)\n"
                                  key heading (or old-value "(unset)")
                                  value rel-file)))
               (org-entry-put nil key value)
               (save-buffer)
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/mutation-output
                    `((version . 1) (command . "set-property")
                      (heading . ,heading) (file . ,rel-file)
                      (key . ,key)
                      (old_value . ,(or old-value :null))
                      (new_value . ,value))
                    buf-pos)
                 (princ (format "Property %s: \"%s\" %s -> %s (%s)\n"
                                key heading (or old-value "(unset)")
                                value rel-file)))))))))))
  (kill-emacs 0))

;; --- archive helpers (delegated to gtd-core.el shared functions) ---

(defalias 'org-gtd-cli/subtree-has-recent-dates-p #'gtd/subtree-has-recent-dates-p)
(defalias 'org-gtd-cli/subtree-has-any-dates-p #'gtd/subtree-has-any-dates-p)
(defalias 'org-gtd-cli/inside-active-project-p #'gtd/inside-active-project-p)

;; --- archive (single task) ---

(defun org-gtd-cli/archive (substring &optional index dry-run)
  "Archive a single completed task."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (state (org-get-todo-state)))
         ;; Rule 1: must be done
         (unless (member state org-done-keywords)
           (org-gtd-cli/error "Not archivable: \"%s\" is still active (%s) (%s)"
                             heading state rel-file)
           (kill-emacs 1))
         ;; Rule 2b: no recent dates
         (when (org-gtd-cli/subtree-has-recent-dates-p)
           (org-gtd-cli/error "Not archivable: \"%s\" has recent dates (%s)"
                              heading rel-file)
           (kill-emacs 1))
         ;; Rule 3: not inside active project
         (let ((active-parent (org-gtd-cli/inside-active-project-p)))
           (when active-parent
             (org-gtd-cli/error "Not archivable: \"%s\" is inside active project \"%s\" (%s)"
                                heading active-parent rel-file)
             (kill-emacs 1)))
         ;; All checks passed
         (if is-dry-run
             (progn
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/output
                    `((version . 1) (command . "archive")
                      (heading . ,heading) (file . ,rel-file) (dry_run . t)))
                 (princ (format "Would archive: \"%s\" (%s)\n" heading rel-file)))
               (kill-emacs 0))
           ;; Archive
           (org-archive-subtree)
           ;; Save all modified buffers (source + archive)
           (dolist (buf (buffer-list))
             (when (and (buffer-file-name buf)
                        (buffer-modified-p buf))
               (with-current-buffer buf (save-buffer))))
           (if org-gtd-cli/json-mode
               (org-gtd-cli/output
                `((version . 1) (command . "archive")
                  (heading . ,heading) (file . ,rel-file)))
             (princ (format "Archived: \"%s\" (%s)\n" heading rel-file))))))))
  (kill-emacs 0))

;; --- archive-all (batch) ---

(defun org-gtd-cli/archive-all (&optional dry-run)
  "Archive all eligible completed tasks across agenda files."
  (let* ((is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (candidates '())
         (archived 0)
         (skipped 0))
    ;; Collect all DONE/CANCELLED headings
    (dolist (file (org-agenda-files))
      (when (file-exists-p file)
        (with-current-buffer (find-file-noselect file)
          (org-with-wide-buffer
           (goto-char (point-min))
           (while (re-search-forward org-heading-regexp nil t)
             (let ((state (org-get-todo-state)))
               (when (and state (member state org-done-keywords))
                 (let* ((heading (org-get-heading t t t t))
                        (pos (line-beginning-position))
                        (rel-file (org-gtd-cli/relative-filename file)))
                   (push (list (current-buffer) pos heading rel-file)
                         candidates)))))))))
    (setq candidates (nreverse candidates))
    ;; Filter candidates
    (let ((archivable '()))
      (dolist (cand candidates)
        (cl-destructuring-bind (buf pos heading rel-file) cand
          (with-current-buffer buf
            (org-with-wide-buffer
             (goto-char pos)
             (cond
              ;; Rule 3: inside active project → skip silently
              ((org-gtd-cli/inside-active-project-p)
               (cl-incf skipped))
              ;; Rule 2b: recent dates → skip silently
              ((org-gtd-cli/subtree-has-recent-dates-p)
               (cl-incf skipped))
              ;; Rule 2a: no dates at all → skip with message
              ((not (org-gtd-cli/subtree-has-any-dates-p))
               (cl-incf skipped)
               (org-gtd-cli/error "Skipped (no dates): \"%s\" (%s)"
                                  heading rel-file))
              ;; All rules pass
              (t
               (push (list buf pos heading rel-file) archivable)))))))
      (setq archivable (nreverse archivable))
      (if (null archivable)
          (progn
            (when (> skipped 0)
              (org-gtd-cli/error "%d tasks skipped" skipped))
            (org-gtd-cli/error "No archivable tasks found")
            (kill-emacs 0))
        ;; Sort: within each file, process bottom-up (highest position first)
        ;; Group by buffer, reverse position order within each group
        (let ((by-buffer (make-hash-table :test 'eq)))
          (dolist (item archivable)
            (let ((buf (nth 0 item)))
              (puthash buf (cons item (gethash buf by-buffer)) by-buffer)))
          ;; Each buffer's list is already reversed (highest pos first) due to cons
          ;; Flatten back to a single list
          (setq archivable '())
          (maphash (lambda (_buf items) (setq archivable (append items archivable)))
                   by-buffer))
        (let ((archived-items '()))
          (dolist (item archivable)
            (cl-destructuring-bind (buf pos heading rel-file) item
              (if is-dry-run
                  (progn
                    (push (list heading rel-file) archived-items)
                    (cl-incf archived))
                (with-current-buffer buf
                  (org-with-wide-buffer
                   (goto-char pos)
                   (org-back-to-heading t)
                   (org-archive-subtree)
                   (push (list heading rel-file) archived-items)
                   (cl-incf archived))))))
          ;; Save all modified buffers
          (unless is-dry-run
            (dolist (buf (buffer-list))
              (when (and (buffer-file-name buf)
                         (buffer-modified-p buf))
                (with-current-buffer buf (save-buffer)))))
          (if org-gtd-cli/json-mode
              (let ((items '()))
                (dolist (a (nreverse archived-items))
                  (push `((heading . ,(nth 0 a)) (file . ,(nth 1 a))) items))
                (org-gtd-cli/output
                 `((version . 1) (command . "archive")
                   (archived . ,(apply #'vector (nreverse items)))
                   (skipped . ,skipped) (count . ,archived)
                   ,@(when is-dry-run '((dry_run . t))))))
            (princ (format "%s %d tasks, %d skipped\n"
                           (if is-dry-run "Would archive" "Archived")
                           archived skipped)))))))
  (kill-emacs 0))

;; --- delete ---

(defun org-gtd-cli/delete (heading &optional index dry-run)
  "Delete a task by exact heading match.
HEADING must match the full heading text (case-insensitive).
Refuses to delete projects (tasks with subtasks)."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (buf-pos (org-gtd-cli/find-task heading idx t t)))  ;; include-done=t, exact=t
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((task-heading (org-get-heading t t t t))
              (task-state (org-get-todo-state))
              (task-id (org-entry-get nil "ID"))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (level (org-current-level))
              (child-level (1+ level))
              (subtree-end (save-excursion (org-end-of-subtree t) (point)))
              (has-children nil))
         (unless task-state
           (org-gtd-cli/error
            (concat "Cannot delete: \"%s\" is not a task (no TODO keyword) "
                    "- refusing to delete a plain/category heading (%s)")
            task-heading rel-file)
           (kill-emacs 1))
         ;; Check for child headings (project detection)
         (save-excursion
           (forward-line 1)
           (while (and (not has-children) (< (point) subtree-end)
                       (re-search-forward org-heading-regexp subtree-end t))
             (when (= (org-current-level) child-level)
               (setq has-children t))))
         (when has-children
           (org-gtd-cli/error "Cannot delete: \"%s\" is a project with subtasks (%s)"
                              task-heading rel-file)
           (kill-emacs 1))
         (if is-dry-run
             (progn
               (if org-gtd-cli/json-mode
                   (org-gtd-cli/output
                    `((version . 1) (command . "delete")
                      (heading . ,task-heading) (file . ,rel-file) (dry_run . t)))
                 (princ (format "Would delete: \"%s\" (%s)\n" task-heading rel-file)))
               (kill-emacs 0))
           (org-cut-subtree)
           (save-buffer)
           (if org-gtd-cli/json-mode
               (org-gtd-cli/output
                `((version . 1) (command . "delete")
                  (id . ,task-id)
                  (heading . ,task-heading) (file . ,rel-file)
                  (side_effects . [])))
             (princ (format "Deleted: \"%s\" (%s)\n" task-heading rel-file)))))))
    (kill-emacs 0)))

;; ══════════════════════════════════════════════════════════════════════════════
;; Agenda view (uses org-agenda custom commands from gtd-core.el)
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/agenda-task-alist-at-marker (marker)
  "Build a task alist for the heading at MARKER (an agenda org-hd-marker).
Returns an alist matching the per-task schema emitted by the `agenda'
command (heading, state, priority, tags, file, scheduled, deadline,
parent, is_project, properties), plus `body' when `--full' is set."
  (let ((src-buf (marker-buffer marker)))
    (with-current-buffer src-buf
      (org-with-wide-buffer
       (goto-char (marker-position marker))
       (let* ((state (org-get-todo-state))
              (heading (org-get-heading t t t t))
              (priority-char (org-gtd-cli/get-explicit-priority))
              (tags (org-get-tags))
              (id (org-entry-get nil "ID"))
              (scheduled (org-entry-get nil "SCHEDULED"))
              (deadline (org-entry-get nil "DEADLINE"))
              (src-file (buffer-file-name))
              (rel-file (if src-file
                            (org-gtd-cli/relative-filename src-file)
                          "?"))
              (parent-heading (save-excursion
                                (if (org-up-heading-safe)
                                    (org-get-heading t t t t)
                                  nil)))
              (is-project (org-gtd-cli/has-todo-children-p))
              (task `((heading . ,heading)
                      (state . ,(or state :null))
                      (priority . ,(or priority-char :null))
                      (tags . ,(vconcat (mapcar #'identity tags)))
                      (id . ,(or id :null))
                      (file . ,rel-file)
                      (scheduled . ,(or scheduled :null))
                      (deadline . ,(or deadline :null))
                      (parent . ,(or parent-heading :null))
                      (is_project . ,(if is-project t :false))
                      (properties . ,(org-gtd-cli/properties-at-point)))))
         (when org-gtd-cli/full-mode
           (setq task (append task
                              `((body . ,(or (org-gtd-cli/get-body-at-point)
                                             :null))))))
         task)))))

(defun org-gtd-cli/agenda-view-json (cmd-key)
  "Emit the agenda view for CMD-KEY as JSON, grouped into blocks.
Each block carries its overriding header name and the task entries beneath
it (built with `org-gtd-cli/agenda-task-alist-at-marker').  Header lines in
the agenda buffer are detected via the `org-agenda-structural-header' text
property that org sets on each block header.  The `agenda' day section (the
leading dated block of the \" \" view) has no overriding header; its tasks
accumulate under a block named \"Agenda\"."
  (let ((blocks '())
        (cur-name "Agenda")
        (cur-tasks '()))
    (cl-flet ((flush ()
                (when (or cur-tasks (not (equal cur-name "Agenda")))
                  (push `((name . ,cur-name)
                          (count . ,(length cur-tasks))
                          (tasks . ,(apply #'vector (nreverse cur-tasks))))
                        blocks))))
      (with-current-buffer org-agenda-buffer
        (goto-char (point-min))
        (while (not (eobp))
          (let ((marker (or (get-text-property (point) 'org-hd-marker)
                            (get-text-property (point) 'org-marker)))
                (is-header (get-text-property (point) 'org-agenda-structural-header)))
            (cond
             (marker
              (push (org-gtd-cli/agenda-task-alist-at-marker marker) cur-tasks))
             (is-header
              ;; A new block header starts here: flush the previous block,
              ;; then begin a new one named after this header line.
              (flush)
              (setq cur-tasks '())
              (setq cur-name
                    (string-trim
                     (buffer-substring-no-properties
                      (line-beginning-position) (line-end-position)))))))
          (forward-line 1))
        (flush)))
    (org-gtd-cli/output
     `((version . 1)
       (command . "agenda-view")
       (key . ,cmd-key)
       (blocks . ,(apply #'vector (nreverse blocks)))))))

(defun org-gtd-cli/agenda-view (&optional key date)
  "Run an org-agenda custom command in batch mode.
KEY defaults to \" \" (the full GTD dashboard).
DATE, when non-nil, is a \"YYYY-MM-DD\" string that pages the dated
\"Agenda\" block to that day via `org-agenda-start-day'.
Task lines include (file) for source identification.
In JSON mode, emits structured blocks via `org-gtd-cli/agenda-view-json'."
  (let ((cmd-key (or key " ")))
    (unless (assoc cmd-key org-agenda-custom-commands)
      (org-gtd-cli/error "Unknown agenda view key: \"%s\"\nAvailable views:" cmd-key)
      (dolist (cmd org-agenda-custom-commands)
        (when (stringp (car cmd))
          (org-gtd-cli/error "  \"%s\"  %s" (car cmd) (or (nth 1 cmd) ""))))
      (kill-emacs 1))
    ;; Build the agenda buffer
    (let ((org-agenda-window-setup 'current-window)
          (org-agenda-start-day (or date org-agenda-start-day)))
      (org-agenda nil cmd-key))
    (if org-gtd-cli/json-mode
        (org-gtd-cli/agenda-view-json cmd-key)
      ;; Walk the buffer and print with (file) suffixes on task lines
      (with-current-buffer org-agenda-buffer
        (goto-char (point-min))
        (while (not (eobp))
          (let* ((marker (or (get-text-property (point) 'org-hd-marker)
                             (get-text-property (point) 'org-marker)))
                 (line-text (buffer-substring-no-properties
                             (line-beginning-position) (line-end-position))))
            (if marker
                (let* ((src-buf (marker-buffer marker))
                       (src-file (and src-buf (buffer-file-name src-buf))))
                  (princ (format "%s (%s)\n"
                                 line-text
                                 (if src-file
                                     (org-gtd-cli/relative-filename src-file)
                                   "?"))))
              (princ (format "%s\n" line-text))))
          (forward-line 1))))
    (kill-emacs 0)))

;; ══════════════════════════════════════════════════════════════════════════════
;; Batch mode
;; ══════════════════════════════════════════════════════════════════════════════

(define-error 'org-gtd-cli/batch-item-error "Batch item failed" 'error)

(defun org-gtd-cli/batch--parse-items (json-str)
  "Parse JSON-STR as the batch input array, or error out loudly.
Returns a list of items.  Invalid JSON or a non-array input emits an
error (JSON error object in json-mode) and exits 1."
  (let ((parsed (condition-case err
                    (json-parse-string json-str :object-type 'alist :array-type 'array)
                  (json-parse-error
                   (org-gtd-cli/error "Error: invalid JSON input: %s" (error-message-string err))
                   (kill-emacs 1)))))
    ;; A top-level JSON object also parses to a list with :array-type 'list,
    ;; so parse arrays as vectors to tell the two apart reliably.
    (unless (vectorp parsed)
      (org-gtd-cli/error "Error: expected a JSON array of batch items")
      (kill-emacs 1))
    (append parsed nil)))

(defun org-gtd-cli/batch--run-item (idx thunk)
  "Run THUNK (one batch item) with error isolation.
Intercepts `kill-emacs' and `message' so a failing item can neither kill
the batch process nor leak output, and converts failures into per-item
error result alists.  Returns (RESULT . SUCCESS-P)."
  (let ((item-error nil)
        (item-exit-code nil))
    (cl-letf (((symbol-function 'kill-emacs)
               (lambda (&optional code)
                 (setq item-exit-code (or code 0))
                 (throw 'org-gtd-cli-batch-item nil)))
              ((symbol-function 'message)
               (lambda (fmt &rest args)
                 (when fmt
                   (setq item-error (apply #'format fmt args))))))
      (condition-case err
          (let ((result (catch 'org-gtd-cli-batch-item
                          (funcall thunk))))
            (cond
             (result
              ;; The thunk returned a result alist directly
              (cons result t))
             ((and item-exit-code (> item-exit-code 0))
              ;; kill-emacs with non-zero = failure
              (cons `((index . ,idx) (success . :false)
                      (error . ,(or item-error "Command failed")))
                    nil))
             (t
              ;; kill-emacs 0 but no result alist — shouldn't happen now
              ;; that batch-one delegates (the delegate shim intercepts
              ;; kill-emacs), but keep as a success fallback.
              (cons `((index . ,idx) (success . t)) t))))
        ;; A delegated command that failed with a {error, hint} payload —
        ;; carry the hint through so batch results keep the per-item hint
        ;; single commands return (see `org-gtd-cli/batch--delegate').
        (org-gtd-cli/batch-item-error
         (let ((msg (nth 1 err))
               (hint (nth 2 err)))
           (cons `((index . ,idx) (success . :false)
                   (error . ,msg)
                   ,@(when hint `((hint . ,hint))))
                 nil)))
        (error
         (cons `((index . ,idx) (success . :false)
                 (error . ,(error-message-string err)))
               nil))))))

(defun org-gtd-cli/batch--output (command results succeeded failed)
  "Emit the batch JSON response for COMMAND and exit.
RESULTS is the reversed per-item result list; SUCCEEDED/FAILED are
counts.  Exits 0 when at least one item succeeded, 1 otherwise."
  (org-gtd-cli/output
   `((version . 1)
     (command . ,command)
     (batch . t)
     (results . ,(apply #'vector (nreverse results)))
     (summary . ((total . ,(+ succeeded failed))
                 (succeeded . ,succeeded)
                 (failed . ,failed)))))
  (kill-emacs (if (> succeeded 0) 0 1)))

(defun org-gtd-cli/batch (command json-str &optional shared-arg)
  "Execute COMMAND in batch mode over items in JSON-STR.
SHARED-ARG is a shared parameter (e.g. parent heading for add-subtask,
category for refile).  JSON-STR is a JSON array.

Outputs a JSON batch response with per-item results."
  (let ((items (org-gtd-cli/batch--parse-items json-str))
        (results '())
        (succeeded 0)
        (failed 0)
        (idx 0))
    (dolist (item items)
      (pcase-let ((`(,result . ,ok)
                   (org-gtd-cli/batch--run-item
                    idx (lambda ()
                          (org-gtd-cli/batch-one command item shared-arg idx)))))
        (push result results)
        (if ok (cl-incf succeeded) (cl-incf failed)))
      (cl-incf idx))
    (org-gtd-cli/batch--output command results succeeded failed)))

(defun org-gtd-cli/batch-mixed (json-str)
  "Execute heterogeneous batch items from JSON-STR (the `batch' subcommand).
JSON-STR is a JSON array of {\"command\": NAME, \"args\": {...}} objects.
Each item's command is validated and dispatched through
`org-gtd-cli/batch-one', with the same per-item error isolation as
`org-gtd-cli/batch' — an unknown command or failing item yields a
per-item error result without aborting the rest.

Commands that take a shared argument in homogeneous batch mode carry it
per item here: add-subtask items take \"parent\" in args, refile items
take \"category\" in args.

Outputs a JSON batch response with per-item results."
  (let ((items (org-gtd-cli/batch--parse-items json-str))
        (results '())
        (succeeded 0)
        (failed 0)
        (idx 0))
    (dolist (item items)
      (pcase-let ((`(,result . ,ok)
                   (org-gtd-cli/batch--run-item
                    idx
                    (lambda ()
                      (let ((command (org-gtd-cli/batch--field item 'command))
                            (item-args (org-gtd-cli/batch--field item 'args)))
                        (unless (stringp command)
                          (error "Missing required field: command"))
                        (unless (listp item-args)
                          (error "Field \"args\" must be a JSON object"))
                        (let ((shared-arg
                               (pcase command
                                 ("add-subtask"
                                  ;; parent heading OR parent_id (:ID:) — the
                                  ;; id, when present, is read per item in
                                  ;; `org-gtd-cli/batch-one'.
                                  (let ((parent (org-gtd-cli/batch--field
                                                 item-args 'parent))
                                        (pid (org-gtd-cli/batch--field
                                              item-args 'parent_id 'parent-id)))
                                    (unless (or parent pid)
                                      (error "Missing required field: parent (or parent_id)"))
                                    parent))
                                 ("refile"
                                  ;; --category (shared) OR a per-item --to
                                  ;; target, read in `org-gtd-cli/batch-one'.
                                  (let ((cat (org-gtd-cli/batch--field
                                              item-args 'category))
                                        (to (org-gtd-cli/batch--field
                                             item-args 'to)))
                                    (unless (or cat to)
                                      (error "Missing required field: category (or to)"))
                                    cat))
                                 (_ nil))))
                          (org-gtd-cli/batch-one command item-args
                                                 shared-arg idx)))))))
        (push result results)
        (if ok (cl-incf succeeded) (cl-incf failed)))
      (cl-incf idx))
    (org-gtd-cli/batch--output "batch" results succeeded failed)))

(defun org-gtd-cli/batch--field (item &rest keys)
  "Return the first present field among KEYS in batch ITEM.
ITEM is either a bare string — returned as-is when KEYS includes
`heading' — or an alist parsed from a JSON object.  JSON null values
count as absent.  Returns nil when no key is present."
  (if (stringp item)
      (when (memq 'heading keys) item)
    (let (val)
      (while (and keys (null val))
        (let ((v (alist-get (pop keys) item)))
          (unless (eq v :null)
            (setq val v))))
      val)))

(defun org-gtd-cli/batch--required (item name &rest keys)
  "Like `org-gtd-cli/batch--field' but signal an error when absent.
NAME is the field name used in the error message."
  (or (apply #'org-gtd-cli/batch--field item keys)
      (error "Missing required field: %s" name)))

(defun org-gtd-cli/batch--addr (item &rest keys)
  "Return ITEM's heading substring for a task-addressing command.
KEYS default to (heading).  When ITEM carries an `id' field the task is
resolved by org :ID: (see `org-gtd-cli/batch--with-addr'), so this
returns nil — `org-gtd-cli/find-task' ignores its substring argument
under a bound `org-gtd-cli/forced-id'.  Signals when ITEM provides
neither an id nor a heading key."
  (let ((id (org-gtd-cli/batch--field item 'id))
        (heading (apply #'org-gtd-cli/batch--field item (or keys '(heading)))))
    (cond (id nil)
          (heading heading)
          (t (error "Missing required field: heading (or id)")))))

(defun org-gtd-cli/batch--flag (item &rest keys)
  "Return \"t\" when ITEM's first present flag among KEYS is JSON true.
JSON false parses to `:false' and true to `t' (see
`org-gtd-cli/batch--parse-items'); the individual command impls expect
the \"t\"/nil string convention the Python layer emits.  JSON
false/null and an absent key all yield nil."
  (let ((v (apply #'org-gtd-cli/batch--field item keys)))
    (and (eq v t) "t")))

(defmacro org-gtd-cli/batch--with-addr (item mutation &rest body)
  "Bind id-addressing dynamic vars from ITEM around BODY.
When ITEM has an `id' field, `org-gtd-cli/forced-id' is bound so the
delegated command resolves the task by org :ID: instead of substring —
matching the individual commands' `--id' semantics.  MUTATION non-nil
enables lazy id creation on the resolved task (a no-op when it already
has an id, and irrelevant when addressing by substring)."
  (declare (indent 2))
  `(let ((org-gtd-cli/forced-id (org-gtd-cli/batch--field ,item 'id))
         (org-gtd-cli/forced-create-id (and ,mutation t)))
     ,@body))

(defun org-gtd-cli/batch--json-error-obj (s)
  "If S parses as a JSON object with an `error' field, return (ERROR . HINT).
The real commands emit errors as {\"error\": ..., \"hint\": ...} JSON in
json-mode; this recovers the error text and its accompanying hint (nil when
absent) for batch per-item results.  Returns nil when S is not such an object."
  (condition-case nil
      (let* ((obj (json-parse-string (string-trim s)
                                     :object-type 'alist
                                     :array-type 'array))
             (err (and (consp obj) (alist-get 'error obj))))
        (when (stringp err)
          (let ((hint (alist-get 'hint obj)))
            (cons err (and (stringp hint) hint)))))
    (error nil)))

(defun org-gtd-cli/batch--json-error (s)
  "If S parses as a JSON object with an `error' field, return that field.
See `org-gtd-cli/batch--json-error-obj' for the hint-bearing variant."
  (car (org-gtd-cli/batch--json-error-obj s)))

(defun org-gtd-cli/batch--delegate (fn &rest args)
  "Call the real command implementation FN with ARGS, capturing its output.
Forces `org-gtd-cli/json-mode' on so FN emits JSON, captures stdout by
rebinding `standard-output' (same shim style as `daemon-dispatch'), and
intercepts `kill-emacs' and `message' so a single item can neither kill
the batch process nor leak output.

On success (FN returned normally or exited 0) returns FN's JSON payload
parsed as an alist (arrays as vectors, so the payload re-serializes
cleanly into the batch response), or nil when FN printed nothing.  On
failure (non-zero exit) signals `error' with the message FN reported."
  (let ((exit-code nil)
        (stderr-msgs '())
        (payload "")
        ;; Capture into a dedicated buffer bound to `standard-output', but do
        ;; NOT make it current: `agenda-view' runs `org-agenda' with
        ;; window-setup `current-window, which fills the *current* buffer with
        ;; the (propertized) agenda listing.  A `with-temp-buffer' capture
        ;; buffer would be that current buffer, so the read's JSON ends up
        ;; appended after raw agenda text and fails to parse.
        (cap (generate-new-buffer " *org-gtd-cli-batch-delegate*")))
    (unwind-protect
        (progn
          (let ((standard-output cap)
                (org-gtd-cli/json-mode t))
            (cl-letf (((symbol-function 'kill-emacs)
                       (lambda (&optional code)
                         (setq exit-code (or code 0))
                         (throw 'org-gtd-cli--delegate nil)))
                      ((symbol-function 'message)
                       (lambda (fmt &rest margs)
                         ;; Return the formatted string like the real `message'
                         ;; does — some callers rely on the return value, so a
                         ;; nil-returning stub can corrupt them.
                         (when fmt
                           (let ((s (apply #'format fmt margs)))
                             (push s stderr-msgs)
                             s)))))
              (catch 'org-gtd-cli--delegate
                (apply fn args))))
          (setq payload (string-trim (with-current-buffer cap (buffer-string)))))
      (kill-buffer cap))
    (if (and exit-code (> exit-code 0))
        ;; Failure.  In json-mode the error object ({"error": ..., "hint": ...})
        ;; is written to stdout, so it lands in PAYLOAD; opaque diagnostics may
        ;; also arrive via `message' (STDERR-MSGS).  Recover both the error text
        ;; and its `hint' so the per-item result keeps the hint single commands
        ;; return (signalled via `org-gtd-cli/batch-item-error').
        (let* ((msgs (nreverse stderr-msgs))
               (texts (delq nil
                            (mapcar (lambda (m)
                                      (or (org-gtd-cli/batch--json-error m) m))
                                    msgs)))
               (hint nil))
          (dolist (m msgs)
            (let ((obj (org-gtd-cli/batch--json-error-obj m)))
              (when (and obj (cdr obj) (null hint))
                (setq hint (cdr obj)))))
          (when (and (null texts) (not (string-empty-p payload)))
            (let ((obj (org-gtd-cli/batch--json-error-obj payload)))
              (setq texts (list (or (car obj) payload)))
              (when (and obj (cdr obj)) (setq hint (cdr obj)))))
          (let ((msg (if texts (mapconcat #'identity texts "; ") "Command failed")))
            (signal 'org-gtd-cli/batch-item-error (list msg hint))))
      (unless (string-empty-p payload)
        (json-parse-string payload :object-type 'alist :array-type 'array)))))

(defun org-gtd-cli/batch--result (idx payload &optional extra)
  "Build a per-item batch result from a delegated command's PAYLOAD.
Strips PAYLOAD's version/command wrapper and prepends the batch
bookkeeping fields (index, success) plus EXTRA fields."
  (append `((index . ,idx) (success . t))
          extra
          (cl-remove-if (lambda (kv) (memq (car-safe kv) '(version command)))
                        payload)))

(defun org-gtd-cli/batch-one (command item shared-arg idx)
  "Execute one batch ITEM for COMMAND.  Returns a per-item result alist.
Delegates to the real command implementation via
`org-gtd-cli/batch--delegate', so batch items get behavior identical to
single CLI calls (validation, auto-progress, side effects, full JSON
fields).  SHARED-ARG is the command-specific shared parameter (parent
heading for add-subtask, category for refile).  IDX is the 0-based
index in the batch array."
  (pcase command
    ("set-done"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/set-done
             (org-gtd-cli/batch--addr item)))))

    ("set-state"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/set-state
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--required item "state" 'state)))))

    ("set-cancelled"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/set-state
             (org-gtd-cli/batch--addr item)
             "CANCELLED"))))

    ("set-next"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/set-next
             (org-gtd-cli/batch--addr item)))))

    ("set-priority"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/set-priority
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--field item 'priority)
             (org-gtd-cli/batch--flag item 'clear)))))

    ("rename"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/rename
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--required item "title" 'title 'new_title 'newtitle)))))

    ("move"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/move
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--required item "direction" 'direction)
             (org-gtd-cli/batch--field item 'sibling)))))

    ("set-schedule"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/set-schedule
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--field item 'date)
             (org-gtd-cli/batch--field item 'time)
             (org-gtd-cli/batch--flag item 'clear)))))

    ("set-deadline"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/set-deadline
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--field item 'date)
             (org-gtd-cli/batch--field item 'time)
             (org-gtd-cli/batch--flag item 'clear)))))

    ("set-property"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/set-property
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--required item "key" 'key)
             (org-gtd-cli/batch--field item 'value)
             (org-gtd-cli/batch--flag item 'clear)))))

    ("set-body"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/set-body
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--required item "text" 'text 'body)))))

    ("append-body"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/append-body
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--required item "text" 'text 'body)))))

    ("agenda-view"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--delegate
           #'org-gtd-cli/agenda-view
           (org-gtd-cli/batch--field item 'key)
           (org-gtd-cli/batch--field item 'date))))

    ("outline"
     (org-gtd-cli/batch--result
      idx (let ((org-gtd-cli/full-mode (or org-gtd-cli/full-mode
                                           (and (org-gtd-cli/batch--flag item 'full)
                                                t))))
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/outline
             (org-gtd-cli/batch--field item 'file)))))

    ("categories"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--delegate
           #'org-gtd-cli/categories
           (org-gtd-cli/batch--field item 'file))))

    ("delete"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/delete
             (org-gtd-cli/batch--addr item)))))

    ("refile"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/refile
             (org-gtd-cli/batch--addr item)
             ;; --to target (exact heading) takes precedence over the shared
             ;; --category, matching the single `refile' command.
             (org-gtd-cli/batch--field item 'to)
             shared-arg))))

    ("add-subtask"
     ;; The parent is addressed by SHARED-ARG (heading substring) or, when the
     ;; item carries `parent_id', by org :ID: — `add-subtask' resolves its
     ;; parent through `find-task', which honors a bound `forced-id'.
     (org-gtd-cli/batch--result
      idx (let ((org-gtd-cli/forced-id
                 (org-gtd-cli/batch--field item 'parent_id 'parent-id)))
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/add-subtask
             shared-arg
             (org-gtd-cli/batch--required item "title" 'title)
             (org-gtd-cli/batch--field item 'body)
             (org-gtd-cli/batch--field item 'tags)
             (org-gtd-cli/batch--field item 'schedule)
             (org-gtd-cli/batch--field item 'deadline)
             (org-gtd-cli/batch--field item 'priority)
             (org-gtd-cli/batch--field item 'state)))))

    ("add-task"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--delegate
           #'org-gtd-cli/add-task
           (org-gtd-cli/batch--required item "title" 'title)
           (org-gtd-cli/batch--field item 'body)
           (org-gtd-cli/batch--field item 'tags)
           (org-gtd-cli/batch--field item 'schedule)
           (org-gtd-cli/batch--field item 'deadline)
           (org-gtd-cli/batch--field item 'priority)
           (org-gtd-cli/batch--field item 'file)
           (org-gtd-cli/batch--field item 'category)
           (org-gtd-cli/batch--field item 'state)
           (org-gtd-cli/batch--field item 'time))))

    ("add-event"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--delegate
           #'org-gtd-cli/add-event
           (org-gtd-cli/batch--required item "title" 'title)
           (org-gtd-cli/batch--required item "date" 'date)
           (org-gtd-cli/batch--field item 'time)
           (org-gtd-cli/batch--field item 'tag)
           (org-gtd-cli/batch--field item 'file)
           (org-gtd-cli/batch--field item 'end-date 'end_date))))

    ("add-session-id"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item t
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/add-session-id
             (org-gtd-cli/batch--addr item)
             (org-gtd-cli/batch--required item "session_id"
                                          'session_id 'session-id)))))

    ("show"
     (org-gtd-cli/batch--result
      idx (org-gtd-cli/batch--with-addr item nil
            (org-gtd-cli/batch--delegate
             #'org-gtd-cli/show
             (org-gtd-cli/batch--addr item)))))

    ("set-tags"
     (let ((payload (org-gtd-cli/batch--with-addr item t
                      (org-gtd-cli/batch--delegate
                       #'org-gtd-cli/set-tags
                       (org-gtd-cli/batch--addr item)
                       (org-gtd-cli/batch--field item 'tags)))))
       ;; Legacy batch schema: `tags' as a comma-joined string.
       (org-gtd-cli/batch--result
        idx payload
        `((tags . ,(mapconcat #'identity
                              (append (alist-get 'new_tags payload) nil)
                              ","))))))

    ("add-tags"
     (let ((payload (org-gtd-cli/batch--with-addr item t
                      (org-gtd-cli/batch--delegate
                       #'org-gtd-cli/add-tags
                       (org-gtd-cli/batch--addr item)
                       (org-gtd-cli/batch--required item "tags" 'tags)))))
       ;; Legacy batch schema: `tags' as the merged comma-joined string.
       (org-gtd-cli/batch--result
        idx payload
        `((tags . ,(mapconcat #'identity
                              (append (alist-get 'new_tags payload) nil)
                              ","))))))

    ("remove-tags"
     (let ((payload (org-gtd-cli/batch--with-addr item t
                      (org-gtd-cli/batch--delegate
                       #'org-gtd-cli/remove-tags
                       (org-gtd-cli/batch--addr item)
                       (org-gtd-cli/batch--required item "tags" 'tags)))))
       ;; Legacy batch schema: `tags' as the resulting comma-joined string.
       (org-gtd-cli/batch--result
        idx payload
        `((tags . ,(mapconcat #'identity
                              (append (alist-get 'new_tags payload) nil)
                              ","))))))

    (_ (error "Unsupported batch command: %s" command))))

;;; org-gtd-cli.el ends here
