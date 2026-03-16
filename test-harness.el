;;; test-harness.el --- Batch test runner for org-gtd-cli -*- lexical-binding: t; -*-

;; Allows multiple org-gtd-cli function calls in a single Emacs session
;; by intercepting kill-emacs and capturing output per test.
;;
;; Set these variables (via --eval) before loading:
;;   org-gtd-test/results-dir  — directory for result files
;;   org-gtd-test/test-dir     — directory with test fixture copies
;;   org-gtd-test/script-dir   — directory with fixture originals

(require 'cl-lib)

(defvar org-gtd-test/results-dir nil)
(defvar org-gtd-test/test-dir nil)
(defvar org-gtd-test/script-dir nil)

;; Normalize paths: strip trailing slashes to avoid // in shell commands
;; (zsh treats // specially and glob fails)
(defun org-gtd-test/init ()
  "Normalize path variables after they are set by --eval."
  (when org-gtd-test/test-dir
    (setq org-gtd-test/test-dir (directory-file-name org-gtd-test/test-dir)))
  (when org-gtd-test/script-dir
    (setq org-gtd-test/script-dir (directory-file-name org-gtd-test/script-dir)))
  (when org-gtd-test/results-dir
    (setq org-gtd-test/results-dir (directory-file-name org-gtd-test/results-dir))))

(defun org-gtd-test/reset ()
  "Kill all org buffers and re-copy fixtures from script-dir to test-dir."
  ;; Kill all file-visiting buffers in test-dir (discard unsaved changes)
  (dolist (buf (buffer-list))
    (when (and (buffer-file-name buf)
               (string-prefix-p (expand-file-name org-gtd-test/test-dir)
                                (expand-file-name (buffer-file-name buf))))
      (with-current-buffer buf
        (set-buffer-modified-p nil)
        (kill-buffer buf))))
  ;; Reset files on disk
  ;; Use find+delete instead of rm glob to avoid zsh NOMATCH on empty dirs
  (let ((td org-gtd-test/test-dir)
        (sd org-gtd-test/script-dir))
    (shell-command-to-string
     (format "find %s -mindepth 1 -delete 2>/dev/null; cp %s/fixtures/*.org %s/; chmod u+w %s/*.org; mkdir -p %s/agent-notes"
             (shell-quote-argument td)
             (shell-quote-argument sd)
             (shell-quote-argument td)
             (shell-quote-argument td)
             (shell-quote-argument td)))))

(defun org-gtd-test/run (idx form)
  "Run FORM, capturing stdout and exit code to result files.
Also snapshots the test directory for file-content assertions."
  (let ((exit-code 0)
        (output ""))
    (cl-letf (((symbol-function 'kill-emacs)
               (lambda (&optional code)
                 (setq exit-code (or code 0))
                 (throw '--org-gtd-test-done nil)))
              ((symbol-function 'princ)
               (lambda (object &optional _printcharfun)
                 (setq output (concat output (format "%s" object))))))
      (catch '--org-gtd-test-done
        (eval form t)))
    ;; Save all modified org buffers (the function may have saved already,
    ;; but some paths exit before save-buffer)
    (dolist (buf (buffer-list))
      (when (and (buffer-file-name buf)
                 (buffer-modified-p buf)
                 (string-prefix-p (expand-file-name org-gtd-test/test-dir)
                                  (expand-file-name (buffer-file-name buf))))
        (with-current-buffer buf (save-buffer))))
    ;; Write output and exit code
    (with-temp-file (format "%s/%d.out" org-gtd-test/results-dir idx)
      (insert output))
    (with-temp-file (format "%s/%d.rc" org-gtd-test/results-dir idx)
      (insert (number-to-string exit-code)))
    ;; Snapshot test directory for file assertions
    (let ((snap-dir (format "%s/%d.files" org-gtd-test/results-dir idx)))
      (shell-command-to-string
       (format "mkdir -p %s && cp -a %s/. %s/"
               (shell-quote-argument snap-dir)
               (shell-quote-argument org-gtd-test/test-dir)
               (shell-quote-argument snap-dir))))))

;; Initialize: normalize paths set by --eval before this file was loaded
(org-gtd-test/init)

;;; test-harness.el ends here
