;;; org-gtd-cli.el --- CLI interface for org-mode GTD system -*- lexical-binding: t; -*-

;; Standalone org-mode GTD tool for batch mode (emacs --batch -q).
;; No Doom Emacs, no external packages — pure built-in org-mode + cl-lib.

(require 'org)
(require 'org-agenda)
(require 'cl-lib)

;; ══════════════════════════════════════════════════════════════════════════════
;; Configuration (duplicated from Doom +gtd.el for portability)
;; ══════════════════════════════════════════════════════════════════════════════

;; Prevent lock file conflicts with running Doom instance
(setq create-lockfiles nil)

;; Isolated user-emacs-directory (set by caller, but ensure a default)
(unless (file-directory-p user-emacs-directory)
  (make-directory user-emacs-directory t))

;; Org directory from environment or default
(setq org-directory (or (getenv "ORG_DIRECTORY")
                        (expand-file-name "~/Nextcloud/org/")))
;; Ensure trailing slash
(unless (string-suffix-p "/" org-directory)
  (setq org-directory (concat org-directory "/")))

(setq org-agenda-files (list org-directory)
      org-default-notes-file (concat org-directory "inbox.org"))

;; TODO keywords — DEFER drops @ to avoid interactive note prompt in batch
(setq org-todo-keywords
      '((sequence "TODO(t)" "NEXT(n)" "|" "DONE(d!/!)")
        (sequence "WAITING(w/!)" "DEFER(f/!)" "|" "CANCELLED(c/!)")))

(setq org-todo-state-tags-triggers
      '(("CANCELLED" ("CANCELLED" . t))
        ("WAITING" ("WAITING" . t))
        ("DEFER" ("WAITING" . t) ("DEFER" . t))
        (done ("WAITING") ("CANCELLED") ("DEFER"))
        ("TODO" ("WAITING") ("CANCELLED") ("DEFER"))
        ("NEXT" ("WAITING") ("CANCELLED") ("DEFER"))
        ("DONE" ("WAITING") ("CANCELLED") ("DEFER"))))

(setq org-tag-alist '((:startgroup)
                       ("@errand" . ?e)
                       ("@agent" . ?a)
                       (:endgroup)
                       ("buy" . ?b)
                       ("call" . ?h)
                       ("email" . ?E)
                       ("url" . ?u)
                       ("nocal" . ?x)))

(setq org-log-done 'time
      org-log-into-drawer t
      org-enforce-todo-dependencies t
      org-refile-use-outline-path t
      org-outline-path-complete-in-steps nil
      org-refile-allow-creating-parent-nodes 'confirm
      org-archive-location "%s_archive::* Archived Tasks"
      org-tags-column -100
      org-startup-with-inline-images nil)

