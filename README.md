# Codex Model Compaction

A small downstream patch for ChatGPT/Codex Desktop on Arch Linux:

| Model | Context compaction |
| --- | --- |
| `gpt-*`, with a provider supporting remote compaction | Native remote compaction |
| Kimi and all other model names | Codex's existing ordinary summarization |

The policy applies to manual, foreground automatic, and background automatic
compaction. Models may share the same Responses gateway and run concurrently.
Provider restrictions remain authoritative: the patch never enables remote
compaction for a provider that disables it.

## Installation

Repository signing fingerprint:

`93ABE6A96FE686C876BCCA16FF628EB8B9BA6C2C`

Download and inspect the installer, then run it:

```sh
curl -fL https://w568w.github.io/codex-model-compaction/install.sh -o /tmp/install-codex-model-compaction.sh
bash /tmp/install-codex-model-compaction.sh
```

The installer checks the public key fingerprint, imports and locally signs that
key with `pacman-key`, adds this repository, and runs a normal full upgrade:

```ini
[codex-model-compaction]
SigLevel = Required
Server = https://w568w.github.io/codex-model-compaction/repo/$arch
```

Fully quit Desktop once, then launch **ChatGPT (Model Compaction)**. That launcher
sets `CODEX_CLI_PATH=/usr/bin/codex-model-compaction`. The package does not overwrite
Desktop's bundled backend or the official `/usr/bin/codex`. Starting a second
launcher while Desktop is already running does not change its backend.

Afterward, update normally with `pacman -Syu`. The package requires the matching
`chatgpt-bin` version. If a new Desktop is available before a successful patched
build, pacman rejects the mismatched upgrade transaction. Other packages can be
updated with `pacman -Syu --ignore=chatgpt-bin` until the matching build is ready.
Restart Desktop after a backend update to use the newly installed executable.

## Automatic releases

GitHub Actions checks the signed official Desktop Arch repository every six
hours. New Desktop packages are verified against the pinned OpenAI Linux signing
key. The bundled `codex --version` selects an exact public `openai/codex` tag;
missing tags, signature failures, patch conflicts, or failed tests stop release.

The workflow builds matching `codex`, `codex-code-mode-host`, and
`codex-responses-api-proxy` binaries with the source's locked Rust toolchain,
Cargo dependencies, and verified V8 artifacts. Native policy tests and actual
app-server tests with a local mock Responses gateway run before packaging.
Previously verified binaries are reused when the backend source and patch have
not changed. CI never receives personal model API credentials.

The signing job signs the Arch package, backend archive, release manifest, and
repository databases. GitHub Releases preserve build artifacts; an atomic Pages
deployment serves the most recent three package versions. Failed runs retain
the previously published repository. A successful Release with a failed Pages
deployment can be retried without replacing its immutable package assets.

Every release manifest records the Desktop version and checksum, Codex version,
upstream commit, patch checksums, pipeline fingerprint, and artifact checksums.
The Arch package version is the Desktop build number; `pkgrel` increments for a
new pipeline/patch on the same Desktop version.

## Development

`packaging` is the maintenance branch of this fork. Upstream source is checked
out cleanly at the selected tag during each build; this branch carries the patch
and release tooling instead of repeatedly merging upstream `main`.

```sh
python3 -m unittest discover -s tests -p test_pipeline.py -v
python3 scripts/desktop.py --work .work
bash scripts/build-backend.sh .work
python3 tests/test_backend.py --codex .work/backend/bin/codex
python3 scripts/package.py stage .work
(cd .work/package && makepkg --force --nodeps)
python3 scripts/package.py collect .work
```

The hosted workflow builds in an Arch container. Native tests cover concurrent
GPT/Kimi requests, manual compaction, continuing after compaction, tool execution,
model switching, foreground automatic compaction, and GPT-only background remote
compaction. Pipeline tests cover bad signatures, absent tags, and patch conflicts.

Ordinary summaries are portable text. This patch does not decrypt GPT's existing
opaque remote checkpoints when another model is selected in an old conversation;
that cross-provider history behavior remains the gateway's responsibility.

## Rollback

Quit Desktop and use its original **ChatGPT** launcher to return to the bundled
backend. Older signed packages remain available in GitHub Releases and the
repository retains three versions; a package downgrade must be accompanied by
the matching Desktop version. Removing `codex-model-compaction` leaves the
official Desktop and CLI packages intact.
