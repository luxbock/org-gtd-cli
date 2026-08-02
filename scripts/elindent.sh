# elindent — batch-Emacs reindent of .el files, in place.
#
# Usage: elindent FILE.el [FILE.el ...]
#
# Purpose: after an elisp edit, an unbalanced paren upstream makes
# emacs-lisp-mode reindent everything *below* it wildly — so reindent
# the file and read `git diff`: a runaway cascade of indentation from
# some line onward points straight at the breakage above it, far faster
# than staring at "End of file during parsing".
#
# Loads org first so org's macro indent declarations apply. Batch Emacs
# indents a handful of continuation lines slightly differently from the
# interactive (Doom) setup that wrote these files, so expect a little
# benign one-column drift even on a pristine file — the imbalance signal
# is the *wild* tail reindent, not small offsets. Revert the noise with
# `git checkout -- FILE` once the real problem is found; don't commit a
# pure reindent pass.
#
# This is the body of a writeShellApplication (see default.nix): bash
# with -euo pipefail, and emacs/coreutils provided on PATH.

if [ "$#" -eq 0 ]; then
  echo "usage: elindent FILE.el [FILE.el ...]" >&2
  exit 2
fi

for f in "$@"; do
  if [ ! -f "$f" ]; then
    echo "elindent: no such file: $f" >&2
    exit 2
  fi
done

emacs --batch -Q -l org --eval '(dolist (file command-line-args-left)
    (with-temp-buffer
      (insert-file-contents file)
      (emacs-lisp-mode)
      (setq indent-tabs-mode nil)
      (let ((inhibit-message t))
        (indent-region (point-min) (point-max))
        (write-region (point-min) (point-max) file nil :silent))
      (princ (format "elindent: reindented %s (inspect git diff for a runaway tail)\n"
                     file))))' "$@"
