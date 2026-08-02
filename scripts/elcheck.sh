# elcheck — fast elisp sanity check: exact-position paren balance via
# check-parens, then a strict byte-compile in dependency order.
#
# Usage: elcheck [FILE.el ...]
# With no arguments it checks +gtd-core.el and org-gtd-cli.el in the
# current directory (run it from the repo root).
#
# Two phases, both in the low hundreds of milliseconds:
#
#   1. Balance: Emacs `check-parens` per file. On imbalance it prints
#      FILE:LINE:COL — the spot where the first unterminated expression
#      *starts*, which is the position byte-compile's "End of file
#      during parsing" never gives you — and skips phase 2.
#   2. Compile: byte-compile in dependency order (per README), in a
#      throwaway directory so the checkout's git-ignored .elc files are
#      left alone. Warnings are promoted to errors: the tree is
#      warning-clean, so any new warning is almost certainly a typo
#      (free variable, misspelled function, bad arity).
#
# Exits non-zero on any problem. This is the body of a
# writeShellApplication (see default.nix): bash with -euo pipefail, and
# emacs/coreutils provided on PATH.

if [ "$#" -gt 0 ]; then
  targets=("$@")
else
  targets=(+gtd-core.el org-gtd-cli.el)
fi

for f in "${targets[@]}"; do
  if [ ! -f "$f" ]; then
    echo "elcheck: no such file: $f (run from the repo root, or pass paths)" >&2
    exit 2
  fi
done

# Phase 1: balance.
if ! emacs --batch -Q --eval '(let ((status 0))
       (dolist (file command-line-args-left)
         (with-temp-buffer
           (insert-file-contents file)
           (emacs-lisp-mode)
           (condition-case nil
               (let ((inhibit-message t)) (check-parens))
             (error
              (setq status 1)
              (princ (format "%s:%d:%d: unbalanced expression starts here (check-parens)\n"
                             file (line-number-at-pos) (1+ (current-column))))))))
       (kill-emacs status))' "${targets[@]}"; then
  echo "elcheck: FAIL (unbalanced delimiters; positions above)" >&2
  exit 1
fi

# Phase 2: strict byte-compile, in a scratch dir.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

status=0
core_state=""

# Copy +gtd-core.el from $1 into $tmp and compile it there (once).
ensure_core() {
  case "$core_state" in
    ok) return 0 ;;
    failed) return 1 ;;
  esac
  cp "$1/+gtd-core.el" "$tmp/+gtd-core.el"
  if (cd "$tmp" && emacs --batch -Q \
        --eval '(setq byte-compile-error-on-warn t)' \
        -l org -f batch-byte-compile +gtd-core.el); then
    core_state=ok
    return 0
  fi
  core_state=failed
  return 1
}

for f in "${targets[@]}"; do
  dir=$(dirname "$f")
  base=$(basename "$f")
  case "$base" in
    +gtd-core.el)
      ensure_core "$dir" || status=1
      ;;
    org-gtd-cli.el)
      # Needs the compiled core on the load chain (README dependency order).
      if [ ! -f "$dir/+gtd-core.el" ]; then
        echo "elcheck: cannot compile $f: no +gtd-core.el next to it" >&2
        status=1
        continue
      fi
      if ! ensure_core "$dir"; then
        status=1
        continue
      fi
      cp "$f" "$tmp/org-gtd-cli.el"
      (cd "$tmp" && emacs --batch -Q \
         --eval '(setq byte-compile-error-on-warn t)' \
         -l ./+gtd-core.elc -f batch-byte-compile org-gtd-cli.el) || status=1
      ;;
    *)
      cp "$f" "$tmp/$base"
      (cd "$tmp" && emacs --batch -Q \
         --eval '(setq byte-compile-error-on-warn t)' \
         -l org -f batch-byte-compile "$base") || status=1
      ;;
  esac
done

if [ "$status" -ne 0 ]; then
  echo "elcheck: FAIL (byte-compile problems above; warnings are errors here)" >&2
  exit 1
fi
echo "elcheck: OK (${#targets[@]} file(s): balanced, byte-compile clean)"
