#!/usr/bin/env bash
# System tooling the Codespace needs and the base image does not carry.
#
# Deliberately small. Everything here is *toolchain*: it builds and validates
# the artifact and is never linked into the wheel or the single-file binary,
# which is why none of it appears in `licence-verifications.md` and why the
# licence gate does not see it. That reasoning is written down in the
# "Toolchain that is never distributed" section of that file so nobody has to
# infer it during a review.
set -euo pipefail

# Pinned. A floating `uv` is a resolver that changes underneath a green run.
UV_VERSION="0.9.5"

echo "==> apt packages: sqlite3, postgresql-client, build tooling"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  sqlite3 \
  postgresql-client \
  build-essential \
  ca-certificates
sudo rm -rf /var/lib/apt/lists/*

echo "==> uv ${UV_VERSION}"
pipx install "uv==${UV_VERSION}" || pip install --user "uv==${UV_VERSION}"

echo "==> versions"
sqlite3 --version
psql --version
uv --version