(setq org-refile-targets '((org-agenda-files :maxlevel . 9)))
(setq org-refile-target-verify-function #'org-gtd-cli/verify-refile-target)

;; ══════════════════════════════════════════════════════════════════════════════
;; Ported GTD functions (from +gtd-functions.el, Doom macros stripped)
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/verify-refile-target ()
  "Exclude headings with done TODO states from refile targets."
  (not (member (org-get-todo-state) org-done-keywords)))

(defun org-gtd-cli/is-project-p ()
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

(defun org-gtd-cli/is-task-p ()
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

(defun org-gtd-cli/is-project-subtree-p ()
  "Any task with a todo keyword that is in a project subtree."
  (let ((task (save-excursion
                (org-back-to-heading 'invisible-ok)
                (point))))
    (save-excursion
      (org-gtd-cli/find-project-task)
      (not (equal (point) task)))))

(defun org-gtd-cli/find-project-task ()
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

;; ══════════════════════════════════════════════════════════════════════════════
;; Shared helpers
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/relative-filename (filepath)
  "Return FILEPATH relative to org-directory."
  (file-relative-name filepath org-directory))

(defun org-gtd-cli/find-task (substring &optional index include-done)
  "Find a task by SUBSTRING match across all agenda files.
Returns a cons (buffer . position) or exits with appropriate code.
If INDEX is non-nil, select the Nth match (1-based).
If INCLUDE-DONE is non-nil, also match done tasks."
  (let ((matches '()))
    (dolist (file (org-agenda-files))
      (when (file-exists-p file)
        (with-current-buffer (find-file-noselect file)
          (org-with-wide-buffer
           (goto-char (point-min))
           (while (re-search-forward org-heading-regexp nil t)
             (let* ((state (org-get-todo-state))
                    (heading (org-get-heading t t t t))
                    (line (line-number-at-pos))
                    (pos (line-beginning-position)))
               (when (and state
                          (or include-done
                              (not (member state org-done-keywords)))
                          (string-match-p (regexp-quote substring)
                                          (downcase heading)))
                 (push (list (current-buffer) pos
                             state heading
                             (org-gtd-cli/relative-filename file) line)
                       matches))))))))
    (setq matches (nreverse matches))
    (cond
     ((null matches)
      (princ (format "No task found matching \"%s\"\n" substring))
      (kill-emacs 1))
     ((and (= (length matches) 1) (not index))
      (cons (nth 0 (car matches)) (nth 1 (car matches))))
     ((and index (> index 0) (<= index (length matches)))
      (let ((m (nth (1- index) matches)))
        (cons (nth 0 m) (nth 1 m))))
     ((and index (or (<= index 0) (> index (length matches))))
      (princ (format "Index %d out of range (1-%d)\n" index (length matches)))
      (kill-emacs 1))
     (t
      (princ "Multiple matches:\n")
      (let ((i 1))
        (dolist (m matches)
          (princ (format "[%d] %s %s (%s:%d)\n"
                         i (nth 2 m) (nth 3 m) (nth 4 m) (nth 5 m)))
          (cl-incf i)))
      (princ "\nUse --index N to select one.\n")
      (kill-emacs 2)))))

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
                                       schedule deadline body)
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
      (push (concat "SCHEDULED: " (org-gtd-cli/make-timestamp schedule nil t))
            parts))
    (when (and deadline (not (string-empty-p deadline)))
      (push (concat "DEADLINE: " (org-gtd-cli/make-timestamp deadline nil t))
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
     (princ (format "Error: invalid date \"%s\": %s\n" date-str (error-message-string err)))
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
    ;; Build the match string for org-map-entries
    (let ((match (or tag-filter "")))
      (dolist (file (org-agenda-files))
        (when (file-exists-p file)
          (with-current-buffer (find-file-noselect file)
            (org-with-wide-buffer
             (goto-char (point-min))
             (while (re-search-forward org-heading-regexp nil t)
               (let* ((state (org-get-todo-state))
                      (heading (org-get-heading t t t t))
                      (priority (org-get-priority (thing-at-point 'line t)))
                      (priority-char (org-entry-get nil "PRIORITY"))
                      (tags (org-get-tags))
                      (tags-str (when tags (concat ":" (mapconcat #'identity tags ":") ":")))
                      (scheduled (org-entry-get nil "SCHEDULED"))
                      (deadline (org-entry-get nil "DEADLINE"))
                      (line (line-number-at-pos))
                      (rel-file (org-gtd-cli/relative-filename file)))
                 ;; Filter: must have a TODO state
                 (when (and state
                            ;; State filter
                            (if state-filter
                                (member state state-filter)
                              ;; Default: non-done
                              (not (member state org-done-keywords)))
                            ;; Tag filter
                            (or (not tag-filter)
                                (let ((tag-list (split-string tag-filter "[+]")))
                                  (cl-every (lambda (tag)
                                              (member tag tags))
                                            tag-list)))
                            ;; Date range filter (on scheduled or deadline)
                            (or (not from-time)
                                (let ((s-time (when scheduled
                                                (org-time-string-to-time scheduled)))
                                      (d-time (when deadline
                                                (org-time-string-to-time deadline))))
                                  (or (and s-time (not (time-less-p s-time from-time)))
                                      (and d-time (not (time-less-p d-time from-time)))
                                      ;; Tasks without dates pass through
                                      (and (not s-time) (not d-time)))))
                            (or (not to-time)
                                (let ((s-time (when scheduled
                                                (org-time-string-to-time scheduled)))
                                      (d-time (when deadline
                                                (org-time-string-to-time deadline))))
                                  (or (and s-time (time-less-p s-time
                                                               (time-add to-time (seconds-to-time 86400))))
                                      (and d-time (time-less-p d-time
                                                               (time-add to-time (seconds-to-time 86400))))
                                      ;; Tasks without dates pass through
                                      (and (not s-time) (not d-time))))))
                   (let ((line-str
                          (concat state
                                  (when (and priority-char
                                             (not (string= priority-char "B")))
                                    ;; Only show non-default priority
                                    (concat " [#" priority-char "]"))
                                  " " heading
                                  (when tags-str (concat " " tags-str))
                                  " (" rel-file ":" (number-to-string line) ")"
                                  (when scheduled (concat " S:" scheduled))
                                  (when deadline (concat " D:" deadline)))))
                     (push line-str results))))))))))
    (dolist (line (nreverse results))
      (princ (concat line "\n")))
    (kill-emacs 0)))

;; --- show ---

(defun org-gtd-cli/show (substring &optional index)
  "Show full content of a task."
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((file (buffer-file-name))
              (rel-file (org-gtd-cli/relative-filename file))
              (line (line-number-at-pos))
              (beg (point))
              (end (save-excursion (org-end-of-subtree t) (point)))
              (content (buffer-substring-no-properties beg end)))
         (princ (format "(%s:%d)\n" rel-file line))
         (princ content)
         (princ "\n")))))
  (kill-emacs 0))

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
              (line (line-number-at-pos))
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
                      (child-line (line-number-at-pos))
                      (child-scheduled (org-entry-get nil "SCHEDULED"))
                      (child-deadline (org-entry-get nil "DEADLINE"))
                      (child-priority (org-entry-get nil "PRIORITY")))
                 (cl-incf total-count)
                 (when (and child-state (member child-state org-done-keywords))
                   (cl-incf done-count))
                 (push (list (or child-state "")
                             child-heading
                             child-scheduled
                             child-deadline
                             child-priority
                             child-line)
                       children)))))
         (if (= total-count 0)
             (progn
               (princ (format "Task \"%s\" has no subtasks\n" heading))
               (kill-emacs 1))
           (princ (format "Project: %s (%s:%d)\n" heading rel-file line))
           (dolist (child (nreverse children))
             (let ((line-str (concat "  " (nth 0 child) " " (nth 1 child)
                                     " (" rel-file ":" (number-to-string (nth 5 child)) ")")))
               (when (and (nth 4 child) (not (string= (nth 4 child) "B")))
                 (setq line-str (concat line-str " [#" (nth 4 child) "]")))
               (when (nth 3 child)
                 (setq line-str (concat line-str "  D:" (nth 3 child))))
               (when (nth 2 child)
                 (setq line-str (concat line-str "  S:" (nth 2 child))))
               (princ (concat line-str "\n"))))
           (princ (format "\nProgress: %d/%d done\n" done-count total-count)))))))
  (kill-emacs 0))

