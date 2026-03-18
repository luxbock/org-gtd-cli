;;; org-gtd-cli.el --- CLI interface for org-mode GTD system -*- lexical-binding: t; -*-

;; Standalone org-mode GTD tool for batch mode (emacs --batch -q).
;; No Doom Emacs, no external packages — pure built-in org-mode + cl-lib.
;;
;; Shared GTD config (TODO keywords, tags, project detection, skip functions,
;; agenda commands) is loaded from gtd-core.el via `-l` before this file.

(require 'org)
(require 'org-agenda)
(require 'org-archive)
(require 'cl-lib)

;; ══════════════════════════════════════════════════════════════════════════════
;; CLI-specific configuration
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
      org-default-notes-file (concat org-directory "inbox.org")
      org-startup-with-inline-images nil)

;; Override DEFER from core: drop @ to avoid interactive note prompt in batch
(setq org-todo-keywords
      '((sequence "TODO(t)" "NEXT(n)" "|" "DONE(d!/!)")
        (sequence "WAITING(w/!)" "DEFER(f/!)" "|" "CANCELLED(c/!)")))

;; ══════════════════════════════════════════════════════════════════════════════
;; Body text validation
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/validate-body-text (text)
  "Error and exit if TEXT contains org headings that would corrupt the file."
  (when (and text (not (string-empty-p text))
             (or (string-match-p "\\`\\*+ " text)
                 (string-match-p "\n\\*+ " text)))
    (princ (concat
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

(defun org-gtd-cli/heading-path-at-point ()
  "Return the full slash-separated path for the heading at point.
Walks up the org tree to build a path like \"Computers/NixOS/epiphyte\"."
  (let ((path (list (org-get-heading t t t t))))
    (save-excursion
      (while (org-up-heading-safe)
        (push (org-get-heading t t t t) path)))
    (mapconcat #'identity path "/")))

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
                    (pos (line-beginning-position)))
               (when (and state
                          (or include-done
                              (not (member state org-done-keywords)))
                          (string-match-p (regexp-quote substring)
                                          (downcase heading)))
                 (push (list (current-buffer) pos
                             state heading
                             (org-gtd-cli/relative-filename file))
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
          (princ (format "[%d] %s %s (%s)\n"
                         i (nth 2 m) (nth 3 m) (nth 4 m)))
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

(defun org-gtd-cli/fill-text (text)
  "Fill TEXT to 80 columns, respecting org syntax (blocks, lists, timestamps)."
  (if (or (null text) (string-empty-p text))
      text
    (with-temp-buffer
      (org-mode)
      (insert text)
      (let ((fill-column 80))
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
                   (let ((line-str
                          (concat state
                                  (when (and priority-char
                                             (not (string= priority-char "B")))
                                    ;; Only show non-default priority
                                    (concat " [#" priority-char "]"))
                                  " " heading
                                  (when tags-str (concat " " tags-str))
                                  " (" rel-file ")"
                                  (when scheduled (concat " S:" scheduled))
                                  (when deadline (concat " D:" deadline)))))
                     (push line-str results))))))))))
    (dolist (line (nreverse results))
      (princ (concat line "\n")))
    (kill-emacs 0)))

;; --- search ---

(defun org-gtd-cli/search (substring &optional states-csv tag-filter file-name)
  "Search for tasks matching SUBSTRING in heading text.
Unlike `find-task' (which treats multiple matches as an error),
search intentionally returns all matches with exit code 0.
STATES-CSV defaults to \"TODO,NEXT\".  \"all\" means no state filter.
TAG-FILTER limits to tasks with that tag (supports inheritance).
FILE-NAME restricts search to a single file in org-directory."
  (when (or (null substring) (string-empty-p substring))
    (princ "Error: search requires a SUBSTR argument\n")
    (kill-emacs 1))
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
                        (princ (format "Error: file not found: %s\n" file-name))
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
                          (string-match-p (regexp-quote substring)
                                          heading)
                          (or (not tag-filter)
                              (let ((tag-list (split-string tag-filter "[+]")))
                                (cl-every (lambda (tag)
                                            (member tag tags))
                                          tag-list))))
                 (push (list state heading rel-file) matches))))))))
    (setq matches (nreverse matches))
    (if (null matches)
        (princ "No matches.\n")
      (let ((i 1))
        (dolist (m matches)
          (princ (format "[%d] %s %s (%s)\n"
                         i (nth 0 m) (nth 1 m) (nth 2 m)))
          (cl-incf i)))))
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
                  (content (buffer-substring-no-properties beg end)))
             (princ content)
             (princ "\n")))))))
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
                      (child-priority (org-entry-get nil "PRIORITY")))
                 (cl-incf total-count)
                 (when (and child-state (member child-state org-done-keywords))
                   (cl-incf done-count))
                 (push (list (or child-state "")
                             child-heading
                             child-scheduled
                             child-deadline
                             child-priority)
                       children)))))
         (if (= total-count 0)
             (progn
               (princ (format "Task \"%s\" has no subtasks\n" heading))
               (kill-emacs 1))
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
               (princ (concat line-str "\n"))))
           (princ (format "\nProgress: %d/%d done\n" done-count total-count)))))))
  (kill-emacs 0))

;; --- categories ---

(defun org-gtd-cli/categories (&optional file-name)
  "Show the category tree for an org file.
Displays plain (non-TODO) headings as full paths, stopping at the
first TODO heading in each branch. Useful for finding refile targets.
FILE-NAME defaults to \"tasks.org\"."
  (let* ((target (or (and file-name
                          (not (equal file-name "nil"))
                          (not (string-empty-p file-name))
                          file-name)
                     "tasks.org"))
         (file (expand-file-name target org-directory))
         (found nil))
    (unless (file-exists-p file)
      (princ (format "File not found: %s\n" target))
      (kill-emacs 1))
    (with-current-buffer (find-file-noselect file)
      (org-with-wide-buffer
       (let ((rel-file (org-gtd-cli/relative-filename file)))
         (goto-char (point-min))
         (while (re-search-forward org-heading-regexp nil t)
           (let ((state (org-get-todo-state)))
             (if state
                 (org-end-of-subtree t)
               (setq found t)
               (princ (format "%s (%s)\n"
                              (org-gtd-cli/heading-path-at-point)
                              rel-file))))))))
    (unless found
      (princ "No categories found\n")))
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
                 (let* ((rel-file (org-gtd-cli/relative-filename file))
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
                               (child-deadline (org-entry-get nil "DEADLINE")))
                           (cl-incf total-count)
                           (when (member child-state org-done-keywords)
                             (cl-incf done-count))
                           (push (list child-state child-heading child-deadline)
                                 children)))))
                   (cl-incf task-num)
                   (push (list task-num state heading project
                               (mapconcat #'identity all-tags ",")
                               rel-file body
                               (nreverse children) done-count total-count)
                         results)))))))))
    (dolist (r (nreverse results))
      (cl-destructuring-bind (num state heading project tags-str
                                  rel-file body children
                                  done-count total-count)
          r
        (princ (format "--- Task %d ---\n" num))
        (princ (format "State: %s\n" state))
        (princ (format "Heading: %s\n" heading))
        (when project
          (princ (format "Project: %s\n" project)))
        (princ (format "Tags: %s\n" tags-str))
        (princ (format "File: %s\n" rel-file))
        (when (and body (not (string-empty-p body)))
          (princ (format "Body: %s\n" body)))
        (when (> total-count 0)
          (princ (format "Subtasks: %d/%d done\n" done-count total-count))
          (dolist (child children)
            (let ((child-str (format "  %s %s (%s)"
                                     (nth 0 child) (nth 1 child)
                                     rel-file)))
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
    (when body (setq body (org-gtd-cli/fill-text body)))
    (org-gtd-cli/validate-body-text body)
    (unless (file-exists-p target-file)
      (princ (format "Error: file not found: %s\n" target-file))
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
                 (if (org-get-todo-state)
                     (org-end-of-subtree t)
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
                 (princ (format "Error: category heading \"%s\" not found in %s\n"
                                category (org-gtd-cli/relative-filename target-file)))
                 (kill-emacs 1))
                ((> (length matches) 1)
                 (princ (format "Multiple category matches for \"%s\":\n" category))
                 (let ((idx 1))
                   (dolist (m matches)
                     (princ (format "[%d] %s (%s)\n"
                                    idx (nth 2 m)
                                    (org-gtd-cli/relative-filename target-file)))
                     (cl-incf idx)))
                 (princ "Use a more specific path (e.g. --category \"Parent/Child\").\n")
                 (kill-emacs 2)))
               ;; Single match — go to it and insert
               (let ((match (car matches)))
                 (setq matched-path (nth 2 match))
                 (goto-char (car match))
                 (let ((target-level (1+ (nth 1 match))))
                   (org-end-of-subtree t)
                   (insert "\n" (org-gtd-cli/build-entry
                                 target-level todo-state title
                                 priority tags-csv schedule deadline body))
                   (insert "\n"))))
           ;; Append to end of file
           (goto-char (point-max))
           (unless (bolp) (insert "\n"))
           (insert "\n" (org-gtd-cli/build-entry
                         1 todo-state title
                         priority tags-csv schedule deadline body))
           (insert "\n")))
        (save-buffer))
      (let ((display-target
             (if use-category
                 (format "%s/%s"
                         (org-gtd-cli/relative-filename target-file)
                         matched-path)
               (org-gtd-cli/relative-filename target-file))))
        (princ (format "Added: %s -> %s (%s)\n" title display-target
                       (org-gtd-cli/relative-filename target-file))))))
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
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name))))
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
         (save-buffer)
         (princ (format "Added subtask: \"%s\" under \"%s\" (%s)\n"
                        title parent-heading rel-file))))))
  (kill-emacs 0))

;; --- add-event ---

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
         (use-gcal-drawer (string-suffix-p "family-calendar.org" target-file))
         (gcal-calendar-id (when use-gcal-drawer
                             "REDACTED@group.calendar.google.com")))
    (unless (file-exists-p target-file)
      (princ (format "Error: file not found: %s\n" target-file))
      (kill-emacs 1))
    (with-current-buffer (find-file-noselect target-file)
      (org-with-wide-buffer
       (goto-char (point-max))
       (unless (bolp) (insert "\n"))
       (if use-gcal-drawer
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
    (princ (format "Created: %s\n" note-file)))
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
         (princ (format "Appended to: \"%s\" (%s)\n" heading rel-file))))))
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
         (princ (format "Set body: \"%s\" (%s)\n" heading rel-file))))))
  (kill-emacs 0))

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
             (progn
               (princ (format "Would mark done: %s (%s)\n" heading rel-file))
               ;; Check for auto-progress
               (when (gtd/is-project-subtree-p)
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
             (when (gtd/is-project-subtree-p)
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
                         (let ((next-heading (org-get-heading t t t t)))
                           (org-todo "NEXT")
                           (setq auto-msg
                                 (format "  Auto-progressed: \"%s\" -> NEXT (%s)\n"
                                         next-heading rel-file)))))))))
             ;; Reorder siblings by state (DONE first, then NEXT, TODO, etc.)
             (save-excursion
               (goto-char (cdr buf-pos))
               (org-gtd-cli/reorder-siblings-by-state))
             (save-buffer)
             (princ (format "Done: %s (%s)\n" heading rel-file))
             (when auto-msg (princ auto-msg))))))))
  (kill-emacs 0))

;; --- set-state ---

(defun org-gtd-cli/set-state (substring new-state &optional index dry-run)
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
      (princ (format "Error: \"%s\" is not a valid state\nValid states: %s\n"
                     new-state (mapconcat #'identity all-states ", ")))
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
         (if is-dry-run
             (princ (format "Would change: \"%s\" %s -> %s (%s)\n"
                            heading old-state new-state rel-file))
           (let ((org-inhibit-logging nil))
             (org-todo new-state))
           (org-gtd-cli/reorder-siblings-by-state)
           (save-buffer)
           (princ (format "State change: \"%s\" %s -> %s (%s)\n"
                          heading old-state new-state rel-file)))))))
  (kill-emacs 0))

;; --- refile ---

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
                           ;; Single segment: exact case-insensitive
                           (when (string= (downcase (car target-parts))
                                          (downcase heading))
                             (if in-source
                                 (cl-incf self-match-count)
                               (setq target-pos (point)
                                     target-buf (current-buffer)
                                     target-file file)))
                         ;; Multi-segment: exact match on last, exact on each ancestor
                         (when (string= (downcase (car (last target-parts)))
                                        (downcase heading))
                           (let ((path-match t)
                                 (parts (butlast target-parts)))
                             (save-excursion
                               (dolist (part (reverse parts))
                                 (unless (and (org-up-heading-safe)
                                              (string= (downcase part)
                                                       (downcase (org-get-heading t t t t))))
                                   (setq path-match nil))))
                             (when path-match
                               (if in-source
                                   (cl-incf self-match-count)
                                 (setq target-pos (point)
                                       target-buf (current-buffer)
                                       target-file file))))))))))))
            ;; Error handling for --to
            (unless target-pos
              (if (> self-match-count 0)
                  (princ (format "Error: no valid refile target for \"%s\" (skipped %d self-match%s inside source subtree)\n"
                                 target self-match-count (if (= self-match-count 1) "" "es")))
                (princ (format "Error: target heading \"%s\" not found\n" target)))
              (kill-emacs 1)))
        ;; --category: substring match on non-TODO headings in tasks.org only
        (let* ((cat-parts (split-string category "/" t))
               (matches '())
               (cat-file (expand-file-name "tasks.org" org-directory)))
          (when (file-exists-p cat-file)
            (with-current-buffer (find-file-noselect cat-file)
              (org-with-wide-buffer
               (goto-char (point-min))
               (while (re-search-forward org-heading-regexp nil t)
                 (if (org-get-todo-state)
                     (org-end-of-subtree t)
                   (let ((heading (org-get-heading t t t t)))
                     (if (= (length cat-parts) 1)
                         (when (string-match-p (regexp-quote (car cat-parts)) heading)
                           (push (list (current-buffer) (point) cat-file
                                       (org-gtd-cli/heading-path-at-point))
                                 matches))
                       ;; Multi-segment: substring match on last, substring on ancestors
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
                             (push (list (current-buffer) (point) cat-file
                                         (org-gtd-cli/heading-path-at-point))
                                   matches)))))))))))
          (setq matches (nreverse matches))
          (cond
           ((null matches)
            (princ (format "Error: category heading \"%s\" not found\n" category))
            (kill-emacs 1))
           ((> (length matches) 1)
            (princ (format "Multiple category matches for \"%s\":\n" category))
            (let ((idx 1))
              (dolist (m matches)
                (princ (format "[%d] %s (%s)\n"
                               idx (nth 3 m)
                               (org-gtd-cli/relative-filename (nth 2 m))))
                (cl-incf idx)))
            (princ "Use a more specific path (e.g. --category \"Parent/Child\").\n")
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
           (if is-dry-run
               (princ (format "Would refile: \"%s\" -> %s/%s (%s)\n"
                              heading rel-target target-name rel-file))
             ;; Perform the refile
             (let ((rfloc (list (org-get-heading t t t t)
                                target-file nil target-pos)))
               (org-refile nil nil rfloc))
             (save-buffer)
             (with-current-buffer target-buf (save-buffer))
             (princ (format "Refiled: \"%s\" -> %s/%s (%s)\n"
                            heading rel-target target-name rel-file)))))))
    (kill-emacs 0)))

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
         (cond
          ((not has-children)
           ;; Leaf task: set it to NEXT directly (like set-state SUBSTR NEXT)
           (let ((current-state (org-get-todo-state)))
             (cond
              ((string= current-state "NEXT")
               (princ (format "Already NEXT: \"%s\" (%s)\n" heading rel-file)))
              ((not (member current-state org-not-done-keywords))
               (princ (format "Error: \"%s\" is in done state %s\n" heading current-state))
               (kill-emacs 1))
              (t
               (let ((org-inhibit-logging nil))
                 (org-todo "NEXT"))
               (save-buffer)
               (princ (format "Set NEXT: \"%s\" (%s)\n" heading rel-file))))))
          (existing-next
           (princ (format "Already has NEXT: \"%s\" (%s)\n"
                          existing-next rel-file))
           (kill-emacs 0))
          ((not first-todo-pos)
           (princ (format "Error: \"%s\" has no TODO children to promote\n" heading))
           (kill-emacs 1))
          (t
           (goto-char first-todo-pos)
           (let ((child-heading (org-get-heading t t t t)))
             (let ((org-inhibit-logging nil))
               (org-todo "NEXT"))
             (org-gtd-cli/reorder-siblings-by-state)
             (save-buffer)
             (princ (format "Set NEXT: \"%s\" (%s)\n"
                            child-heading rel-file)))))))))
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
           (princ (format "Moved: \"%s\" up (%s)\n" heading rel-file)))
          ((string= direction "down")
           (org-move-subtree-down)
           (princ (format "Moved: \"%s\" down (%s)\n" heading rel-file)))
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
           (princ (format "Moved: \"%s\" %s \"%s\" (%s)\n"
                          heading direction sibling-substring rel-file)))
          (t
           (princ (format "Error: unknown direction \"%s\"\n" direction))
           (kill-emacs 1)))
         (save-buffer)))))
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
             (princ (format "Would rename: \"%s\" -> \"%s\" (%s)\n"
                            old-heading new-title rel-file))
           (org-edit-headline new-title)
           (save-buffer)
           (princ (format "Renamed: \"%s\" -> \"%s\" (%s)\n"
                          old-heading new-title rel-file)))))))
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
               (princ (format "Would clear schedule: \"%s\" (%s)\n"
                              heading rel-file))
             (org-schedule '(4))
             (save-buffer)
             (princ (format "Cleared schedule: \"%s\" (%s)\n"
                            heading rel-file))))
          (date-str
           (let ((ts (org-gtd-cli/make-timestamp date-str time-str t)))
             (if is-dry-run
                 (princ (format "Would schedule: \"%s\" %s (%s)\n"
                                heading ts rel-file))
               (org-schedule nil ts)
               (save-buffer)
               (princ (format "Scheduled: \"%s\" %s (%s)\n"
                              heading ts rel-file)))))
          (t
           (princ "Error: provide a DATE or --clear\n")
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
               (princ (format "Would clear deadline: \"%s\" (%s)\n"
                              heading rel-file))
             (org-deadline '(4))
             (save-buffer)
             (princ (format "Cleared deadline: \"%s\" (%s)\n"
                            heading rel-file))))
          (date-str
           (let ((ts (org-gtd-cli/make-timestamp date-str time-str t)))
             (if is-dry-run
                 (princ (format "Would set deadline: \"%s\" %s (%s)\n"
                                heading ts rel-file))
               (org-deadline nil ts)
               (save-buffer)
               (princ (format "Deadline: \"%s\" %s (%s)\n"
                              heading ts rel-file)))))
          (t
           (princ "Error: provide a DATE or --clear\n")
           (kill-emacs 1)))))))
  (kill-emacs 0))

;; --- set-tags ---

(defun org-gtd-cli/set-tags (substring add-csv remove-csv &optional index dry-run)
  "Add and/or remove tags on an existing task.
ADD-CSV and REMOVE-CSV are comma-separated tag strings."
  (let* ((idx (org-gtd-cli/parse-index index))
         (is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (add-tags (when (and add-csv (not (string-empty-p add-csv))
                              (not (equal add-csv "nil")))
                     (split-string add-csv ",")))
         (remove-tags (when (and remove-csv (not (string-empty-p remove-csv))
                                 (not (equal remove-csv "nil")))
                        (split-string remove-csv ",")))
         (buf-pos (org-gtd-cli/find-task substring idx t)))
    (with-current-buffer (car buf-pos)
      (org-with-wide-buffer
       (goto-char (cdr buf-pos))
       (let* ((heading (org-get-heading t t t t))
              (rel-file (org-gtd-cli/relative-filename (buffer-file-name)))
              (old-tags (org-get-tags nil t))
              (new-tags (copy-sequence old-tags)))
         ;; Add tags (skip duplicates)
         (dolist (tag add-tags)
           (unless (member tag new-tags)
             (setq new-tags (append new-tags (list tag)))))
         ;; Remove tags
         (dolist (tag remove-tags)
           (setq new-tags (cl-remove tag new-tags :test #'string=)))
         (let ((old-str (if old-tags (concat ":" (mapconcat #'identity old-tags ":") ":") ""))
               (new-str (if new-tags (concat ":" (mapconcat #'identity new-tags ":") ":") "")))
           (if is-dry-run
               (princ (format "Would set tags: \"%s\" %s -> %s (%s)\n"
                              heading old-str new-str rel-file))
             (org-set-tags new-tags)
             (save-buffer)
             (princ (format "Tags: \"%s\" %s -> %s (%s)\n"
                            heading old-str new-str rel-file))))))))
  (kill-emacs 0))

;; --- archive helpers ---

(defun org-gtd-cli/subtree-has-recent-dates-p ()
  "Return non-nil if the subtree at point contains dates from this or last month."
  (save-excursion
    (let* ((subtree-end (save-excursion (org-end-of-subtree t)))
           (this-month (format-time-string "%Y-%m-"))
           ;; Subtract (day-of-month + 1) days to get a date in last month
           (day-of-month (string-to-number (format-time-string "%d")))
           (last-month-time (time-subtract (current-time)
                                           (days-to-time (1+ day-of-month))))
           (last-month (format-time-string "%Y-%m-" last-month-time))
           (recent-re (concat last-month "\\|" this-month)))
      (re-search-forward recent-re subtree-end t))))

(defun org-gtd-cli/subtree-has-any-dates-p ()
  "Return non-nil if the subtree at point contains any YYYY-MM-DD date string."
  (save-excursion
    (let ((subtree-end (save-excursion (org-end-of-subtree t))))
      (re-search-forward "[0-9]\\{4\\}-[0-9]\\{2\\}-[0-9]\\{2\\}" subtree-end t))))

(defun org-gtd-cli/inside-active-project-p ()
  "Return the heading of an active (non-done) ancestor, or nil.
Walks up via `org-up-heading-safe'.  An ancestor is \"active\" if it has
a TODO keyword that is NOT in `org-done-keywords'."
  (save-excursion
    (let ((result nil))
      (while (and (not result) (org-up-heading-safe))
        (let ((state (org-get-todo-state)))
          (when (and state
                     (member state org-todo-keywords-1)
                     (not (member state org-done-keywords)))
            (setq result (org-get-heading t t t t)))))
      result)))

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
           (princ (format "Not archivable: \"%s\" is still active (%s) (%s)\n"
                          heading state rel-file))
           (kill-emacs 1))
         ;; Rule 2b: no recent dates
         (when (org-gtd-cli/subtree-has-recent-dates-p)
           (princ (format "Not archivable: \"%s\" has recent dates (%s)\n"
                          heading rel-file))
           (kill-emacs 1))
         ;; Rule 3: not inside active project
         (let ((active-parent (org-gtd-cli/inside-active-project-p)))
           (when active-parent
             (princ (format "Not archivable: \"%s\" is inside active project \"%s\" (%s)\n"
                            heading active-parent rel-file))
             (kill-emacs 1)))
         ;; All checks passed
         (if is-dry-run
             (progn
               (princ (format "Would archive: \"%s\" (%s)\n" heading rel-file))
               (kill-emacs 0))
           ;; Archive
           (org-archive-subtree)
           ;; Save all modified buffers (source + archive)
           (dolist (buf (buffer-list))
             (when (and (buffer-file-name buf)
                        (buffer-modified-p buf))
               (with-current-buffer buf (save-buffer))))
           (princ (format "Archived: \"%s\" (%s)\n" heading rel-file)))))))
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
               (princ (format "Skipped (no dates): \"%s\" (%s)\n"
                              heading rel-file)))
              ;; All rules pass
              (t
               (push (list buf pos heading rel-file) archivable)))))))
      (setq archivable (nreverse archivable))
      (if (null archivable)
          (progn
            (when (> skipped 0)
              (princ (format "%d tasks skipped\n" skipped)))
            (princ "No archivable tasks found\n")
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
        (dolist (item archivable)
          (cl-destructuring-bind (buf pos heading rel-file) item
            (if is-dry-run
                (progn
                  (princ (format "Would archive: \"%s\" (%s)\n"
                                 heading rel-file))
                  (cl-incf archived))
              (with-current-buffer buf
                (org-with-wide-buffer
                 (goto-char pos)
                 ;; Verify we're still at the right heading (positions may shift)
                 (org-back-to-heading t)
                 (org-archive-subtree)
                 (cl-incf archived))))))
        ;; Save all modified buffers
        (unless is-dry-run
          (dolist (buf (buffer-list))
            (when (and (buffer-file-name buf)
                       (buffer-modified-p buf))
              (with-current-buffer buf (save-buffer)))))
        (princ (format "%s %d tasks, %d skipped\n"
                       (if is-dry-run "Would archive" "Archived")
                       archived skipped)))))
  (kill-emacs 0))

;; --- fix-timestamps (batch) ---

(defun org-gtd-cli/fix-timestamps (&optional dry-run)
  "Add missing trailing inactive timestamps to TODO headings across agenda files.
Scans all headings with a TODO keyword that lack a trailing inactive timestamp
in their body, and inserts one using the current date/time."
  (let* ((is-dry-run (and dry-run (not (equal dry-run "nil"))
                          (not (string-empty-p dry-run))))
         (candidates '())
         (fixed 0))
    ;; Collect headings with TODO keywords that lack a trailing timestamp
    (dolist (file (org-agenda-files))
      (when (file-exists-p file)
        (with-current-buffer (find-file-noselect file)
          (org-with-wide-buffer
           (goto-char (point-min))
           (while (re-search-forward org-heading-regexp nil t)
             (let ((state (org-get-todo-state)))
               (when state
                 (let* ((heading (org-get-heading t t t t))
                        (pos (line-beginning-position))
                        (level (org-current-level))
                        (subtree-end (save-excursion (org-end-of-subtree t) (point)))
                        (rel-file (org-gtd-cli/relative-filename file))
                        (has-timestamp nil))
                   ;; Check body for trailing inactive timestamp
                   (save-excursion
                     (org-end-of-meta-data t)
                     (when (> (point) subtree-end)
                       (goto-char subtree-end))
                     (let ((body-end (save-excursion
                                       (if (re-search-forward
                                            (format "^\\*\\{%d,\\} " (1+ level))
                                            subtree-end t)
                                           (line-beginning-position)
                                         subtree-end))))
                       (while (and (< (point) body-end)
                                   (not (eobp)))
                         (when (looking-at "^\\[[-0-9]+ [A-Z][a-z]+\\( [0-9:]+\\)?\\]$")
                           (setq has-timestamp t))
                         (forward-line 1))))
                   ;; If no timestamp found, add to candidates
                   (unless has-timestamp
                     (push (list (current-buffer) pos heading rel-file level subtree-end)
                           candidates))))))))))
    (setq candidates (nreverse candidates))
    (if (null candidates)
        (progn
          (princ "All headings have timestamps, nothing to fix\n")
          (kill-emacs 0))
      ;; Sort bottom-up within each buffer to avoid position invalidation
      (let ((by-buffer (make-hash-table :test 'eq)))
        (dolist (item candidates)
          (let ((buf (nth 0 item)))
            (puthash buf (cons item (gethash buf by-buffer)) by-buffer)))
        (setq candidates '())
        (maphash (lambda (_buf items) (setq candidates (append items candidates)))
                 by-buffer))
      (dolist (item candidates)
        (cl-destructuring-bind (buf pos heading rel-file level subtree-end) item
          (if is-dry-run
              (progn
                (princ (format "Would fix: \"%s\" (%s)\n" heading rel-file))
                (cl-incf fixed))
            (with-current-buffer buf
              (org-with-wide-buffer
               (goto-char pos)
               (org-back-to-heading t)
               (let* ((cur-subtree-end (save-excursion (org-end-of-subtree t) (point))))
                 (org-end-of-meta-data t)
                 (when (> (point) cur-subtree-end)
                   (goto-char cur-subtree-end))
                 (let ((body-end (save-excursion
                                   (if (re-search-forward
                                        (format "^\\*\\{%d,\\} " (1+ level))
                                        cur-subtree-end t)
                                       (line-beginning-position)
                                     cur-subtree-end))))
                   (goto-char body-end)
                   (unless (bolp) (insert "\n"))
                   (insert (org-gtd-cli/current-inactive-timestamp) "\n")))
               (princ (format "Fixed: \"%s\" (%s)\n" heading rel-file))
               (cl-incf fixed))))))
      ;; Save all modified buffers
      (unless is-dry-run
        (dolist (buf (buffer-list))
          (when (and (buffer-file-name buf)
                     (buffer-modified-p buf))
            (with-current-buffer buf (save-buffer)))))
      (princ (format "%s %d headings\n"
                     (if is-dry-run "Would fix" "Fixed")
                     fixed))))
  (kill-emacs 0))

;; ══════════════════════════════════════════════════════════════════════════════
;; Agenda view (uses org-agenda custom commands from gtd-core.el)
;; ══════════════════════════════════════════════════════════════════════════════

(defun org-gtd-cli/agenda-view (&optional key)
  "Run an org-agenda custom command in batch mode.
KEY defaults to \" \" (the full GTD dashboard).
Task lines include (file) for source identification."
  (let ((cmd-key (or key " ")))
    (unless (assoc cmd-key org-agenda-custom-commands)
      (princ (format "Unknown agenda view key: \"%s\"\nAvailable views:\n" cmd-key))
      (dolist (cmd org-agenda-custom-commands)
        (when (stringp (car cmd))
          (princ (format "  \"%s\"  %s\n" (car cmd) (or (nth 1 cmd) "")))))
      (kill-emacs 1))
    ;; Build the agenda buffer
    (let ((org-agenda-window-setup 'current-window))
      (org-agenda nil cmd-key))
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
        (forward-line 1)))
    (kill-emacs 0)))

;;; org-gtd-cli.el ends here
