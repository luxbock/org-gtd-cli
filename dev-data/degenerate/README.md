# dev-data — in-use verification data

Staged for factory workers: the built CLI must be exercised against BOTH sets,
never fixtures alone. Workers must COPY these into a scratch ORG_DIRECTORY
before mutating — never mutate this directory in place.

## real/
Copies of `fixtures/` — ordinary, well-formed org data.

## degenerate/
| file | shape | why it exists |
|---|---|---|
| `empty.org` | zero bytes | commands must not crash on an empty agenda file |
| `no-ids.org` | headings carrying no `:ID:` | id resolution must degrade, not raise |
| `malformed-drawer.org` | `:PROPERTIES:` with no `:END:` | parser robustness |
| `no-trailing-newline.org` | last line has no final newline | the exact shape the missing-`bolp` insert bugs fed on (org-gtd-cli #77, nixos-config #505) — anything appending to the last entry must not weld onto the heading or `:END:` |
| `severed.org` | tasks below category headings (`Plumbing`, `Wiring`) | the §2 severing shapes: closing over a severed open task, and the severed-leaf promotion case |
