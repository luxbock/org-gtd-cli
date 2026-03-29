# Reference: notes/reference/org-gtd-cli.md
{ lib
, coreutils
, emacs-nox
, python3
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
    nativeBuildInputs = [ emacs-nox coreutils ];
  } ''
    cp ${./test.sh} test.sh
    cp ${coreFile} gtd-core.el
    cp ${./org-gtd-cli.el} org-gtd-cli.el
    cp ${./test-harness.el} test-harness.el
    cp -r ${./fixtures} fixtures
    chmod +x test.sh
    bash test.sh
    touch $out
  '';
}
