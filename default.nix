# Reference: notes/reference/org-gtd-cli.md
{
  lib,
  coreutils,
  emacs-nox,
  procps,
  python3,
  python3Packages,
  runCommand,
  writeShellScriptBin,
  symlinkJoin,
}:

let
  # Canonical shared GTD core lives here, with the tool, so this package (and
  # its standalone subflake, ./flake.nix) is self-contained — no reach into the
  # Doom tree. Doom's interactive config loads this same file via the repo root
  # (see +gtd.el). Keep it a real file, never a symlink: Nix copies a symlink
  # verbatim into the store (dangling), breaking the byte-compile.
  coreFile = ./+gtd-core.el;
  elispFile = ./org-gtd-cli.el;
  pythonScript = ./org-gtd-cli.py;

  # Byte-compiled elisp for faster Emacs startup on each invocation
  compiledElisp =
    runCommand "org-gtd-cli-elisp"
      {
        nativeBuildInputs = [ emacs-nox ];
      }
      ''
        mkdir -p $out
        cp ${coreFile} $out/gtd-core.el
        cp ${elispFile} $out/org-gtd-cli.el
        cd $out
        emacs --batch -l org -f batch-byte-compile gtd-core.el
        emacs --batch -l ./gtd-core.elc -f batch-byte-compile org-gtd-cli.el
      '';

  # The Python CLI script — thin dispatch layer calling emacs --batch
  unwrapped = writeShellScriptBin "org-gtd-cli" ''
    export PATH="${
      lib.makeBinPath [
        coreutils
        emacs-nox
        python3
      ]
    }:$PATH"
    export ORG_GTD_CORE_FILE="${compiledElisp}/gtd-core.elc"
    export ORG_GTD_ELISP_FILE="${compiledElisp}/org-gtd-cli.elc"
    exec ${python3}/bin/python3 ${pythonScript} "$@"
  '';

in
symlinkJoin {
  name = "org-gtd-cli";

  paths = [ unwrapped ];

  meta = with lib; {
    description = "CLI tool for org-mode GTD system management";
    license = licenses.mit;
    mainProgram = "org-gtd-cli";
  };

  passthru.tests =
    runCommand "org-gtd-cli-tests"
      {
        nativeBuildInputs = [
          emacs-nox
          coreutils
          procps
          python3
          python3Packages.pytest
          python3Packages.pytest-xdist
        ];
      }
      ''
        cp ${pythonScript} org-gtd-cli.py
        cp ${./test_org_gtd_cli.py} test_org_gtd_cli.py
        cp ${coreFile} +gtd-core.el
        cp ${elispFile} org-gtd-cli.el
        cp -r ${./fixtures} fixtures
        python3 -m pytest test_org_gtd_cli.py -q -n 4
        touch $out
      '';
}
