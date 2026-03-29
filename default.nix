# Reference: notes/reference/org-gtd-cli.md
{ lib
, coreutils
, emacs-nox
, python3
, python3Packages
, runCommand
, writeShellScriptBin
, symlinkJoin
}:

let
  coreFile = ../../home/olli/features/editors/emacs/doom/modules/config/private/+gtd-core.el;
  elispFile = ./org-gtd-cli.el;
  pythonScript = ./org-gtd-cli.py;

  # The Python CLI script — thin dispatch layer calling emacs --batch
  unwrapped = writeShellScriptBin "org-gtd-cli" ''
    export PATH="${lib.makeBinPath [ coreutils emacs-nox python3 ]}:$PATH"
    export ORG_GTD_CORE_FILE="${coreFile}"
    export ORG_GTD_ELISP_FILE="${elispFile}"
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

  passthru.tests = runCommand "org-gtd-cli-tests" {
    nativeBuildInputs = [ emacs-nox coreutils python3 python3Packages.pytest ];
  } ''
    cp ${pythonScript} org-gtd-cli.py
    cp ${./test_org_gtd_cli.py} test_org_gtd_cli.py
    cp ${coreFile} gtd-core.el
    cp ${elispFile} org-gtd-cli.el
    cp -r ${./fixtures} fixtures
    export ORG_GTD_CORE_FILE="$PWD/gtd-core.el"
    export ORG_GTD_ELISP_FILE="$PWD/org-gtd-cli.el"
    python3 -m pytest test_org_gtd_cli.py -q
    touch $out
  '';
}
