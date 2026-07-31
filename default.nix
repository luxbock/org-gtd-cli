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

  # Emacs with `htmlize` on its load-path. `render-file` (and the shared
  # `org-gtd-cli/render-org-string` helper) fontify src blocks via
  # `org-html-htmlize-output-type 'css`, which needs htmlize to emit `org-*`
  # face-class spans; without it the exporter degrades to a plain <pre> (no
  # crash), but the dashboard's CSS syntax highlighting would be lost. Provision
  # it for build, runtime, and the test suite. The wrapper still exposes `emacs`
  # / `emacsclient` (the binary names the Python layer invokes) and a full org.
  emacsWithHtmlize = emacs-nox.pkgs.withPackages (epkgs: [ epkgs.htmlize ]);

  # Byte-compiled elisp for faster Emacs startup on each invocation
  compiledElisp =
    runCommand "org-gtd-cli-elisp"
      {
        nativeBuildInputs = [ emacsWithHtmlize ];
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
        emacsWithHtmlize
        python3
      ]
    }:$PATH"
    export ORG_GTD_CORE_FILE="${compiledElisp}/gtd-core.elc"
    export ORG_GTD_ELISP_FILE="${compiledElisp}/org-gtd-cli.elc"
    exec ${python3}/bin/python3 ${pythonScript} "$@"
  '';

  # The complete dev/test environment. Kept exhaustive on purpose: a
  # factory VM worker gets everything the suite needs from `nix develop`
  # alone (2026-07-31 ruling on #45) — no host-provided extras.
  testInputs = [
    emacsWithHtmlize
    coreutils
    procps
    python3
    python3Packages.pytest
    python3Packages.pytest-xdist
    python3Packages.hypothesis
  ];

in
symlinkJoin {
  name = "org-gtd-cli";

  paths = [ unwrapped ];

  meta = with lib; {
    description = "CLI tool for org-mode GTD system management";
    license = licenses.mit;
    mainProgram = "org-gtd-cli";
  };

  passthru = {
    inherit testInputs;

    tests =
      runCommand "org-gtd-cli-tests"
        {
          nativeBuildInputs = testInputs;
        }
        ''
          cp ${pythonScript} org-gtd-cli.py
          cp ${./test_org_gtd_cli.py} test_org_gtd_cli.py
          cp ${coreFile} +gtd-core.el
          cp ${elispFile} org-gtd-cli.el
          cp ${./conftest.py} conftest.py
          cp ${./gtd_reference_model.py} gtd_reference_model.py
          cp ${./test_gtd_model_properties.py} test_gtd_model_properties.py
          cp ${./test_gtd_conformance.py} test_gtd_conformance.py
          cp -r ${./.hypothesis-examples} .hypothesis-examples
          cp -r ${./fixtures} fixtures
          # Sandbox runs the fast profile; `nix develop` +
          # ORG_GTD_TEST_PROFILE=thorough is the deep run.
          python3 -m pytest -q -n 4
          touch $out
        '';
  };
}
