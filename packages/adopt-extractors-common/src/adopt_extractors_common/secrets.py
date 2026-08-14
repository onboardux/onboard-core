"""`common.secrets` -- secret **references only**, and the value has nowhere to go.

`02` §3.1 rule 2 and `01` F2.6: a secret reference is a `config_key` with
`namespace = "secret:<source>"`, and `02` §5.1 rule 4 gives that namespace an
attribute model admitting `source` and `name` and **nothing else**. There is no
value field, so this extractor could not emit a secret value if it tried -- the
model would reject the key at `validate_attributes`, before the writer, before
the store, before any artifact.

That is the design worth stating plainly: `01` N9 is *"zero secret values in
store, outputs, logs or report"*, and it is enforced by a **missing field**
rather than by an extractor being careful. The planted-secret property suite
(`03` §7, one of the five instruments that survives any budget cut) asserts the
outcome; this module's shape is why the outcome holds.

**The name is recorded and the value is never read.** `looks_secret` decides from
the *key*, so the branch that would have read a value does not exist here at all.
"""

import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact

__all__ = ["MANIFEST", "SecretsExtractor", "looks_secret"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="common.secrets",
    version="1.0.0",
    pack="common",
    archetypes=["web", "ai", "data", "lowcode", "platform"],
    kinds=["config_key"],
    method="declared",
)

#: Key-name shapes that mark a configuration key as a **credential reference**.
#: Matched on the key, never on the value: reading a value to decide whether it
#: is a secret means having read the secret.
#:
#: These are the markers that route a key *away* from any model with a value
#: field -- the opposite of a hard-coded credential.
_SECRET_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[._-])(?:secret|password|passwd|token|api[._-]?key|apikey|credential|"
    r"private[._-]?key|access[._-]?key|client[._-]?secret|auth[._-]?token|dsn|"
    r"connection[._-]?string)(?:$|[._-])",
    re.IGNORECASE,
)

#: `<name>` -> the `secret:<source>` this file's keys belong to.
_SOURCE_BY_NAME: Final[dict[str, str]] = {
    ".env": "env",
    ".env.example": "env",
    "vault.yaml": "vault",
    "vault.yml": "vault",
}

_DOTENV_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def looks_secret(key: str) -> bool:
    """Whether a configuration key names a credential.

    Public because `common.config` asks it before minting an ordinary
    `config_key`: exactly one of the two extractors claims each key, and the
    question has to have one answer or a credential is minted twice -- once under
    a model with a value field.
    """
    return bool(_SECRET_KEY_RE.search(key))


def _source(path: str) -> str:
    name = PurePosixPath(path).name
    if name in _SOURCE_BY_NAME:
        return _SOURCE_BY_NAME[name]
    if name.startswith(".env"):
        return "env"
    return "file"


class SecretsExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        del root
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `config_key` under `secret:<source>` per credential reference.

        Note what is absent from the loop: **there is no expression that reads a
        value.** A dotenv line is matched for its key and the rest of the line is
        never captured, so the value is not in scope, not in a local, and not in
        a traceback if this function raises.
        """
        for entry in ctx.files():
            ctx.budget.check()
            name = PurePosixPath(entry.path).name
            if not (name.startswith(".env") or name in _SOURCE_BY_NAME):
                continue
            source = _source(entry.path)
            for line in ctx.text(entry).splitlines():
                match = _DOTENV_RE.match(line)
                if match is None or not looks_secret(match.group(1)):
                    continue
                key = match.group(1)
                yield SurfaceFact(
                    identity_kind="config_key",
                    namespace=f"secret:{source}",
                    local_key=key,
                    title=f"{source}:{key}",
                    attributes={"source": source, "name": key},
                    source_refs=[SourceRef(path=entry.path, blob_sha=entry.blob_sha)],
                    # A credential resolved from the environment changes without
                    # a commit, which is exactly `01` F8.6's outside-VCS case and
                    # the reason rotation is invisible to a diff.
                    outside_vcs=source == "env",
                )