;; --- process-agent-tasks ---

(defun org-gtd-cli/process-agent-tasks ()
  "Scan for @agent tasks and output structured work queue."
  (let ((task-num 0)
        (results '()))
    (dolist (file (org-agenda-files))
      (when (file-exists-p file)
        (with-current-buffer (find-file-noselect file)
          (org-with-wide-buffer
           (goto-char (point-min))
           (while (re-search-forward org-heading-regexp nil t)
             (let* ((state (org-get-todo-state))
                    (local-tags (org-get-tags nil t))
                    (all-tags (org-get-tags))
                    (heading (org-get-heading t t t t)))
               (when (and state
                          (not (member state org-done-keywords))
                          (member "@agent" local-tags))
                 (let* ((line (line-number-at-pos))
                        (rel-file (org-gtd-cli/relative-filename file))
                        (level (org-current-level))
                        (subtree-end (save-excursion (org-end-of-subtree t) (point)))
                        ;; Get parent heading for project context (skip non-TODO headings)
                        (project (save-excursion
                                   (let (found)
                                     (while (and (not found) (org-up-heading-safe))
                                       (when (org-get-todo-state)
                                         (setq found (org-get-heading t t t t))))
                                     found)))
                        ;; Get body text (between heading/planning and first child or end)
                        (body-start (save-excursion
                                      (org-end-of-meta-data t)
                                      (point)))
                        (body-end (save-excursion
                                    (goto-char body-start)
                                    (if (re-search-forward
                                         (format "^\\*\\{%d,\\} " (1+ level))
                                         subtree-end t)
                                        (line-beginning-position)
                                      subtree-end)))
                        (body (string-trim
                               (buffer-substring-no-properties body-start body-end)))
                        ;; Count subtasks
                        (child-level (1+ level))
                        (children '())
                        (done-count 0)
                        (total-count 0))
                   ;; Gather subtasks
                   (save-excursion
                     (goto-char body-start)
                     (while (and (< (point) subtree-end)
                                 (re-search-forward org-heading-regexp subtree-end t))
                       (when (= (org-current-level) child-level)
                         (let ((child-state (or (org-get-todo-state) ""))
                               (child-heading (org-get-heading t t t t))
                               (child-deadline (org-entry-get nil "DEADLINE"))
                               (child-line (line-number-at-pos)))
                           (cl-incf total-count)
                           (when (member child-state org-done-keywords)
                             (cl-incf done-count))
                           (push (list child-state child-heading child-deadline child-line)
                                 children)))))
                   (cl-incf task-num)
                   (push (list task-num state heading project
                               (mapconcat #'identity all-tags ",")
                               rel-file line body
                               (nreverse children) done-count total-count)
                         results)))))))))
    (dolist (r (nreverse results))
      (cl-destructuring-bind (num state heading project tags-str
                                  rel-file line body children
                                  done-count total-count)
          r
        (princ (format "--- Task %d ---\n" num))
        (princ (format "State: %s\n" state))
        (princ (format "Heading: %s\n" heading))
        (when project
          (princ (format "Project: %s\n" project)))
        (princ (format "Tags: %s\n" tags-str))
        (princ (format "File: %s:%d\n" rel-file line))
        (when (and body (not (string-empty-p body)))
          (princ (format "Body: %s\n" body)))
        (when (> total-count 0)
          (princ (format "Subtasks: %d/%d done\n" done-count total-count))
          (dolist (child children)
            (let ((child-str (format "  %s %s (%s:%d)"
                                     (nth 0 child) (nth 1 child)
                                     rel-file (nth 3 child))))
              (when (nth 2 child)
                (setq child-str (concat child-str "  D:" (nth 2 child))))
              (princ (concat child-str "\n")))))
        (princ "\n")))
    (princ (format "Found %d agent tasks.\n" task-num))
    (kill-emacs 0)))

;; --- add-task ---

(defun org-gtd-cli/add-task (title &optional body tags-csv schedule deadline
                                    priority file category state)
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
         (priority (when (and priority (not (string-empty-p priority))
                              (not (equal priority "nil")))
                     priority)))
    (unless (file-exists-p target-file)
      (princ (format "Error: file not found: %s\n" target-file))
      (kill-emacs 1))
    (let (inserted-line)
      (with-current-buffer (find-file-noselect target-file)
        (org-with-wide-buffer
         (if use-category
             ;; Find the category heading and insert under it
             (progn
               (goto-char (point-min))
               (let ((found nil)
                     (target-level nil))
                 (while (and (not found)
                             (re-search-forward org-heading-regexp nil t))
                   (when (string-match-p (regexp-quote category)
                                         (org-get-heading t t t t))
                     (setq found t
                           target-level (1+ (org-current-level)))))
                 (unless found
                   (princ (format "Error: category heading \"%s\" not found in %s\n"
                                  category (org-gtd-cli/relative-filename target-file)))
                   (kill-emacs 1))
                 ;; Go to end of this subtree
                 (org-end-of-subtree t)
                 (insert "\n" (org-gtd-cli/build-entry
                               target-level todo-state title
                               priority tags-csv schedule deadline body))
                 (insert "\n")
                 ;; Find the heading we just inserted
                 (forward-line -1)
                 (re-search-backward org-heading-regexp nil t)
                 (setq inserted-line (line-number-at-pos))))
           ;; Append to end of file
           (goto-char (point-max))
           (unless (bolp) (insert "\n"))
           (insert "\n" (org-gtd-cli/build-entry
                         1 todo-state title
                         priority tags-csv schedule deadline body))
           (insert "\n")
           (forward-line -1)
           (re-search-backward org-heading-regexp nil t)
           (setq inserted-line (line-number-at-pos))))
        (save-buffer))
      (let ((display-target
             (if use-category
                 (format "%s/%s"
                         (org-gtd-cli/relative-filename target-file)
                         category)
               (org-gtd-cli/relative-filename target-file))))
        (princ (format "Added: %s -> %s (%s:%d)\n" title display-target
                       (org-gtd-cli/relative-filename target-file)
                       inserted-line)))))
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
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((parent-heading (org-get-heading t t t t))
              (parent-level (org-current-level))
              (child-level (1+ parent-level))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (parent-line (line-number-at-pos)))
         ;; Demote NEXT parent to TODO (a NEXT task becoming a project should be TODO)
         (when (string= (org-get-todo-state) "NEXT")
           (let ((org-inhibit-logging nil))
             (org-todo "TODO")))
         ;; Go to end of subtree
         (org-end-of-subtree t)
         (insert "\n" (org-gtd-cli/build-entry
                       child-level todo-state title
                       priority tags-csv schedule deadline body))
         (insert "\n")
         ;; Find the heading we just inserted for its line number
         (forward-line -1)
         (re-search-backward org-heading-regexp nil t)
         (let ((child-line (line-number-at-pos)))
           (save-buffer)
           (princ (format "Added subtask: \"%s\" under \"%s\" (%s:%d)\n"
                          title parent-heading rel-file child-line)))))))
  (kill-emacs 0))

