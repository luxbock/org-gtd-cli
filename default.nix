{ writeShellApplication
, lib
, coreutils
, emacs-nox
, runCommand
}:

let
  elispFile = ./org-gtd-cli.el;
in
writeShellApplication {
  name = "org-gtd-cli";
  runtimeInputs = [ coreutils emacs-nox ];
  text = ''
    # --- Config ---
    ORG_DIR="''${ORG_DIRECTORY:-$HOME/Nextcloud/org/}"
    ELISP_FILE="${elispFile}"
    EMACS_TMPDIR=$(mktemp -d)
    trap 'rm -rf "$EMACS_TMPDIR"' EXIT

    # --- Helpers ---

    run_elisp() {
      emacs --batch -q \
        --eval "(setq user-emacs-directory \"$EMACS_TMPDIR/\")" \
        --eval "(setenv \"ORG_DIRECTORY\" \"$ORG_DIR\")" \
        -l "$ELISP_FILE" \
        --eval "$1"
    }

    # Escape a string for use as an elisp string literal
    escape_elisp() {
      local s="$1"
      s="''${s//\\/\\\\}"
      s="''${s//\"/\\\"}"
      printf '%s' "$s"
    }

    # Convert a value to elisp: empty/unset -> nil, otherwise quoted string
    to_elisp() {
      if [[ -z "''${1:-}" ]]; then
        printf 'nil'
      else
        printf '"%s"' "$(escape_elisp "$1")"
      fi
    }

    # --- Command dispatch ---

    case "''${1:-}" in
      org-timestamp)
        shift
        DATE="" TIME="" INACTIVE=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --inactive) INACTIVE="t"; shift ;;
            --help|-h)  echo "Usage: org-gtd-cli org-timestamp DATE [TIME] [--inactive]"; exit 0 ;;
            *)
              if [[ -z "$DATE" ]]; then DATE="$1"
              elif [[ -z "$TIME" ]]; then TIME="$1"
              else echo "Unknown option: $1" >&2; exit 1
              fi; shift ;;
          esac
        done
        if [[ -z "$DATE" ]]; then
          echo "Usage: org-gtd-cli org-timestamp DATE [TIME] [--inactive]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/org-timestamp $(to_elisp "$DATE") $(to_elisp "$TIME") $(to_elisp "$INACTIVE"))"
        ;;

      agenda)
        shift
        STATES="" TAG="" FROM="" TO=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --help|-h) echo "Usage: org-gtd-cli agenda [--state S1,S2] [--tag TAG] [--from DATE] [--to DATE]"; exit 0 ;;
            --state)  STATES="$2"; shift 2 ;;
            --tag)    TAG="$2"; shift 2 ;;
            --from)   FROM="$2"; shift 2 ;;
            --to)     TO="$2"; shift 2 ;;
            *)        echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        run_elisp "(org-gtd-cli/agenda $(to_elisp "$STATES") $(to_elisp "$TAG") $(to_elisp "$FROM") $(to_elisp "$TO"))"
        ;;

      show)
        shift
        SUBSTRING="''${1:-}"
        if [[ "$SUBSTRING" == "--help" || "$SUBSTRING" == "-h" ]]; then
          echo "Usage: org-gtd-cli show SUBSTR [--index N]"; exit 0
        fi
        shift || true
        INDEX=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --index) INDEX="$2"; shift 2 ;;
            *)       echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" ]]; then
          echo "Usage: org-gtd-cli show SUBSTR [--index N]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/show $(to_elisp "$SUBSTRING") $(to_elisp "$INDEX"))"
        ;;

      subtasks)
        shift
        SUBSTRING="''${1:-}"
        if [[ "$SUBSTRING" == "--help" || "$SUBSTRING" == "-h" ]]; then
          echo "Usage: org-gtd-cli subtasks SUBSTR [--index N]"; exit 0
        fi
        shift || true
        INDEX=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --index) INDEX="$2"; shift 2 ;;
            *)       echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" ]]; then
          echo "Usage: org-gtd-cli subtasks SUBSTR [--index N]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/subtasks $(to_elisp "$SUBSTRING") $(to_elisp "$INDEX"))"
        ;;

      process-agent-tasks)
        shift || true
        if [[ "''${1:-}" == "--help" || "''${1:-}" == "-h" ]]; then
          echo "Usage: org-gtd-cli process-agent-tasks"; exit 0
        fi
        run_elisp "(org-gtd-cli/process-agent-tasks)"
        ;;

      add-task)
        shift
        TITLE="" BODY="" TAGS="" SCHEDULE="" DEADLINE="" PRIORITY="" FILE="" CATEGORY="" STATE=""
        # First positional arg is title
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli add-task TITLE [--body TEXT] [--tags T1,T2] [--schedule DATE] [--deadline DATE] [--priority A|B|C] [--file FILE] [--category HEADING] [--state STATE]"; exit 0
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          TITLE="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --body)     BODY="$2"; shift 2 ;;
            --tags)     TAGS="$2"; shift 2 ;;
            --schedule) SCHEDULE="$2"; shift 2 ;;
            --deadline) DEADLINE="$2"; shift 2 ;;
            --priority) PRIORITY="$2"; shift 2 ;;
            --file)     FILE="$2"; shift 2 ;;
            --category) CATEGORY="$2"; shift 2 ;;
            --state)    STATE="$2"; shift 2 ;;
            *)          echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$TITLE" ]]; then
          echo "Usage: org-gtd-cli add-task TITLE [OPTIONS]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/add-task $(to_elisp "$TITLE") $(to_elisp "$BODY") $(to_elisp "$TAGS") $(to_elisp "$SCHEDULE") $(to_elisp "$DEADLINE") $(to_elisp "$PRIORITY") $(to_elisp "$FILE") $(to_elisp "$CATEGORY") $(to_elisp "$STATE"))"
        ;;

      add-subtask)
        shift
        PARENT="" TITLE="" BODY="" TAGS="" SCHEDULE="" DEADLINE="" PRIORITY="" STATE="" INDEX=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli add-subtask SUBSTR TITLE [--body TEXT] [--tags T1,T2] [--schedule DATE] [--deadline DATE] [--priority A|B|C] [--state STATE] [--index N]"; exit 0
        fi
        # First two positional args are parent and title
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          PARENT="$1"; shift
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          TITLE="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --body)     BODY="$2"; shift 2 ;;
            --tags)     TAGS="$2"; shift 2 ;;
            --schedule) SCHEDULE="$2"; shift 2 ;;
            --deadline) DEADLINE="$2"; shift 2 ;;
            --priority) PRIORITY="$2"; shift 2 ;;
            --state)    STATE="$2"; shift 2 ;;
            --index)    INDEX="$2"; shift 2 ;;
            *)          echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$PARENT" || -z "$TITLE" ]]; then
          echo "Usage: org-gtd-cli add-subtask PARENT_SUBSTRING TITLE [OPTIONS]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/add-subtask $(to_elisp "$PARENT") $(to_elisp "$TITLE") $(to_elisp "$BODY") $(to_elisp "$TAGS") $(to_elisp "$SCHEDULE") $(to_elisp "$DEADLINE") $(to_elisp "$PRIORITY") $(to_elisp "$STATE") $(to_elisp "$INDEX"))"
        ;;

      add-event)
        shift
        TITLE="" DATE="" TIME="" TAG="" FILE=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli add-event TITLE --date DATE [--time TIME] [--tag TAG] [--file FILE]"; exit 0
        fi
        # First positional arg is title
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          TITLE="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --date) DATE="$2"; shift 2 ;;
            --time) TIME="$2"; shift 2 ;;
            --tag)  TAG="$2"; shift 2 ;;
            --file) FILE="$2"; shift 2 ;;
            *)      echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$TITLE" || -z "$DATE" ]]; then
          echo "Usage: org-gtd-cli add-event TITLE --date DATE [OPTIONS]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/add-event $(to_elisp "$TITLE") $(to_elisp "$DATE") $(to_elisp "$TIME") $(to_elisp "$TAG") $(to_elisp "$FILE"))"
        ;;

      add-note)
        shift
        TITLE="" LINK_TASK="" TAGS="" SECTIONS=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --help|-h)   echo "Usage: org-gtd-cli add-note --title TITLE [--link-task SUBSTR] [--tags T1,T2] [--sections S1,S2]"; exit 0 ;;
            --title)     TITLE="$2"; shift 2 ;;
            --link-task) LINK_TASK="$2"; shift 2 ;;
            --tags)      TAGS="$2"; shift 2 ;;
            --sections)  SECTIONS="$2"; shift 2 ;;
            *)           echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$TITLE" ]]; then
          echo "Usage: org-gtd-cli add-note --title TITLE [OPTIONS]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/add-note $(to_elisp "$TITLE") $(to_elisp "$LINK_TASK") $(to_elisp "$TAGS") $(to_elisp "$SECTIONS"))"
        ;;

      append-body)
        shift
        SUBSTRING="" TEXT="" INDEX=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli append-body SUBSTR TEXT [--index N]"; exit 0
        fi
        # First two positional args
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          SUBSTRING="$1"; shift
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          TEXT="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --index) INDEX="$2"; shift 2 ;;
            *)       echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" || -z "$TEXT" ]]; then
          echo "Usage: org-gtd-cli append-body SUBSTRING TEXT [--index N]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/append-body $(to_elisp "$SUBSTRING") $(to_elisp "$TEXT") $(to_elisp "$INDEX"))"
        ;;

      done)
        shift
        SUBSTRING="" INDEX="" DRY_RUN=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli done SUBSTR [--index N] [--dry-run]"; exit 0
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          SUBSTRING="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --index)   INDEX="$2"; shift 2 ;;
            --dry-run) DRY_RUN="t"; shift ;;
            *)         echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" ]]; then
          echo "Usage: org-gtd-cli done SUBSTRING [--index N] [--dry-run]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/done $(to_elisp "$SUBSTRING") $(to_elisp "$INDEX") $(to_elisp "$DRY_RUN"))"
        ;;

      set-state)
        shift
        SUBSTRING="" STATE="" INDEX="" DRY_RUN=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli set-state SUBSTR STATE [--index N] [--dry-run]"; exit 0
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          SUBSTRING="$1"; shift
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          STATE="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --index)   INDEX="$2"; shift 2 ;;
            --dry-run) DRY_RUN="t"; shift ;;
            *)         echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" || -z "$STATE" ]]; then
          echo "Usage: org-gtd-cli set-state SUBSTRING STATE [--index N] [--dry-run]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/set-state $(to_elisp "$SUBSTRING") $(to_elisp "$STATE") $(to_elisp "$INDEX") $(to_elisp "$DRY_RUN"))"
        ;;

      refile)
        shift
        SUBSTRING="" TARGET="" INDEX="" DRY_RUN=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli refile SUBSTR --to TARGET [--index N] [--dry-run]"; exit 0
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          SUBSTRING="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --to)      TARGET="$2"; shift 2 ;;
            --index)   INDEX="$2"; shift 2 ;;
            --dry-run) DRY_RUN="t"; shift ;;
            *)         echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" || -z "$TARGET" ]]; then
          echo "Usage: org-gtd-cli refile SUBSTRING --to TARGET [--index N] [--dry-run]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/refile $(to_elisp "$SUBSTRING") $(to_elisp "$TARGET") $(to_elisp "$INDEX") $(to_elisp "$DRY_RUN"))"
        ;;

      set-next)
        shift
        SUBSTRING="''${1:-}"
        if [[ "$SUBSTRING" == "--help" || "$SUBSTRING" == "-h" ]]; then
          echo "Usage: org-gtd-cli set-next SUBSTR [--index N]"; exit 0
        fi
        shift || true
        INDEX=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --index) INDEX="$2"; shift 2 ;;
            *)       echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" ]]; then
          echo "Usage: org-gtd-cli set-next SUBSTR [--index N]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/set-next $(to_elisp "$SUBSTRING") $(to_elisp "$INDEX"))"
        ;;

      move)
        shift
        SUBSTRING="" DIRECTION="" SIBLING="" INDEX=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli move SUBSTR --up|--down|--before SIBL|--after SIBL [--index N]"; exit 0
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          SUBSTRING="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --up)     DIRECTION="up"; shift ;;
            --down)   DIRECTION="down"; shift ;;
            --before) DIRECTION="before"; SIBLING="$2"; shift 2 ;;
            --after)  DIRECTION="after"; SIBLING="$2"; shift 2 ;;
            --index)  INDEX="$2"; shift 2 ;;
            *)        echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" || -z "$DIRECTION" ]]; then
          echo "Usage: org-gtd-cli move SUBSTRING --up|--down|--before SIBLING|--after SIBLING [--index N]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/move $(to_elisp "$SUBSTRING") $(to_elisp "$DIRECTION") $(to_elisp "$SIBLING") $(to_elisp "$INDEX"))"
        ;;

      rename)
        shift
        SUBSTRING="" NEWTITLE="" INDEX="" DRY_RUN=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli rename SUBSTR NEWTITLE [--index N] [--dry-run]"; exit 0
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          SUBSTRING="$1"; shift
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          NEWTITLE="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --index)   INDEX="$2"; shift 2 ;;
            --dry-run) DRY_RUN="t"; shift ;;
            *)         echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" || -z "$NEWTITLE" ]]; then
          echo "Usage: org-gtd-cli rename SUBSTRING NEWTITLE [--index N] [--dry-run]" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/rename $(to_elisp "$SUBSTRING") $(to_elisp "$NEWTITLE") $(to_elisp "$INDEX") $(to_elisp "$DRY_RUN"))"
        ;;

      set-schedule)
        shift
        SUBSTRING="" DATE="" TIME="" CLEAR="" INDEX="" DRY_RUN=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli set-schedule SUBSTR DATE [--time TIME] [--clear] [--index N] [--dry-run]"; exit 0
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          SUBSTRING="$1"; shift
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          DATE="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --time)    TIME="$2"; shift 2 ;;
            --clear)   CLEAR="t"; shift ;;
            --index)   INDEX="$2"; shift 2 ;;
            --dry-run) DRY_RUN="t"; shift ;;
            *)         echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" ]]; then
          echo "Usage: org-gtd-cli set-schedule SUBSTR DATE [--time TIME] [--clear] [--index N] [--dry-run]" >&2
          exit 1
        fi
        if [[ -z "$DATE" && -z "$CLEAR" ]]; then
          echo "Error: provide a DATE or --clear" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/set-schedule $(to_elisp "$SUBSTRING") $(to_elisp "$DATE") $(to_elisp "$TIME") $(to_elisp "$CLEAR") $(to_elisp "$INDEX") $(to_elisp "$DRY_RUN"))"
        ;;

      set-deadline)
        shift
        SUBSTRING="" DATE="" TIME="" CLEAR="" INDEX="" DRY_RUN=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli set-deadline SUBSTR DATE [--time TIME] [--clear] [--index N] [--dry-run]"; exit 0
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          SUBSTRING="$1"; shift
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          DATE="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --time)    TIME="$2"; shift 2 ;;
            --clear)   CLEAR="t"; shift ;;
            --index)   INDEX="$2"; shift 2 ;;
            --dry-run) DRY_RUN="t"; shift ;;
            *)         echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" ]]; then
          echo "Usage: org-gtd-cli set-deadline SUBSTR DATE [--time TIME] [--clear] [--index N] [--dry-run]" >&2
          exit 1
        fi
        if [[ -z "$DATE" && -z "$CLEAR" ]]; then
          echo "Error: provide a DATE or --clear" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/set-deadline $(to_elisp "$SUBSTRING") $(to_elisp "$DATE") $(to_elisp "$TIME") $(to_elisp "$CLEAR") $(to_elisp "$INDEX") $(to_elisp "$DRY_RUN"))"
        ;;

      set-tags)
        shift
        SUBSTRING="" ADD="" REMOVE="" INDEX="" DRY_RUN=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli set-tags SUBSTR --add T1,T2 --remove T3 [--index N] [--dry-run]"; exit 0
        fi
        if [[ $# -gt 0 && "''${1:0:2}" != "--" ]]; then
          SUBSTRING="$1"; shift
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --add)     ADD="$2"; shift 2 ;;
            --remove)  REMOVE="$2"; shift 2 ;;
            --index)   INDEX="$2"; shift 2 ;;
            --dry-run) DRY_RUN="t"; shift ;;
            *)         echo "Unknown option: $1" >&2; exit 1 ;;
          esac
        done
        if [[ -z "$SUBSTRING" ]]; then
          echo "Usage: org-gtd-cli set-tags SUBSTR --add T1,T2 --remove T3 [--index N] [--dry-run]" >&2
          exit 1
        fi
        if [[ -z "$ADD" && -z "$REMOVE" ]]; then
          echo "Error: at least one of --add or --remove is required" >&2
          exit 1
        fi
        run_elisp "(org-gtd-cli/set-tags $(to_elisp "$SUBSTRING") $(to_elisp "$ADD") $(to_elisp "$REMOVE") $(to_elisp "$INDEX") $(to_elisp "$DRY_RUN"))"
        ;;

      archive)
        shift
        SUBSTRING="" INDEX="" DRY_RUN="" ALL=""
        if [[ $# -gt 0 && ("''${1}" == "--help" || "''${1}" == "-h") ]]; then
          echo "Usage: org-gtd-cli archive SUBSTR [--index N] [--dry-run]"; echo "       org-gtd-cli archive --all [--dry-run]"; exit 0
        fi
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --all)     ALL="t"; shift ;;
            --index)   INDEX="$2"; shift 2 ;;
            --dry-run) DRY_RUN="t"; shift ;;
            *)
              if [[ -z "$SUBSTRING" ]]; then SUBSTRING="$1"
              else echo "Unknown option: $1" >&2; exit 1
              fi; shift ;;
          esac
        done
        if [[ -n "$ALL" && -n "$SUBSTRING" ]]; then
          echo "Error: --all and SUBSTR are mutually exclusive" >&2
          exit 1
        fi
        if [[ -z "$ALL" && -z "$SUBSTRING" ]]; then
          echo "Usage: org-gtd-cli archive SUBSTR [--index N] [--dry-run]" >&2
          echo "       org-gtd-cli archive --all [--dry-run]" >&2
          exit 1
        fi
        if [[ -n "$ALL" ]]; then
          run_elisp "(org-gtd-cli/archive-all $(to_elisp "$DRY_RUN"))"
        else
          run_elisp "(org-gtd-cli/archive $(to_elisp "$SUBSTRING") $(to_elisp "$INDEX") $(to_elisp "$DRY_RUN"))"
        fi
        ;;

      -h|--help|help|"")
        cat << 'EOF'
    Usage: org-gtd-cli <command> [options]

    Commands:
      org-timestamp DATE [TIME] [--inactive]
      agenda [--state S1,S2] [--tag TAG] [--from DATE] [--to DATE]
      show SUBSTR [--index N]
      subtasks SUBSTR [--index N]
      process-agent-tasks
      add-task TITLE [--body TEXT] [--tags T1,T2] [--schedule DATE]
        [--deadline DATE] [--priority A|B|C] [--file FILE]
        [--category HEADING] [--state STATE]
      add-subtask SUBSTR TITLE [--body TEXT] [--tags T1,T2]
        [--schedule DATE] [--deadline DATE] [--priority A|B|C]
        [--state STATE] [--index N]
      add-event TITLE --date DATE [--time TIME] [--tag TAG] [--file FILE]
      add-note --title TITLE [--link-task SUBSTR] [--tags T1,T2]
        [--sections S1,S2]
      append-body SUBSTR TEXT [--index N]
      done SUBSTR [--index N] [--dry-run]
      set-state SUBSTR STATE [--index N] [--dry-run]
      set-next SUBSTR [--index N]
      refile SUBSTR --to TARGET [--index N] [--dry-run]
      move SUBSTR --up|--down|--before SIBL|--after SIBL [--index N]
      rename SUBSTR NEWTITLE [--index N] [--dry-run]
      set-schedule SUBSTR DATE [--time TIME] [--clear] [--index N] [--dry-run]
      set-deadline SUBSTR DATE [--time TIME] [--clear] [--index N] [--dry-run]
      set-tags SUBSTR --add T1,T2 --remove T3 [--index N] [--dry-run]
      archive SUBSTR [--index N] [--dry-run]
      archive --all [--dry-run]

    SUBSTR matches task headings case-insensitively. --index N (1-based)
    disambiguates when multiple tasks match.

    Environment:
      ORG_DIRECTORY    Path to org files (default: ~/Nextcloud/org/)

    Exit codes: 0 success, 1 error, 2 ambiguous match
    EOF
        ;;
      *)
        echo "Unknown command: $1" >&2
        echo "Run 'org-gtd-cli help' for usage" >&2
        exit 1
        ;;
    esac
  '';

  meta = with lib; {
    description = "CLI tool for org-mode GTD system management";
    license = licenses.mit;
    mainProgram = "org-gtd-cli";
  };

  passthru.tests = runCommand "org-gtd-cli-tests" {
    nativeBuildInputs = [ emacs-nox coreutils ];
  } ''
    cp ${./test.sh} test.sh
    cp ${./org-gtd-cli.el} org-gtd-cli.el
    cp -r ${./fixtures} fixtures
    chmod +x test.sh
    bash test.sh
    touch $out
  '';
}
