;;; pkgs/org-gtd-cli/+gtd-core.el -*- lexical-binding: t; -*-

;; Shared GTD core: TODO keywords, tag triggers, project detection, the §4.1
;; minimal-move sibling placement, skip functions, agenda views. Loaded by
;; both Doom (+gtd.el) and org-gtd-cli.
;; Canonical copy lives here, with the org-gtd-cli package, so that tool's
;; standalone subflake (./flake.nix) is self-contained.
;;
;; This file contains only top-level `setq`, `defvar`, and `defun` forms -- no
;; `require`, no `after!`, no function calls at load time. Each consumer loads
;; it in a context where org symbols are available:
;;   - Doom: loaded by +gtd.el (after! org) via absolute path off the repo root
;;   - CLI: loaded via `-l` before org-gtd-cli.el (which does the requires)

;; ── Byte-compile forward declarations ───────────────────────────────────────
;; These `defvar's (no value) declare org / org-agenda dynamic variables
;; special for the byte-compiler. The file is compiled standalone in the Nix
;; build with only `org' on the load path, so the org-agenda vars are otherwise
;; unknown. Bare `defvar's have no runtime effect (they never set a value).
(defvar org-refile-use-outline-path)
(defvar org-outline-path-complete-in-steps)
(defvar org-refile-allow-creating-parent-nodes)
(defvar org-archive-mark-done)
(defvar org-refile-targets)
(defvar org-refile-target-verify-function)
(defvar org-agenda-restrict-begin)
(defvar org-agenda-skip-scheduled-if-done)
(defvar org-agenda-skip-deadline-if-done)
(defvar org-agenda-skip-timestamp-if-done)
(defvar org-agenda-todo-ignore-with-date)
(defvar org-agenda-todo-ignore-deadlines)
(defvar org-agenda-todo-ignore-scheduled)
(defvar org-agenda-todo-ignore-timestamp)
(defvar org-agenda-tags-todo-honor-ignore-options)
(defvar org-agenda-start-day)
(defvar org-agenda-span)
(defvar org-agenda-compact-blocks)
(defvar org-agenda-tags-column)
(defvar org-agenda-custom-commands)

;; ── TODO keywords & state machine ───────────────────────────────────────────

(setq org-todo-keywords
      '((sequence "TODO(t)" "NEXT(n)" "|" "DONE(d!/!)")
        (sequence "WAITING(w/!)" "DEFER(f@/!)" "|" "CANCELLED(c/!)")))

(setq org-todo-state-tags-triggers
      '(("CANCELLED" ("CANCELLED" . t) ("WAITING") ("DEFER"))
        ("WAITING" ("WAITING" . t) ("CANCELLED") ("DEFER"))
        ("DEFER" ("WAITING" . t) ("DEFER" . t) ("CANCELLED"))
        (done ("WAITING") ("CANCELLED") ("DEFER"))
        ("TODO" ("WAITING") ("CANCELLED") ("DEFER"))
        ("NEXT" ("WAITING") ("CANCELLED") ("DEFER"))
        ("DONE" ("WAITING") ("CANCELLED") ("DEFER"))))

(setq org-tag-persistent-alist '((:startgroup)
                       ("@errand" . ?e)
                       ("@agent" . ?a)
                       ("@phone" . ?h)
                       ("@computer" . ?c)
                       ("@home" . ?H)
                       (:endgroup)
                       ("buy" . ?b)
                       ("email" . ?E)
                       ("url" . ?u)
                       ("nocal" . ?x)))

;; ── Core settings ───────────────────────────────────────────────────────────

(setq org-log-done 'time
      org-log-into-drawer t
      org-enforce-todo-dependencies t
      org-refile-use-outline-path t
      org-outline-path-complete-in-steps nil
      org-refile-allow-creating-parent-nodes 'confirm
      org-tags-column 0)

(setq org-archive-location "%s_archive::* Archived Tasks"
      org-archive-mark-done nil)

(defvar gtd/archive-recent-days 21
  "Number of days a completed task must age before it becomes archivable.
Tasks with any date within this many days of today are skipped.")

(defvar gtd/now-reference nil
  "When non-nil, a time value used instead of `current-time' for archive
recency checks. Lets callers pin \"now\" deterministically (e.g. tests that
exercise date-sensitive archiving against fixed fixtures). Nil = live clock.")