;; --- add-event ---

(defun org-gtd-cli/add-event (title date &optional time tag file)
  "Add a calendar event."
  (let* ((target-file (if (and file (not (string-empty-p file))
                               (not (equal file "nil")))
                          (concat org-directory file)
                        (concat org-directory "calendar.org")))
         (cal-tag (if (and tag (not (string-empty-p tag))
                           (not (equal tag "nil")))
                      tag
                    "calpersonal"))
         (time-str (when (and time (not (string-empty-p time))
                              (not (equal time "nil")))
                     time))
         (timestamp (org-gtd-cli/make-timestamp date time-str t)))
    (unless (file-exists-p target-file)
      (princ (format "Error: file not found: %s\n" target-file))
      (kill-emacs 1))
    (with-current-buffer (find-file-noselect target-file)
      (org-with-wide-buffer
       (goto-char (point-max))
       (unless (bolp) (insert "\n"))
       (insert (format "\n* %s :%s:\n%s\n" title cal-tag timestamp)))
      (save-buffer))
    (princ (format "Added event: %s -> %s\n"
                   title (org-gtd-cli/relative-filename target-file))))
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
             (let ((insert-point (point)))
               ;; Check if there are child headings
               (when (re-search-forward
                      (format "^\\*\\{%d,\\} " (1+ level))
                      subtree-end t)
                 (setq insert-point (line-beginning-position)))
               (goto-char insert-point)
               (insert (format "Research file: [[file:agent-notes/%s.org]]\n" slug))))
          (save-buffer)))))
    (princ (format "Created: %s\n" note-file)))
  (kill-emacs 0))

