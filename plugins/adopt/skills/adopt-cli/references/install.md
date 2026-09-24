# Installing `adopt`

Three ways, one CLI. The package is **`adopt-cli`**; the executable is **`adopt`**.
Install nothing without the person's agreement — it is their machine.

## Contents

- pip or uv (Python 3.12+)
- The signed standalone binary (no Python)
- Verifying what you installed

## pip or uv

Every distribution declares `requires-python = ">=3.12"`.

```shell
uv tool install "adopt-cli>=0.4.1"      # preferred: an isolated tool environment
pipx install "adopt-cli>=0.4.1"         # the same idea with pipx
pip install "adopt-cli>=0.4.1"          # into the active environment
```

Pin a floor, not an exact version: command names, flags, JSON keys and exit codes
are additive-only from `0.3.0`, so a newer CLI never breaks these skills, while
`0.4.0` has two P1 defects. Upgrade with the tool that installed it
(`uv tool upgrade adopt-cli`, `pipx upgrade adopt-cli`, `pip install -U adopt-cli`).

Optional renderers, each needed by one flag only: `pandoc` for
`pack --format docx`, `typst` for `pack --format pdf`. Without them those two
flags refuse with `PACK_RENDERER_MISSING`; Markdown is canonical and unaffected.

## The signed standalone binary

For a machine without Python 3.12, or with no package index: every release
attaches a single-file binary packed from the same wheels PyPI serves.

| Platform | Release asset |
|---|---|
| Linux x86-64 | `adopt-linux-x86_64` |
| macOS, Apple silicon | `adopt-macos-arm64` |
| Windows x86-64 | `adopt-windows-x86_64.exe` |

There is no Intel-macOS or ARM-Linux binary; use pip or uv there.

```shell
gh release download v0.4.1 --repo onboardux/onboard-core \
   --pattern 'adopt-linux-x86_64*' --pattern 'provenance.intoto.jsonl'
```

**Verify before running.** Each binary is signed keylessly by the release
workflow, so the check is "was this produced by that workflow at that tag" —
there is no key to trust:

```shell
cosign verify-blob adopt-linux-x86_64 \
   --signature   adopt-linux-x86_64.sig \
   --certificate adopt-linux-x86_64.pem \
   --certificate-identity https://github.com/onboardux/onboard-core/.github/workflows/release.yml@refs/tags/v0.4.1 \
   --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

`Verified OK` is the only acceptable result. Without `cosign`, the GitHub CLI
verifies the attached SLSA provenance:

```shell
gh attestation verify adopt-linux-x86_64 --bundle provenance.intoto.jsonl \
   --repo onboardux/onboard-core \
   --cert-identity https://github.com/onboardux/onboard-core/.github/workflows/release.yml@refs/tags/v0.4.1 \
   --cert-oidc-issuer https://token.actions.githubusercontent.com
```

Judge it by the **exit code**: `0` is verified, and it may print nothing when its
output is not a terminal. A certificate naming a different tag is a different
release — do not run the file.

Then put it on `PATH` as `adopt` (`chmod +x` on Linux and macOS; rename to
`adopt.exe` on Windows). A binary downloaded through a browser on macOS carries a
quarantine flag: `xattr -d com.apple.quarantine <path>`.

## Verifying what you installed

```shell
adopt version --json
```

`build_id` and `sbom_sha256` are stamped in anything installed from PyPI or run
as a release binary — they bind the artifact to the tag and commit that built it.
`null` means a source checkout; on anything else it is worth reporting.