(setq org-refile-targets '((org-agenda-files :maxlevel . 9)))

;; ── Refile target verification ──────────────────────────────────────────────

(defun gtd/verify-refile-target ()
  "Exclude headings with done TODO states from refile targets."
  (not (member (org-get-todo-state) org-done-keywords)))

(setq org-refile-target-verify-function #'gtd/verify-refile-target)

;; ── Project detection ───────────────────────────────────────────────────────

(defun gtd/is-project-p ()
  "Any task with a todo keyword subtask."
  (save-restriction
    (widen)
    (let ((has-subtask)
          (subtree-end (save-excursion (org-end-of-subtree t)))
          (is-a-task (member (org-get-todo-state) org-todo-keywords-1)))
      (save-excursion
        (forward-line 1)
        (while (and (not has-subtask)
                    (< (point) subtree-end)
                    (re-search-forward "^\*+ " subtree-end t))
          (when (member (org-get-todo-state) org-todo-keywords-1)
            (setq has-subtask t))))
      (and is-a-task has-subtask))))

(defun gtd/is-task-p ()
  "Any task with a todo keyword and no subtask."
  (save-restriction
    (widen)
    (let ((has-subtask)
          (subtree-end (save-excursion (org-end-of-subtree t)))
          (is-a-task (member (org-get-todo-state) org-todo-keywords-1)))
      (save-excursion
        (forward-line 1)
        (while (and (not has-subtask)
                    (< (point) subtree-end)
                    (re-search-forward "^\*+ " subtree-end t))
          (when (member (org-get-todo-state) org-todo-keywords-1)
            (setq has-subtask t))))
      (and is-a-task (not has-subtask)))))

(defun gtd/is-subproject-p ()
  "Any task which is a subtask of another project."
  (let ((is-subproject)
        (is-a-task (member (org-get-todo-state) org-todo-keywords-1)))
    (save-excursion
      (while (and (not is-subproject) (org-up-heading-safe))
        (when (member (org-get-todo-state) org-todo-keywords-1)
          (setq is-subproject t))))
    (and is-a-task is-subproject)))

(defun gtd/is-project-subtree-p ()
  "Any task with a todo keyword that is in a project subtree.
Callers of this function already widen the buffer view."
  (let ((task (save-excursion
                (org-back-to-heading 'invisible-ok)
                (point))))
    (save-excursion
      (gtd/find-project-task)
      (if (equal (point) task)
          nil
        t))))

(defun gtd/find-project-task ()
  "Move point to the parent (project) task if any."
  (save-restriction
    (widen)
    (let ((parent-task
           (save-excursion
             (org-back-to-heading 'invisible-ok)
             (point))))
      (while (org-up-heading-safe)
        (when (member (org-get-todo-state) org-todo-keywords-1)
          (setq parent-task (point))))
      (goto-char parent-task)
      parent-task)))

;; ── Project progression helpers ────────────────────────────────────────────

(defun gtd/has-active-in-subtree-p ()
  "Return non-nil if any heading in the subtree at point has NEXT or WAITING state."
  (save-excursion
    (let ((subtree-end (save-excursion (org-end-of-subtree t)))
          (found nil))
      (forward-line 1)
      (while (and (not found)
                  (< (point) subtree-end)
                  (re-search-forward "^\\*+ \\(?:NEXT\\|WAITING\\) " subtree-end t))
        (setq found t))
      found)))

(defun gtd/promote-first-child-task ()
  "Promote the first TODO non-project child of heading at point to NEXT.
Returns the heading of the promoted task, or nil."
  (save-excursion
    (when (org-goto-first-child)
      (catch 'done
        (while t
          (when (and (equal (org-get-todo-state) "TODO")
                     (not (gtd/is-project-p)))
            (let ((h (org-get-heading t t t t)))
              (org-todo "NEXT")
              (throw 'done h)))
          (unless (org-get-next-sibling)
            (throw 'done nil)))))))

;; ── Heading helpers ─────────────────────────────────────────────────────────

(defun gtd/heading-path-at-point ()
  "Return the full slash-separated path for the heading at point.
Walks up the org tree to build a path like \"Foo/Bar/Baz\".
TODO keywords, priority cookies, tags, and comment markers are stripped."
  (let ((path (list (org-get-heading t t t t))))
    (save-excursion
      (while (org-up-heading-safe)
        (push (org-get-heading t t t t) path)))
    (mapconcat #'identity path "/")))

;; ── §4.1 minimal-move sibling placement ─────────────────────────────────────
;; The SEMANTICS.md §4.1 placement primitive, shared between the CLI's batch
;; mutations and Doom's interactive hooks. Pure elisp + org.el only: no
;; cl-lib/seq, no CLI symbols, no load-time calls.

(defun gtd/reorder--state-zone (state)
  "Return the SEMANTICS.md §2 zone STATE belongs to.
One of the symbols `completed' (DONE/CANCELLED, top block), `defer'
(DEFER, bottom block), or `active' (everything else — TODO/NEXT/WAITING
today; any later open keyword falls here by default)."
  (cond
   ((member state org-done-keywords) 'completed)
   ((string= state "DEFER") 'defer)
   (t 'active)))

(defun gtd/reorder--boundary-class (state)
  "Return the §4.1 boundary class of STATE.
`closed' (completed block), `next' (the NEXT prefix), `defer' (DEFER
block), or `active-rest' — the free interior of the active zone
(TODO/WAITING today; the never-move set, so a later addition such as
ONGOING lands here without further changes). A transition that stays
within one class never moves anything."
  (cond
   ((member state org-done-keywords) 'closed)
   ((string= state "NEXT") 'next)
   ((string= state "DEFER") 'defer)
   (t 'active-rest)))

(defun gtd/reorder--dest-index (new-state old-state states)
  "Destination index for an entry with NEW-STATE among sibling STATES.
STATES is the ordered state list of the sibling group WITHOUT the
moving entry; the returned index is the position to insert before.
OLD-STATE is the keyword before the change; nil means the entry newly
arrived in the group (add-task/add-subtask/refile). Mirrors
`gtd_reference_model.Model._reorder' exactly (SEMANTICS.md §4.1)."
  (let* ((n (length states))
         (states (vconcat states))
         (completed-run
          (let ((i 0))
            (while (and (< i n)
                        (eq (gtd/reorder--state-zone (aref states i))
                            'completed))
              (setq i (1+ i)))
            i))
         (next-prefix-end
          (let ((i completed-run))
            (while (and (< i n) (string= (aref states i) "NEXT"))
              (setq i (1+ i)))
            i)))
    (cond
     ((eq (gtd/reorder--state-zone new-state) 'completed)
      ;; Closed → bottom of the completed block (all paths).
      completed-run)
     ((string= new-state "NEXT")
      ;; Entering NEXT from within the active zone → top of the active
      ;; zone (§2: NEXT always at the top); arrivals, reopens out of
      ;; the completed block, and releases out of the DEFER block →
      ;; the end of the NEXT prefix (§4.1 arrival/reopen rules).
      (if (and old-state (eq (gtd/reorder--state-zone old-state) 'active))
          completed-run
        next-prefix-end))
     ((string= new-state "DEFER")
      ;; DEFER → top of the DEFER block; arrivals → the end of it.
      (if old-state
          (let ((i 0))
            (while (and (< i n)
                        (not (eq (gtd/reorder--state-zone (aref states i))
                                 'defer)))
              (setq i (1+ i)))
            i)
        n))
     (t
      ;; TODO/WAITING: leaving NEXT while staying in the active zone →
      ;; immediately below the remaining NEXT prefix; arrivals, reopens
      ;; out of the completed block, and DEFER releases → the end of
      ;; the active zone.
      (if (and old-state (string= old-state "NEXT"))
          next-prefix-end
        (let ((i n))
          (while (and (> i 0)
                      (eq (gtd/reorder--state-zone (aref states (1- i)))
                          'defer))
            (setq i (1- i)))
          i))))))

(defun gtd/reorder-place-entry (old-state)
  "Place the entry at point within its sibling group per SEMANTICS.md §4.1.

Call with point anywhere inside the changed entry; the entry's current
TODO keyword is read from the buffer.  OLD-STATE is the keyword the
entry carried before the mutating state change (a string); nil means
the entry newly arrived in this group (add-task/add-subtask/refile
placement — the §4.1 arrival rule, end of its zone).

Minimal move: only the entry at point may move — cut and reinserted at
its zone boundary — leaving every other sibling's bytes untouched.  An
entry already at a consistent position is a strict no-op (the buffer is
not modified at all).  Mixed sibling groups (any keyword-less sibling)
are never reordered.  Top-level groups place like any other — a uniform
top-level group is an implicit category bucket (ruling 2026-08-02,
ex-§7 row 14).

Does not save the buffer.  Leaves point on the entry's heading (at its
new location when moved).  Returns non-nil iff the entry moved."
  (org-back-to-heading 'invisible-ok)
  (let* ((level (org-current-level))
         (my-beg (point))
         (new-state (org-get-todo-state))
         (moved nil))
    (unless (and old-state new-state
                 (eq (gtd/reorder--boundary-class old-state)
                     (gtd/reorder--boundary-class new-state)))
      ;; Collect the sibling group: (BOL . STATE) per sibling, in
      ;; document order, plus the group's end position (the first
      ;; heading shallower than LEVEL, or point-max).
      (let ((group-end (point-max))
            (sibs '()))
        (save-excursion
          (if (> level 1)
              (progn (org-up-heading-safe) (forward-line 1))
            (goto-char (point-min)))
          (catch 'group-done
            (while (re-search-forward org-heading-regexp nil t)
              (let ((this-level (org-current-level)))
                (cond
                 ((< this-level level)
                  (setq group-end (line-beginning-position))
                  (throw 'group-done nil))
                 ((= this-level level)
                  (push (cons (line-beginning-position)
                              (org-get-todo-state))
                        sibs)))))))
        (setq sibs (nreverse sibs))
        ;; Mixed group (a keyword-less sibling) → never reordered (§2).
        (let ((mixed nil))
          (dolist (s sibs)
            (when (null (cdr s)) (setq mixed t)))
          (unless mixed
            (let ((my-index nil)
                  (others '())
                  (i 0))
              (dolist (s sibs)
                (if (= (car s) my-beg)
                    (setq my-index i)
                  (push s others))
                (setq i (1+ i)))
              (setq others (nreverse others))
              (unless my-index
                (error "reorder: entry at %d not in its sibling group" my-beg))
              (let ((dest (gtd/reorder--dest-index
                           new-state old-state (mapcar #'cdr others))))
                (unless (= dest my-index)
                  ;; Cut the entry's region — its heading line through
                  ;; the next sibling's heading line (or the group end)
                  ;; — and reinsert it at the destination boundary.
                  ;; Markers keep the insertion point valid across the
                  ;; deletion.
                  (let* ((region-end (if (< (1+ my-index) (length sibs))
                                         (car (nth (1+ my-index) sibs))
                                       group-end))
                         (text (buffer-substring my-beg region-end))
                         (insert-marker
                          (copy-marker (if (< dest (length others))
                                           (car (nth dest others))
                                         group-end))))
                    (unless (string-suffix-p "\n" text)
                      (setq text (concat text "\n")))
                    (delete-region my-beg region-end)
                    (goto-char insert-marker)
                    (unless (bolp) (insert "\n"))
                    (let ((entry-beg (point)))
                      (insert text)
                      (goto-char entry-beg))
                    (set-marker insert-marker nil)
                    (setq moved t)))))))))
    moved))

;; ── Agenda skip functions ───────────────────────────────────────────────────

(defun gtd/list-sublevels-for-projects-indented ()
  "Set `org-tags-match-list-sublevels' so a subtree restriction
lists all subtasks."
  (if (marker-buffer org-agenda-restrict-begin)
      (setq org-tags-match-list-sublevels 'indented)
    (setq org-tags-match-list-sublevels nil))
  nil)

(defun gtd/list-sublevels-for-projects ()
  "Set `org-tags-match-list-sublevels' so a subtree restriction
lists all subtasks."
  (if (marker-buffer org-agenda-restrict-begin)
      (setq org-tags-match-list-sublevels t)
    (setq org-tags-match-list-sublevels nil))
  nil)

(defun gtd/skip-stuck-projects ()
  "Skip trees that are not stuck projects."
  (save-restriction
    (widen)
    (let ((next-headline (save-excursion (or (outline-next-heading) (point-max)))))
      (if (gtd/is-project-p)
          (let* ((subtree-end (save-excursion (org-end-of-subtree t)))
                 (has-next))
            (save-excursion
              (forward-line 1)
              (while (and (not has-next)
                          (< (point) subtree-end)
                          (re-search-forward "^\\*+ NEXT " subtree-end t))
                (unless (member "WAITING" (org-get-tags))
                  (setq has-next t))))
            (if has-next
                nil
              next-headline))            ; a stuck project
        nil))))

(defun gtd/skip-non-stuck-projects ()
  "Skip trees that are not stuck projects."
  (gtd/list-sublevels-for-projects-indented)
  (save-restriction
    (widen)
    (let ((next-headline (save-excursion (or (outline-next-heading) (point-max)))))
      (if (gtd/is-project-p)
          (let* ((subtree-end (save-excursion (org-end-of-subtree t)))
                 (has-next))
            (save-excursion
              (forward-line 1)
              (while (and (not has-next)
                          (< (point) subtree-end)
                          (re-search-forward "^\\*+ \\(?:NEXT\\|WAITING\\) " subtree-end t))
                (setq has-next t)))
            (if (or has-next (member "DEFER" (org-get-tags)))
                next-headline
              nil))                      ; a stuck project
        next-headline))))

(defun gtd/skip-non-projects ()
  "Skip trees that are not projects."
  (gtd/list-sublevels-for-projects-indented)
  (if (save-excursion (gtd/skip-non-stuck-projects))
      (save-restriction
        (widen)
        (let ((subtree-end (save-excursion (org-end-of-subtree t))))
          (cond
           ((and (gtd/is-project-p)
                 (marker-buffer org-agenda-restrict-begin))
            nil)
           ((and (gtd/is-project-p)
                 (not (marker-buffer org-agenda-restrict-begin))
                 (not (gtd/is-project-subtree-p)))
            nil)
           (t
            subtree-end))))
    (save-excursion (org-end-of-subtree t))))

(defun gtd/skip-project-trees ()
  "Skip trees that are projects."
  (save-restriction
    (widen)
    (let ((subtree-end (save-excursion (org-end-of-subtree t))))
      (cond
       ((gtd/is-project-p) subtree-end)
       (t nil)))))

(defun gtd/skip-projects-and-single-tasks ()
  "Skip trees that are projects and single non-project tasks."
  (save-restriction
    (widen)
    (let ((next-headline (save-excursion (or (outline-next-heading) (point-max)))))
      (cond
       ((gtd/is-project-p) next-headline)
       ((and (gtd/is-task-p) (not (gtd/is-project-subtree-p))) next-headline)
       (t nil)))))

(defun gtd/skip-project-tasks-maybe ()
  "Show tasks related to the current restriction.
When restricted to a project, skip project and sub-project tasks,
NEXT tasks, and loose tasks.  When not restricted, skip project and
sub-project tasks, and project related tasks."
  (save-restriction
    (widen)
    (let* ((subtree-end (save-excursion (org-end-of-subtree t)))
           (next-headline (save-excursion (or (outline-next-heading) (point-max))))
           (limit-to-project (marker-buffer org-agenda-restrict-begin)))
      (cond
       ((gtd/is-project-p) next-headline)
       ((and (not limit-to-project)
             (gtd/is-project-subtree-p))
        subtree-end)
       ((and limit-to-project
             (gtd/is-project-subtree-p)
             (member (org-get-todo-state) (list "NEXT")))
        subtree-end)
       (t nil)))))

(defun gtd/skip-projects ()
  "Skip trees that are projects."
  (save-restriction
    (widen)
    (let ((subtree-end (save-excursion (org-end-of-subtree t))))
      (cond
       ((gtd/is-project-p) subtree-end)
       (t nil)))))

(defun gtd/skip-non-subprojects ()
  "Skip trees that are not subprojects."
  (let ((next-headline (save-excursion (outline-next-heading))))
    (if (gtd/is-subproject-p)
        nil
      next-headline)))

(defun gtd/subtree-has-any-dates-p ()
  "Return non-nil if the subtree at point contains any YYYY-MM-DD date string."
  (save-excursion
    (let ((subtree-end (save-excursion (org-end-of-subtree t))))
      (re-search-forward "[0-9]\\{4\\}-[0-9]\\{2\\}-[0-9]\\{2\\}" subtree-end t))))

(defun gtd/subtree-has-recent-dates-p ()
  "Return non-nil if the subtree at point has dates within
`gtd/archive-recent-days'."
  (save-excursion
    (let* ((subtree-end (save-excursion (org-end-of-subtree t)))
           (cutoff (format-time-string "%Y-%m-%d"
                     (time-subtract (or gtd/now-reference (current-time))
                                    (days-to-time gtd/archive-recent-days))))
           (found nil))
      (forward-line 1)
      (while (and (not found)
                  (< (point) subtree-end)
                  (re-search-forward
                   "\\([0-9]\\{4\\}-[0-9]\\{2\\}-[0-9]\\{2\\}\\)"
                   subtree-end t))
        (when (not (string< (match-string 1) cutoff))
          (setq found t)))
      found)))

(defun gtd/inside-active-project-p ()
  "Return non-nil if point is inside a project that is still active.
Walks up via `org-up-heading-safe'.  An ancestor is \"active\" if it has
a TODO keyword that is NOT in `org-done-keywords'."
  (save-excursion
    (let ((result nil))
      (while (and (not result) (org-up-heading-safe))
        (let ((state (org-get-todo-state)))
          (when (and state
                     (member state org-todo-keywords-1)
                     (not (member state org-done-keywords)))
            (setq result t))))
      result)))

(defun gtd/skip-non-archivable-tasks ()
  "Skip trees that are not available for archiving.
A done task is archivable when it:
  1. Has at least one date stamp
  2. Has no dates within `gtd/archive-recent-days'
  3. Is not inside an active (non-done) project"
  (save-restriction
    (widen)
    (let ((next-headline (save-excursion (or (outline-next-heading) (point-max)))))
      (if (member (org-get-todo-state) org-done-keywords)
          (cond
           ((gtd/inside-active-project-p) next-headline)
           ((not (gtd/subtree-has-any-dates-p)) next-headline)
           ((gtd/subtree-has-recent-dates-p) next-headline)
           (t nil))
        (or next-headline (point-max))))))

;; ── Agenda filtering settings ───────────────────────────────────────────────

(setq org-agenda-skip-scheduled-if-done t
      org-agenda-skip-deadline-if-done t
      org-agenda-skip-timestamp-if-done t
      org-agenda-todo-ignore-with-date t
      org-agenda-todo-ignore-deadlines t
      org-agenda-todo-ignore-scheduled t
      org-agenda-todo-ignore-timestamp t
      org-agenda-tags-todo-honor-ignore-options t
      org-agenda-start-day nil
      org-agenda-span 'day
      org-agenda-compact-blocks t
      org-agenda-tags-column -100)

;; ── Agenda custom commands ──────────────────────────────────────────────────

(setq org-agenda-custom-commands
      '((" " "Agenda"
         ((agenda "" nil)
          (tags-todo "-WAITING-CANCELLED/!NEXT"
                     ((org-agenda-overriding-header "Next Tasks")
                      (org-agenda-skip-function #'gtd/skip-projects-and-single-tasks)
                      (org-agenda-todo-ignore-scheduled t)
                      (org-agenda-todo-ignore-deadlines t)
                      (org-agenda-todo-ignore-with-date t)
                      (org-agenda-tags-todo-honor-ignore-options t)
                      (org-tags-match-list-sublevels t)
                      (org-agenda-sorting-strategy
                       '(todo-state-down priority-down category-keep))))
          (tags-todo "-refile-CANCELLED-url/!-DEFER-WAITING"
                     ((org-agenda-overriding-header "Tasks")
                      (org-agenda-skip-function #'gtd/skip-project-tasks-maybe)
                      (org-agenda-todo-ignore-scheduled t)
                      (org-agenda-todo-ignore-deadlines t)
                      (org-agenda-todo-ignore-with-date t)
                      (org-agenda-sorting-strategy
                       '(priority-down category-keep))))
          (tags-todo "-CANCELLED/!-DEFER+WAITING"
                     ((org-agenda-overriding-header "Waiting")
                      (org-tags-match-list-sublevels nil)
                      (org-agenda-todo-ignore-scheduled 'future)
                      (org-agenda-todo-ignore-deadlines 'future)))
          (tags-todo "-CANCELLED/!"
                     ((org-agenda-overriding-header "Stuck Projects")
                      (org-agenda-skip-function #'gtd/skip-non-stuck-projects)))
          (tags-todo "-DEFER-CANCELLED/!"
                     ((org-agenda-overriding-header "Projects")
                      (org-agenda-skip-function #'gtd/skip-non-projects)
                      (org-agenda-sorting-strategy
                       '(priority-down category-keep))))
          (tags-todo "-CANCELLED/!DEFER"
                     ((org-agenda-overriding-header "Deferred")
                      (org-agenda-skip-function #'gtd/skip-stuck-projects)
                      (org-tags-match-list-sublevels nil)
                      (org-agenda-todo-ignore-scheduled 'future)
                      (org-agenda-todo-ignore-deadlines 'future)))
          (tags-todo "-refile+url-DONE/!TODO"
                     ((org-agenda-overriding-header "Web")
                      (org-tags-match-list-sublevels nil)))
          (tags "refile"
                ((org-agenda-overriding-header "Tasks to Refile")
                 (org-tags-match-list-sublevels nil)))
          (tags "-refile/"
                ((org-agenda-overriding-header "Tasks to Archive")
                 (org-agenda-skip-function #'gtd/skip-non-archivable-tasks)
                 (org-tags-match-list-sublevels nil))))
         nil)
        ("g" "GTD Dashboard (flat)"
         ((agenda "" ((org-agenda-span 'day)))
          (alltodo "" ((org-agenda-overriding-header "")))))
        ("N" "Notes" tags-todo "note"
         ((org-agenda-overriding-header "Notes")))
        ("r" "Refile" tags "refile"
         ((org-agenda-overriding-header "Tasks to Refile")
          (org-tags-match-list-sublevels nil)))
        ("d" "Done Today" agenda ""
         ((org-agenda-span 'day)
          (org-agenda-start-with-log-mode '(closed))
          (org-agenda-log-mode-items '(closed))
          (org-agenda-start-with-entry-text-mode nil)
          (org-agenda-entry-types '())
          (org-agenda-overriding-header "Done Today")))
        ("S" "Stuck Projects" tags-todo "-CANCELLED/!"
         ((org-agenda-overriding-header "Stuck Projects")
          (org-agenda-skip-function #'gtd/skip-non-stuck-projects)))
        ("n" "Next Tasks" tags-todo "-WAITING-CANCELLED/!NEXT"
         ((org-agenda-overriding-header "Next Tasks")
          (org-agenda-skip-function #'gtd/skip-projects-and-single-tasks)
          (org-agenda-todo-ignore-scheduled t)
          (org-agenda-todo-ignore-deadlines t)
          (org-agenda-todo-ignore-with-date t)
          (org-agenda-tags-todo-honor-ignore-options t)
          (org-tags-match-list-sublevels t)
          (org-agenda-sorting-strategy
           '(todo-state-down priority-down category-keep))))
        ("t" "Tasks" tags-todo "-refile-CANCELLED-url/!-DEFER-WAITING"
         ((org-agenda-overriding-header "Tasks")
          (org-agenda-skip-function #'gtd/skip-project-tasks-maybe)
          (org-agenda-todo-ignore-scheduled t)
          (org-agenda-todo-ignore-deadlines t)
          (org-agenda-todo-ignore-with-date t)
          (org-agenda-sorting-strategy
           '(priority-down category-keep))))
        ("p" "Projects" tags-todo "-DEFER-CANCELLED/!"
         ((org-agenda-overriding-header "Projects")
          (org-agenda-skip-function #'gtd/skip-non-projects)
          (org-agenda-sorting-strategy
           '(priority-down category-keep))))
        ("w" "Waiting" tags-todo "-CANCELLED/!-DEFER+WAITING"
         ((org-agenda-overriding-header "Waiting")
          (org-tags-match-list-sublevels nil)
          (org-agenda-todo-ignore-scheduled 'future)
          (org-agenda-todo-ignore-deadlines 'future)))
        ("u" "Web" tags-todo "-refile+url-DONE/!TODO"
         ((org-agenda-overriding-header "Web")
          (org-tags-match-list-sublevels nil)))
        ("A" "Archive" tags "-refile/"
         ((org-agenda-overriding-header "Tasks to Archive")
          (org-agenda-skip-function #'gtd/skip-non-archivable-tasks)
          (org-tags-match-list-sublevels nil)))))