;; --- append-body ---

(defun org-gtd-cli/append-body (substring text &optional index)
  "Append text to an existing task's body."
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (line (line-number-at-pos))
              (level (org-current-level))
              (subtree-end (save-excursion (org-end-of-subtree t) (point))))
         ;; Find insertion point: after planning lines, before first child or
         ;; before creation timestamp if present
         (org-end-of-meta-data t)
         (let ((insert-point (point)))
           ;; If there are child headings, insert before them
           (save-excursion
             (when (re-search-forward
                    (format "^\\*\\{%d,\\} " (1+ level))
                    subtree-end t)
               (setq insert-point (line-beginning-position))))
           ;; Look for the creation timestamp (inactive timestamp at end of body)
           ;; and insert before it
           (save-excursion
             (goto-char insert-point)
             (let ((search-end (min insert-point subtree-end)))
               (goto-char (cdr buf-pos))
               (org-end-of-meta-data t)
               ;; Scan body lines for last inactive timestamp before children
               (let ((last-ts-pos nil))
                 (while (and (< (point) insert-point)
                             (not (eobp)))
                   (when (looking-at "^\\[[-0-9]+ [A-Z][a-z]+ [0-9:]+\\]$")
                     (setq last-ts-pos (point)))
                   (forward-line 1))
                 (when last-ts-pos
                   (setq insert-point last-ts-pos)))))
           (goto-char insert-point)
           (insert text "\n"))
         (save-buffer)
         (princ (format "Appended to: \"%s\" (%s:%d)\n" heading rel-file line))))))
  (kill-emacs 0))

;; --- done ---

