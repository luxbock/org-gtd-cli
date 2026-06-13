{
  description = "org-gtd-cli — standalone GTD CLI (subflake of nixos-config)";

  # Deliberately nixpkgs-ONLY. This subflake exists so org-gtd-cli can be built
  # or `nix run`/`nix develop`'d in a lightweight context — notably inside an
  # agent-vm worker — WITHOUT evaluating the parent nixos-config flake and
  # fetching its whole input set (home-manager, hyprland, apple-silicon,
  # microvm, …). The package def (./default.nix) is shared verbatim with the
  # parent overlay (pkgs/default.nix callPackage's the same file), so there is
  # one source of truth — this is only a thin, minimal-input entry point.
  #
  # Reference it from a project as:
  #   inputs.org-gtd-cli.url = "github:luxbock/nixos-config?dir=pkgs/org-gtd-cli";
  # then add `org-gtd-cli.packages.${system}.default` to a devShell (direnv +
  # nix-direnv are already in the agent-vm base env, so a `.envrc` with
  # `use flake` puts it on PATH automatically).
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: rec {
        org-gtd-cli = pkgs.callPackage ./default.nix { };
        default = org-gtd-cli;
      });
    };
}
