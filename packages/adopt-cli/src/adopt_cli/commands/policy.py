"""`adopt probe manifest validate` and `adopt envelope validate` -- contracts §14.

Both exit `3` on a violation, because both refusals are `policy` category and
`02` §13 maps `policy` to `3`. Neither command opens a store to do its work: a
capability manifest and an outbound envelope are documents, and validating one
must be possible in a CI job with no `.adopt/` directory anywhere.

**The envelope command is the exception that proves the boundary rule.** It needs
a `BoundaryView`, and the boundary is *stored* -- so `--scope` reads the declared
one. Without a scope it validates against the default metadata-only boundary,
which is the strictest one that exists: that fallback can only ever reject more
than the real boundary would, never less, so a caller who forgets `--scope` gets
a conservative answer rather than a permissive one.
"""

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store
from adopt_detect import DEFAULT_OUTBOUND_CATEGORIES, BoundaryView
from adopt_obs import AdoptError, ErrorCode, now
from adopt_policy import validate_capability_manifest, validate_envelope
from adopt_scope import ScopePath

__all__ = ["envelope_app", "probe_app"]

probe_app = typer.Typer(name="probe", help="Probe definitions and their capability manifests.")
manifest_app = typer.Typer(name="manifest", help="Capability manifests.")
probe_app.add_typer(manifest_app)
envelope_app = typer.Typer(name="envelope", help="Outbound envelopes.")

FileArgument = Annotated[Path, typer.Argument(help="The document to validate.")]
ScopeOption = Annotated[
    str | None,
    typer.Option(
        "--scope",
        help="firm/engagement/system[/environment], to validate against the boundary "
        "declared for that scope. Omitted, the strictest default boundary is used.",
    ),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def _read_document(path: Path, *, as_yaml: bool) -> Any:
    """Parse a YAML or JSON document, refusing rather than guessing.

    Raises:
        AdoptError: ``MANIFEST_INVALID`` when the file is unreadable or malformed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AdoptError(
            ErrorCode.MANIFEST_INVALID,
            message=f"cannot read {path}",
            hint="Check the path.",
        ) from error
    try:
        return yaml.safe_load(text) if as_yaml else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as error:
        raise AdoptError(
            ErrorCode.MANIFEST_INVALID,
            message=f"{path} is not a well-formed document: {error}",
            hint="A document that cannot be parsed is refused rather than partially "
            "validated -- an unparseable declaration is not a permissive one.",
        ) from error


def _default_boundary() -> BoundaryView:
    """The strictest boundary: metadata-only, nothing else permitted.

    Used when no scope is named. It cannot be more permissive than a declared
    boundary, because `metadata_only` is the floor every boundary starts from
    (contracts §8 rule 1) -- so validating against it is conservative by
    construction rather than by luck.
    """
    return BoundaryView(
        boundary_id="",
        system_id="",
        environment_id=None,
        tier="T0",
        archetype=None,
        knowledge_plane_location="customer",
        control_plane_location="vendor",
        permitted_outbound_categories=DEFAULT_OUTBOUND_CATEGORIES,
        unavailable_capabilities=(),
        contractual_approval_ref=None,
        declared_at=now(),
        decline_recommended=False,
        archetype_floor_violated=False,
    )


def _boundary_for(scope: str | None, store: Path | None) -> BoundaryView:
    if scope is None:
        return _default_boundary()
    parsed = ScopePath.parse(scope)
    with open_configured_store(store) as handle:
        resolved = handle.scope().resolve(parsed)
        if resolved.system is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message="--scope must name at least firm/engagement/system",
                hint="A boundary is declared for a system.",
            )
        environment_id = None if resolved.environment is None else resolved.environment.id
        row = handle.boundary().current(system_id=resolved.system.id, environment_id=environment_id)
        if row is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message=f"no boundary is declared for {scope}",
                hint="Run `adopt init` or `adopt boundary --scope ...` first. Validating "
                "against a boundary that was never declared would mean inventing what "
                "the client agreed to.",
            )
        return BoundaryView.of(row, archetype=None)


@manifest_app.command("validate")
def validate_manifest(file: FileArgument, json_output: JsonOption = False) -> None:
    """Validate a probe capability manifest. Exits `3` on a violation."""
    verdict = validate_capability_manifest(_read_document(file, as_yaml=True))
    emit(
        {
            "valid": True,
            "violations": [],
            "probe_id": verdict.probe_id,
            "declared_hosts": list(verdict.declared_hosts),
            "side_effect_policy": verdict.side_effect_policy,
            # Returned, never judged: expiry enforcement is item 8 (PRD F11.5).
            "approval_expires_at": verdict.approval_expires_at,
        },
        as_json=json_output,
        title="adopt probe manifest validate",
    )


@envelope_app.command("validate")
def validate_outbound(
    file: FileArgument,
    scope: ScopeOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Validate an outbound envelope against the boundary. Exits `3` on a violation."""
    document = _read_document(file, as_yaml=False)
    if not isinstance(document, dict):
        raise AdoptError(
            ErrorCode.MANIFEST_INVALID,
            message=f"{file} is a {type(document).__name__}, not a JSON object",
            hint="Contracts §8 gives the envelope shape.",
        )
    validate_envelope(document, _boundary_for(scope, store))
    emit(
        {"valid": True, "violations": []},
        as_json=json_output,
        title="adopt envelope validate",
    )