(defun org-gtd-cli/done (substring &optional index dry-run)
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
              (line (line-number-at-pos))
              (old-state (org-get-todo-state)))
         (if is-dry-run
             (progn
               (princ (format "Would mark done: %s (%s:%d)\n" heading rel-file line))
               ;; Check for auto-progress
               (when (org-gtd-cli/is-project-subtree-p)
                 (let ((next-sibling-heading
                        (save-excursion
                          (when (org-get-next-sibling)
                            (when (and (org-get-todo-state)
                                       (string= (org-get-todo-state) "TODO"))
                              (org-get-heading t t t t))))))
                   (when next-sibling-heading
                     (princ (format "  Would auto-progress: \"%s\" -> NEXT\n"
                                    next-sibling-heading))))))
           ;; Actually mark done
           (let ((org-inhibit-logging nil))
             (org-todo "DONE"))
           ;; Auto-progress: promote next TODO sibling to NEXT
           (let ((auto-msg nil))
             (when (org-gtd-cli/is-project-subtree-p)
               ;; Check siblings for NEXT
               (let ((sibling-states '()))
                 (save-excursion
                   (let ((s (org-get-todo-state)))
                     (push s sibling-states)
                     (while (org-get-next-sibling)
                       (push (org-get-todo-state) sibling-states))))
                 (unless (member "NEXT" sibling-states)
                   ;; Promote next TODO to NEXT
                   (save-excursion
                     (when (org-get-next-sibling)
                       (when (and (org-get-todo-state)
                                  (string= (org-get-todo-state) "TODO"))
                         (let ((next-heading (org-get-heading t t t t))
                               (next-line (line-number-at-pos)))
                           (org-todo "NEXT")
                           (setq auto-msg
                                 (format "  Auto-progressed: \"%s\" -> NEXT (%s:%d)\n"
                                         next-heading rel-file next-line)))))))))
             ;; Group done: move completed task behind active siblings
             (save-excursion
               (goto-char (cdr buf-pos))
               (org-back-to-heading 'invisible-ok)
               (let ((keep-going t) (count -1))
                 (save-excursion
                   (while (and keep-going (org-goto-sibling 'prev))
                     (cl-incf count)
                     (when (org-entry-is-done-p)
                       (setq keep-going nil))))
                 (when (> count 0)
                   (org-move-subtree-up count))))
             (save-buffer)
             (princ (format "Done: %s (%s:%d)\n" heading rel-file line))
             (when auto-msg (princ auto-msg))))))))
  (kill-emacs 0))

;; --- set-state ---

(defun org-gtd-cli/set-state (substring new-state &optional index dry-run)
  "Change a task's TODO state."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (old-state (org-get-todo-state))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (line (line-number-at-pos)))
         (if is-dry-run
             (princ (format "Would change: \"%s\" %s -> %s (%s:%d)\n"
                            heading old-state new-state rel-file line))
           (let ((org-inhibit-logging nil))
             (org-todo new-state))
           (save-buffer)
           (princ (format "State change: \"%s\" %s -> %s (%s:%d)\n"
                          heading old-state new-state rel-file line)))))))
  (kill-emacs 0))

;; --- refile ---

(defun org-gtd-cli/refile (substring target &optional index dry-run)
  "Move a task to a different heading."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    ;; Find the target heading
    (let ((target-parts (split-string target "/"))
          (target-pos nil)
          (target-buf nil)
          (target-file nil))
      (dolist (file (org-agenda-files))
        (when (and (file-exists-p file) (not target-pos))
          (with-current-buffer (find-file-noselect file)
            (org-with-wide-buffer
             (goto-char (point-min))
             (while (and (not target-pos)
                         (re-search-forward org-heading-regexp nil t))
               ;; Match single heading or path
               (if (= (length target-parts) 1)
                   (when (string-match-p (regexp-quote (car target-parts))
                                         (org-get-heading t t t t))
                     (setq target-pos (point)
                           target-buf (current-buffer)
                           target-file file))
                 ;; Multi-part path
                 (when (string-match-p (regexp-quote (car (last target-parts)))
                                       (org-get-heading t t t t))
                   ;; Verify parent path
                   (let ((path-match t)
                         (parts (butlast target-parts)))
                     (save-excursion
                       (dolist (part (reverse parts))
                         (unless (and (org-up-heading-safe)
                                      (string-match-p (regexp-quote part)
                                                      (org-get-heading t t t t)))
                           (setq path-match nil))))
                     (when path-match
                       (setq target-pos (point)
                             target-buf (current-buffer)
                             target-file file))))))))))
      (unless target-pos
        (princ (format "Error: target heading \"%s\" not found\n" target))
        (kill-emacs 1))
      (with-current-buffer (car buf-pos)
        (org-with-wide-buffer
         (goto-char (cdr buf-pos))
         (let* ((heading (org-get-heading t t t t))
                (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
                (line (line-number-at-pos))
                (rel-target (org-gtd-cli/relative-filename target-file)))
           (if is-dry-run
               (princ (format "Would refile: \"%s\" -> %s/%s (%s:%d)\n"
                              heading rel-target target rel-file line))
             ;; Perform the refile
             (let ((rfloc (list (org-get-heading t t t t)
                                target-file nil target-pos)))
               (org-refile nil nil rfloc))
             (save-buffer)
             (with-current-buffer target-buf (save-buffer))
             (princ (format "Refiled: \"%s\" -> %s/%s (%s:%d)\n"
                            heading rel-target target rel-file line)))))))
    (kill-emacs 0)))

;; --- set-next ---

(defun org-gtd-cli/set-next (substring &optional index)
  "Set the first TODO child of a project to NEXT.
If the project already has a NEXT subtask, report it and exit 0.
If no TODO children exist, exit 1."
  (let* ((idx (org-gtd-cli/parse-index index))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (line (line-number-at-pos))
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
                   (setq existing-next
                         (list (org-get-heading t t t t) (line-number-at-pos))))
                 (when (and child-state (string= child-state "TODO") (not first-todo-pos))
                   (setq first-todo-pos (point)))))))
         (cond
          ((not has-children)
           (princ (format "Error: \"%s\" has no children (not a project)\n" heading))
           (kill-emacs 1))
          (existing-next
           (princ (format "Already has NEXT: \"%s\" (%s:%d)\n"
                          (nth 0 existing-next) rel-file (nth 1 existing-next)))
           (kill-emacs 0))
          ((not first-todo-pos)
           (princ (format "Error: \"%s\" has no TODO children to promote\n" heading))
           (kill-emacs 1))
          (t
           (goto-char first-todo-pos)
           (let ((child-heading (org-get-heading t t t t))
                 (child-line (line-number-at-pos)))
             (let ((org-inhibit-logging nil))
               (org-todo "NEXT"))
             (save-buffer)
             (princ (format "Set NEXT: \"%s\" (%s:%d)\n"
                            child-heading rel-file child-line)))))))))
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
           (princ (format "Moved: \"%s\" up (%s:%d)\n" heading rel-file (line-number-at-pos))))
          ((string= direction "down")
           (org-move-subtree-down)
           (princ (format "Moved: \"%s\" down (%s:%d)\n" heading rel-file (line-number-at-pos))))
          ((or (string= direction "before") (string= direction "after"))
           ;; Find sibling
           (unless (and sibling-substring
                        (not (string-empty-p sibling-substring))
                        (not (equal sibling-substring "nil")))
             (princ "Error: --before/--after requires a sibling substring\n")
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
                              (not (= (point-at-bol) task-beg))
                              (string-match-p
                               (regexp-quote (downcase sibling-substring))
                               (downcase (org-get-heading t t t t))))
                     (setq sibling-pos (line-beginning-position))))))
             (unless sibling-pos
               (princ (format "Error: sibling \"%s\" not found\n" sibling-substring))
               (kill-emacs 1))
             ;; Delete the task
             (goto-char task-beg)
             (let ((del-end (save-excursion (org-end-of-subtree t)
                                            (if (eobp) (point) (1+ (point))))))
               (delete-region task-beg del-end))
             ;; Recalculate sibling position after deletion
             (goto-char (point-min))
             (let ((new-sibling-pos nil))
               (while (and (not new-sibling-pos)
                           (re-search-forward org-heading-regexp nil t))
                 (when (and (= (org-current-level) level)
                            (string-match-p
                             (regexp-quote (downcase sibling-substring))
                             (downcase (org-get-heading t t t t))))
                   (setq new-sibling-pos (line-beginning-position))))
               (if (string= direction "before")
                   (progn
                     (goto-char new-sibling-pos)
                     (insert task-text "\n"))
                 ;; after
                 (goto-char new-sibling-pos)
                 (org-end-of-subtree t)
                 (unless (eobp) (forward-char))
                 (insert task-text "\n"))))
           (princ (format "Moved: \"%s\" %s \"%s\" (%s:%d)\n"
                          heading direction sibling-substring rel-file (line-number-at-pos))))
          (t
           (princ (format "Error: unknown direction \"%s\"\n" direction))
           (kill-emacs 1)))
         (save-buffer)))))
  (kill-emacs 0))

;;; org-gtd-cli.el ends here
