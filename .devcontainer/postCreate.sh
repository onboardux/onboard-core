#!/usr/bin/env bash
# Bring a fresh Codespace to the point where every S1 command runs.
#
# The two-repository rule is preserved exactly: `adopt-core` and `adopt-plane`
# are separate git repositories checked out as **siblings** under /workspaces,
# never a monorepo and never vendored into one another. The handoff pack is a
# third sibling, because the constants table and the error registry live there
# and both sync gates read them.
#
# When a sibling repository is not named, this script says so and moves on
# rather than failing the container build: a developer who cannot open a
# Codespace cannot fix the reason they cannot open a Codespace.
set -euo pipefail

WORKSPACES="/workspaces"
CORE="${WORKSPACES}/adopt-core"

clone_sibling() {
  local slug="$1" target="$2" why="$3"
  if [[ -z "${slug}" ]]; then
    echo "!! ${target} not cloned: ${why}"
    echo "!! Set it and re-run:  ${target^^}_REPO=<owner>/<repo> bash .devcontainer/postCreate.sh"
    return 0
  fi
  if [[ -d "${WORKSPACES}/${target}/.git" ]]; then
    echo "==> ${target} already present"
    return 0
  fi
  echo "==> cloning ${slug} -> ${WORKSPACES}/${target}"
  if ! gh repo clone "${slug}" "${WORKSPACES}/${target}" -- --depth=1; then
    echo "!! could not clone ${slug}."
    echo "!! Codespaces only issues a token for another repository when it is listed under"
    echo "!! customizations.codespaces.repositories in .devcontainer/devcontainer.json."
  fi
}

echo "==> git: full history, so the schema linter can read a base ref"
git -C "${CORE}" fetch --unshallow 2>/dev/null || true

clone_sibling "${ADOPT_PACK_REPO:-}" "pack" "ADOPT_PACK_REPO is unset, so constants-sync and error-registry-sync cannot read the §2 tables"
clone_sibling "${ADOPT_PLANE_REPO:-}" "adopt-plane" "ADOPT_PLANE_REPO is unset, so constants-sync cannot compare plane_const"

echo "==> uv sync"
cd "${CORE}"
uv sync --all-packages

cat <<'EOF'

Codespace ready. The S1 commands:

  uv sync --all-packages
  uv run ruff check . && uv run ruff format --check .
  uv run mypy --strict packages/ scripts/ tools/ bench/
  uv run lint-imports --config importlinter.ini
  uv run pytest -m unit
  uv run pytest -m property --hypothesis-seed=0
  uv run adopt-schema generate --check
  uv run adopt-schema lint --base origin/main

Authoritative validation runs in GitHub Actions, not here: the SQLite and
Postgres realizations and the N1 benchmark are gated on the reference runner
pinned in bench/RUNNER.md, and a number from any other machine is an anecdote.
EOF
