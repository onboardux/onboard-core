"""`adopt extractors list|review|audit` -- `02` §8, `04` §6.

The review queue's whole surface. Three verbs and one refusal:

* **`list`** -- what is in quarantine, with the sandbox result and the latest
  ledger outcome for each. The reviewer's index.
* **`audit`** -- run the `04` §6 step-1 audit over a module on disk and print
  every finding. Available on a module already in quarantine so a reviewer can
  see what the pass saw, and on any path, so *"would this have been accepted"*
  is answerable without generating anything.
* **`review <id> --approve|--rewrite|--reject`** -- the human action `01` F12.3
  requires before anything an agent wrote can participate in a deterministic
  pass.

**`--approve` is refused when the file differs from the generated original**, and
this module is where an operator meets that refusal. `04` §6: without it,
reviewers silently patch and approve, the rewrite is never recorded, and
ADR-0.1's >40% reversal trigger can never fire. The error names `--rewrite`,
because a refusal that does not say what to do instead is one people route
around.

**This module is composition** (`03` §7's T4): every rule lives in
`adopt_map.quarantine`, so this file gets no dedicated tests and is swept by the
integration case and by S1.8's journeys.
"""

import json
from pathlib import Path
from typing import Annotated, Any, Final, NoReturn

import typer
from adopt_map.plugins import audit_source
from adopt_map.quarantine import QuarantinePaths, approve, reject, rewrite
from adopt_map.review_ledger import latest_by_extractor, m5_rewrite_rate, read_all

from adopt_cli.json_out import emit, emit_error
from adopt_obs import AdoptError, ErrorCode, map_exit_code_for

__all__ = ["app"]

app = typer.Typer(
    name="extractors",
    help="Review the extractors the agentic glue pass authored.",
    no_args_is_help=True,
)

AdoptDirOption = Annotated[
    Path,
    typer.Option("--adopt-dir", help="The project's `.adopt/` directory."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def _fail(error: AdoptError, *, as_json: bool) -> NoReturn:
    emit_error(error.to_envelope(), as_json=as_json)
    raise typer.Exit(map_exit_code_for(error.code))


@app.command("list")
def list_(adopt_dir: AdoptDirOption = Path(".adopt"), as_json: JsonOption = False) -> None:
    """Every module awaiting review, with its sandbox result and latest outcome."""
    paths = QuarantinePaths(adopt_dir=adopt_dir)
    latest = latest_by_extractor(read_all(paths.ledger))
    review = _review_row(paths)
    entries = []
    for module in sorted(paths.extractors.glob("*.py")):
        extractor_id = module.stem
        entry = latest.get(extractor_id)
        entries.append(
            {
                "extractor_id": extractor_id,
                "path": str(module),
                "outcome": entry.outcome if entry else "quarantined",
                "modified_since_generated": _modified(paths, extractor_id),
                "sandbox_result": review.get("sandbox_result")
                if review.get("extractor_id") == extractor_id
                else None,
                "fact_count": review.get("fact_count")
                if review.get("extractor_id") == extractor_id
                else None,
            }
        )
    emit(
        {"extractors": entries, "m5_rewrite_rate": m5_rewrite_rate(read_all(paths.ledger))},
        as_json=as_json,
        title="adopt extractors list",
    )


@app.command("audit")
def audit(
    target: Annotated[Path, typer.Argument(help="A module path, or an id under `.adopt/`.")],
    adopt_dir: AdoptDirOption = Path(".adopt"),
    kinds: Annotated[
        str | None, typer.Option("--kinds", help="Comma-separated declared kinds, for rule 8.")
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Run the `04` §6 step-1 audit over a module and print every finding."""
    module = (
        target if target.is_file() else QuarantinePaths(adopt_dir=adopt_dir).module(str(target))
    )
    if not module.is_file():
        _fail(
            AdoptError(
                ErrorCode.MAP_USAGE,
                message=f"{target} is neither a file nor an id in quarantine",
                hint="Pass a path to a module, or the id `adopt extractors list` shows.",
            ),
            as_json=as_json,
        )
    declared = [kind.strip() for kind in (kinds or "").split(",") if kind.strip()]
    try:
        findings = audit_source(module.read_text(encoding="utf-8"), declared_kinds=declared)
    except AdoptError as error:
        _fail(error, as_json=as_json)
    emit(
        {
            "module": str(module),
            "clean": not findings,
            "findings": [
                {"rule": finding.rule, "line": finding.line, "detail": finding.detail}
                for finding in findings
            ],
        },
        as_json=as_json,
        title="adopt extractors audit",
    )


@app.command("review")
def review(
    extractor_id: Annotated[str, typer.Argument(help="The id `adopt extractors list` shows.")],
    approve_it: Annotated[
        bool, typer.Option("--approve", help="Accept the module as generated.")
    ] = False,
    rewrite_it: Annotated[
        bool, typer.Option("--rewrite", help="Accept it after a human edit.")
    ] = False,
    reject_it: Annotated[bool, typer.Option("--reject", help="Delete it, with a reason.")] = False,
    reason: Annotated[
        str | None, typer.Option("--reason", help="Why. Required by --reject.")
    ] = None,
    adopt_dir: AdoptDirOption = Path(".adopt"),
    as_json: JsonOption = False,
) -> None:
    """Record a human decision about one quarantined module."""
    chosen = [
        name
        for name, flag in (("approve", approve_it), ("rewrite", rewrite_it), ("reject", reject_it))
        if flag
    ]
    if len(chosen) != 1:
        _fail(
            AdoptError(
                ErrorCode.MAP_USAGE,
                message=f"give exactly one of --approve, --rewrite, --reject (got {len(chosen)})",
                hint="`01` §8's autonomy matrix makes this the human gate. Three "
                "outcomes, one decision, recorded once in the review ledger.",
            ),
            as_json=as_json,
        )
    paths = QuarantinePaths(adopt_dir=adopt_dir)
    action = chosen[0]
    try:
        if action == "approve":
            approve(paths, extractor_id)
        elif action == "rewrite":
            rewrite(paths, extractor_id, reason=reason)
        else:
            if not reason:
                raise AdoptError(
                    ErrorCode.MAP_USAGE,
                    message="--reject needs --reason",
                    hint="The module is deleted and the ledger entry outlives it; the "
                    "reason is the only thing left saying why.",
                )
            reject(paths, extractor_id, reason=reason)
    except AdoptError as error:
        _fail(error, as_json=as_json)
    emit(
        {"extractor_id": extractor_id, "outcome": _OUTCOMES[action]},
        as_json=as_json,
        title="adopt extractors review",
    )


#: Verb -> the ledger outcome it records. `adopt_map.review_ledger.REVIEW_OUTCOMES`
#: is the vocabulary; this is the mapping from the flag a human typed to it.
_OUTCOMES: Final[dict[str, str]] = {
    "approve": "approved",
    "rewrite": "rewritten",
    "reject": "rejected",
}


def _modified(paths: QuarantinePaths, extractor_id: str) -> bool:
    """Whether the module's bytes differ from the generated original."""
    from adopt_map.quarantine import generated_digest

    sidecar = paths.digest_sidecar(extractor_id)
    module = paths.module(extractor_id)
    if not sidecar.is_file() or not module.is_file():
        return False
    return (
        generated_digest(module.read_text(encoding="utf-8"))
        != sidecar.read_text(encoding="utf-8").strip()
    )


def _review_row(paths: QuarantinePaths) -> dict[str, Any]:
    """The last pass's review row, or an empty mapping."""
    path = paths.quarantine_out / "review.json"
    if not path.is_file():
        return {}
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded
