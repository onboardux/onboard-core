"""Build 9's verb: `adopt handover` — the Verified Handover, six recorded steps.

v6.1 §6 Build 9. The engagement-closure event, composed almost entirely of what
already exists: packs are Build 4's, the agenda is Build 2/4's coverage join,
failed verification tasks are Build 3's escalations, the snapshot is Build 0's
export, and the ownership rows go through the port Build 7 built and exposed on
the local handle *for this build by name*.

**Every `adopt_handover` import happens inside a command body**, as Builds 1-8
do it: v6.1 §2.1 requires new verbs to register lazily so `CLI_COLD_START_MS`
holds, and `adopt version` must not pay for a fold it never runs.

**Seven verbs, one per recorded step plus `status`.** A single `next` would hide
which step an operator is asking for, and folding steps together would put a
file write and a store transaction under one exit code. Each step is its own
transaction and each may be re-run once its predecessor holds -- a second
elicitation pass, another verification round, a fresh snapshot -- because real
events do that and a checklist that forbade it would be worked around.

**`verify` exits 4 when a task failed.** Degraded-with-findings, the reading
`adopt probe diff` and `adopt map --check-expected` already carry: the command
worked, and it found something a human must see. A failed verification task is
not a failed command -- it is the product doing its job, and exit 1 would train
an FDE to stop reading the output.
"""

from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store

__all__ = ["app"]

app = typer.Typer(
    name="handover",
    help="The Verified Handover: freeze, elicit, pack, verify, snapshot, close.",
    no_args_is_help=True,
)

SystemOption = Annotated[
    str | None,
    typer.Option("--system", help="System id or slug. Freezes the whole system."),
]
ScopeOption = Annotated[
    str | None,
    typer.Option(
        "--scope",
        help="firm/engagement/system, optionally with /environment. Narrows to one environment.",
    ),
]
OwnerOption = Annotated[
    str, typer.Option("--receiving-owner", help="The group taking ownership of the system.")
]
IndividualOption = Annotated[
    bool,
    typer.Option("--individual", help="The receiving owner is a person, not a group."),
]
ActorOption = Annotated[str | None, typer.Option("--actor", help="Who is performing this step.")]
OutOption = Annotated[
    Path, typer.Option("--out", help="Directory to write into. Created if absent.")
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Path to the store.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
AudienceOption = Annotated[
    list[str] | None,
    typer.Option("--audience", help="Emit this audience. Repeatable; defaults to all four."),
]
FormatOption = Annotated[
    str, typer.Option("--format", help="md (canonical), docx or pdf for the derived copy.")
]
ChecklistOption = Annotated[
    Path, typer.Option("--checklist", help="The filled-in verification checklist (YAML).")
]
AcceptedByOption = Annotated[
    str, typer.Option("--accepted-by", help="Who accepted the handover for the receiving team.")
]


def _open(store: Path | None, *, writing: bool) -> Any:
    """Open the store, refusing a replica first when the verb writes."""
    if writing:
        from adopt_cli.commands._handover_support import refuse_if_replica

        refuse_if_replica(store)
    return open_configured_store(store, read_only=not writing)


@app.command("start")
def start(
    receiving_owner: OwnerOption,
    system: SystemOption = None,
    scope: ScopeOption = None,
    individual: IndividualOption = False,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Step 1 -- freeze the scope and record the opening position.

    Refuses a system that already has an un-closed handover: continuing the open
    one is almost always what was meant, and two records of one transfer is the
    fork a resumable checklist must not silently make.
    """
    from adopt_handover import HANDOVER_STARTED

    from adopt_cli.commands import _handover_support as support
    from adopt_obs import AdoptError, ErrorCode

    if not receiving_owner.strip():
        raise AdoptError(
            ErrorCode.HANDOVER_UNOWNED,
            message="--receiving-owner is empty",
            hint="v6.1 makes an event that closes with the system unowned a "
            "refusal rather than a warning, and an owner named at the start is "
            "what the close checks against. Name the group that will answer for "
            "this system.",
        )

    handle = _open(store, writing=True)
    try:
        resolved = support.resolve_system(handle, system=system, scope=scope)
        existing = support.has_open_handover(handle, resolved.system_id)
        if existing is not None:
            raise AdoptError(
                ErrorCode.HANDOVER_ALREADY_OPEN,
                message=f"handover {existing.id} is already open for {resolved.scope_path}",
                hint=f"Continue it: `{_next_verb(existing)}`. "
                "`adopt handover status` shows every step and what comes next.",
            )

        now = support.now_of(handle)
        opening = support.opening_position(handle, system=resolved, now=now)
        row = support.write_event(
            handle,
            event_type=HANDOVER_STARTED,
            system=resolved,
            detail=support.started_detail(
                system=resolved,
                receiving_owner=receiving_owner.strip(),
                is_group=not individual,
                opening=opening,
            ),
            actor=actor,
            now=now,
            handover_id=None,
        )
        payload: dict[str, Any] = {
            "handover_id": row.id,
            "system_id": resolved.system_id,
            "scope": resolved.scope_path,
            "receiving_owner": receiving_owner.strip(),
            "is_group": not individual,
            "opening": opening,
            "findings": _start_findings(opening),
        }
    finally:
        handle.close()

    if json_output:
        emit(payload, as_json=True)
        return
    lines = [
        f"Handover {payload['handover_id']} opened for {payload['scope']}.",
        f"  receiving owner: {payload['receiving_owner']}",
        f"  identities:      {opening['identities']} ({opening['covered']} covered)",
        f"  open gaps:       {opening['open_gaps']}",
        f"  open questions:  {opening['open_questions']}",
        f"  owner today:     {opening['current_owner'] or 'nobody'}",
        "",
        "Next: `adopt handover elicit` turns the open gaps into the SME agenda.",
    ]
    lines.extend(f"NOTE: {finding}" for finding in payload["findings"])
    typer.echo("\n".join(lines))


@app.command("elicit")
def elicit(
    system: SystemOption = None,
    scope: ScopeOption = None,
    out: OutOption = Path("./handover"),
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Step 2 -- the open gaps, as questions, grouped by the owner who answers them.

    This is where the saving lives: the agenda replaces the broad
    knowledge-transfer interview. An event that runs a general interview anyway
    has not used the product.
    """
    import hashlib

    from adopt_handover import HANDOVER_ELICITED, agenda, render_agenda, require

    from adopt_cli.commands import _handover_support as support

    handle = _open(store, writing=True)
    try:
        resolved = support.resolve_system(handle, system=system, scope=scope)
        record = support.require_open(handle, resolved.system_id)
        require(record, HANDOVER_ELICITED)

        now = support.now_of(handle)
        gaps = support.open_gaps(handle, system=resolved, now=now)
        conflicts = support.open_conflicts(handle, system=resolved)
        built = agenda(gaps, conflicts, now=now)
        document = render_agenda(built)

        out.mkdir(parents=True, exist_ok=True)
        agenda_path = out / "elicitation.md"
        agenda_path.write_text(document, encoding="utf-8", newline="\n")

        detail = {
            "gaps": built.item_count,
            "conflicts": len(conflicts),
            "by_owner": built.by_owner(),
            "agenda_path": str(agenda_path),
            "agenda_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        }
        support.write_event(
            handle,
            event_type=HANDOVER_ELICITED,
            system=resolved,
            detail=detail,
            actor=actor,
            now=now,
            handover_id=record.id,
        )
        by_owner = built.by_owner()
        payload = {"handover_id": record.id, "agenda": str(agenda_path), **detail}
    finally:
        handle.close()

    if json_output:
        emit(payload, as_json=True)
        return
    typer.echo(
        "\n".join(
            [
                f"{payload['gaps']} open gaps and {payload['conflicts']} conflicts "
                f"-> {payload['agenda']}",
                *(f"  {owner}: {count}" for owner, count in sorted(by_owner.items())),
                "",
                "Ask only what is on the agenda. Capture answers with `adopt answer` "
                "or `adopt ingest`, then run `adopt handover pack`.",
            ]
        )
    )


@app.command("pack")
def pack(
    system: SystemOption = None,
    scope: ScopeOption = None,
    audience: AudienceOption = None,
    out: OutOption = Path("./handover"),
    format_name: FormatOption = "md",
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Step 3 -- one pack per audience, every section stamped and dated.

    The same assembler `adopt pack` runs, through the same shared functions, so
    the document a client receives is the one the FDE reviewed. Drafting is
    deliberately **not** here: `adopt pack --draft-missing` and `adopt review`
    run before this step, so what the handover emits is what a human has already
    confirmed or explicitly left stamped UNVERIFIED.
    """
    from adopt_handover import AUDIENCES, HANDOVER_PACK_EMITTED, converter_for, require

    from adopt_cli.commands import _handover_support as support
    from adopt_cli.commands import _pack_support as pack_support

    converter = converter_for(format_name)
    wanted = tuple(audience) if audience else AUDIENCES

    handle = _open(store, writing=True)
    try:
        resolved = support.resolve_system(handle, system=system, scope=scope)
        record = support.require_open(handle, resolved.system_id)
        require(record, HANDOVER_PACK_EMITTED)
        now = support.now_of(handle)

        emitted: list[dict[str, Any]] = []
        for name in wanted:
            assembled = pack_support.assemble_pack(
                handle,
                audience=name,
                system_id=resolved.system_id,
                environment_id=resolved.environment_id,
            )
            written = pack_support.write_pack(
                assembled, out, audience=name, selected=None, converter=converter
            )
            unverified = sum(1 for section in assembled.sections if section.drafted)
            stamps = sorted(
                {stamped.stamp for section in assembled.sections for stamped in section.revisions}
            )
            detail = {
                "audience": name,
                "markdown_path": str(written["markdown_path"]),
                "markdown_sha256": written["sha256"],
                "sidecar_path": str(written["sidecar_path"]),
                "sections": len(assembled.sections),
                "unverified_sections": unverified,
                "stamps": stamps,
                "format": format_name,
                "derived_path": None
                if written["derived_path"] is None
                else str(written["derived_path"]),
            }
            support.write_event(
                handle,
                event_type=HANDOVER_PACK_EMITTED,
                system=resolved,
                detail=detail,
                actor=actor,
                now=now,
                handover_id=record.id,
            )
            support.write_value_event(
                handle,
                system=resolved,
                source_ref=written["sha256"],
                actor=actor,
                now=now,
            )
            emitted.append(detail)

        payload = {"handover_id": record.id, "packs": emitted}
    finally:
        handle.close()

    if json_output:
        emit(payload, as_json=True)
        return
    lines = [f"{len(emitted)} packs emitted into {out}:"]
    for entry in emitted:
        flag = (
            f" -- {entry['unverified_sections']} UNVERIFIED section(s)"
            if entry["unverified_sections"]
            else ""
        )
        lines.append(f"  {entry['audience']}: {entry['markdown_path']}{flag}")
    lines.extend(
        [
            "",
            "Next: the receiving team works a checklist against these, then `adopt handover verify`.",
        ]
    )
    typer.echo("\n".join(lines))


@app.command("verify")
def verify(
    checklist: ChecklistOption,
    system: SystemOption = None,
    scope: ScopeOption = None,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Step 4 -- record what the receiving team could and could not do.

    Every failed task becomes an **open question** carrying its text, answerable
    with `adopt answer` in the room. Failures are findings about the pack, not
    marks against people, and nothing here records who failed anything.

    Exits `4` when any task failed: the command worked and found something.
    """
    import yaml
    from adopt_handover import (
        HANDOVER_VERIFIED,
        counts,
        failures,
        parse_checklist,
        question_for,
        require,
    )

    from adopt_cli.commands import _handover_support as support
    from adopt_obs import AdoptError, ErrorCode, ExitCode

    try:
        raw = yaml.safe_load(checklist.read_text(encoding="utf-8"))
    except OSError as error:
        raise AdoptError(
            ErrorCode.HANDOVER_CHECKLIST_INVALID,
            message=f"{checklist} could not be read: {error}",
            hint="Point --checklist at the filled-in YAML the receiving team worked through.",
        ) from error
    except yaml.YAMLError as error:
        raise AdoptError(
            ErrorCode.HANDOVER_CHECKLIST_INVALID,
            message=f"{checklist} is not valid YAML: {error}",
            hint="A checklist is a mapping with a `tasks:` list, each task carrying `id`, "
            "`task` and `outcome` (pass | fail | skipped). The annotated example is "
            "docs/handover-checklist.example.yaml in the source tree, or "
            "https://github.com/onboardux/onboard-core/blob/main/"
            "docs/handover-checklist.example.yaml.",
        ) from error
    if not isinstance(raw, dict):
        raise AdoptError(
            ErrorCode.HANDOVER_CHECKLIST_INVALID,
            message=f"{checklist} is not a mapping",
            hint="A checklist is a mapping with a `tasks:` list.",
        )

    parsed = parse_checklist(raw)
    passed, failed, skipped = counts(parsed)

    handle = _open(store, writing=True)
    try:
        resolved = support.resolve_system(handle, system=system, scope=scope)
        record = support.require_open(handle, resolved.system_id)
        require(record, HANDOVER_VERIFIED)
        now = support.now_of(handle)
        round_number = len(record.verification_rounds) + 1

        outcomes: list[dict[str, Any]] = []
        opened: list[dict[str, str]] = []
        with handle.backend.transaction():
            for task in parsed.tasks:
                escalation_id: str | None = None
                if task in failures(parsed):
                    escalation_id = handle.governance().open_escalation(
                        system_id=resolved.system_id,
                        branch="ungrounded",
                        question=question_for(task),
                    )
                    opened.append({"task_id": task.id, "escalation_id": escalation_id})
                outcomes.append(
                    {"id": task.id, "outcome": task.outcome, "escalation_id": escalation_id}
                )

            detail = {
                "round": round_number,
                "tasks": len(parsed.tasks),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "outcomes": outcomes,
            }
            support.write_event(
                handle,
                event_type=HANDOVER_VERIFIED,
                system=resolved,
                detail=detail,
                actor=actor,
                now=now,
                handover_id=record.id,
            )
        payload = {
            "handover_id": record.id,
            "round": round_number,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "escalations": opened,
        }
    finally:
        handle.close()

    if json_output:
        emit(payload, as_json=True)
    else:
        lines = [
            f"Round {round_number}: {passed} passed, {failed} failed, {skipped} skipped.",
        ]
        for entry in opened:
            lines.append(f"  {entry['task_id']} -> open question {entry['escalation_id']}")
        if failed:
            lines.extend(
                [
                    "",
                    "Each failure is an open question, not a mark against anyone. "
                    "Answer what you can in the room with `adopt answer <id> --text ...`; "
                    "anything still open transfers with a named owner at close.",
                ]
            )
        typer.echo("\n".join(lines))

    if failed:
        raise typer.Exit(ExitCode.DEGRADED_WITH_FINDINGS)


@app.command("snapshot")
def snapshot(
    system: SystemOption = None,
    scope: ScopeOption = None,
    out: OutOption = Path("./handover/acceptance"),
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Step 5 -- the acceptance snapshot: an export bundle and a recorded digest.

    Both parties hold it, and the client can **check** theirs: `adopt import`
    then `adopt export` on their own machine reproduces the same digest, because
    it is computed over the bundle's byte-stable table digests and nothing that
    varies between exports.
    """
    from adopt_handover import (
        ACCEPTANCE_FILENAME,
        HANDOVER_SNAPSHOT_TAKEN,
        acceptance_digest,
        render_record,
        require,
    )

    from adopt_cli.commands import _handover_support as support
    from adopt_cli.store_option import writer_identity
    from adopt_export import write_bundle

    handle = _open(store, writing=True)
    try:
        resolved = support.resolve_system(handle, system=system, scope=scope)
        record = support.require_open(handle, resolved.system_id)
        require(record, HANDOVER_SNAPSHOT_TAKEN)
        now = support.now_of(handle)

        out.mkdir(parents=True, exist_ok=True)
        bundle_path = out / "bundle"
        manifest = write_bundle(handle.export_records(), bundle_path, written_by=writer_identity())
        digest = acceptance_digest((entry.name, entry.sha256) for entry in manifest.tables)

        detail = {
            "out_dir": str(out),
            "bundle_path": str(bundle_path),
            "acceptance_path": str(out / ACCEPTANCE_FILENAME),
            "bundle_digest": digest,
            "tables": len(manifest.tables),
            "rows": sum(entry.rows for entry in manifest.tables),
            "export_version": manifest.export_version,
        }
        support.write_event(
            handle,
            event_type=HANDOVER_SNAPSHOT_TAKEN,
            system=resolved,
            detail=detail,
            actor=actor,
            now=now,
            handover_id=record.id,
        )
        _write_acceptance(handle, resolved, out=out, render=render_record, now=now)
        payload = {"handover_id": record.id, **detail}
    finally:
        handle.close()

    if json_output:
        emit(payload, as_json=True)
        return
    typer.echo(
        "\n".join(
            [
                f"Snapshot taken: {payload['rows']} rows across {payload['tables']} tables.",
                f"  bundle:     {payload['bundle_path']}",
                f"  acceptance: {payload['acceptance_path']}",
                f"  digest:     {payload['bundle_digest']}",
                "",
                "Give the client both. They can verify the digest from their own copy "
                "with `adopt import` then `adopt export`.",
                "Next: `adopt handover close --accepted-by <their lead>`.",
            ]
        )
    )


@app.command("close")
def close(
    accepted_by: AcceptedByOption,
    system: SystemOption = None,
    scope: ScopeOption = None,
    out: OutOption | None = None,
    actor: ActorOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Step 6 -- transfer ownership, and transfer what is still open with it.

    Refuses to leave the system unowned, and the refusal rolls the whole close
    back. Nothing here resolves a gap, answers a question or clears a conflict:
    unresolved items transfer with named owners rather than being closed to look
    complete.
    """
    from adopt_handover import render_record

    from adopt_cli.commands import _handover_support as support

    handle = _open(store, writing=True)
    try:
        resolved = support.resolve_system(handle, system=system, scope=scope)
        record = support.require_open(handle, resolved.system_id)
        now = support.now_of(handle)
        detail = support.close_handover(
            handle,
            record,
            system=resolved,
            accepted_by=accepted_by,
            actor=actor,
            now=now,
        )
        destination = out or _snapshot_dir(record)
        acceptance_path = _write_acceptance(
            handle, resolved, out=destination, render=render_record, now=now
        )
        payload = {
            "handover_id": record.id,
            "ownership_assignment_id": detail["ownership_assignment_id"],
            "current_owner": support.current_owner_of(handle, system_id=resolved.system_id, at=now),
            "transferred": detail["transferred"],
            "acceptance_path": None if acceptance_path is None else str(acceptance_path),
        }
    finally:
        handle.close()

    if json_output:
        emit(payload, as_json=True)
        return
    transferred = payload["transferred"]
    typer.echo(
        "\n".join(
            [
                f"Handover {payload['handover_id']} closed. "
                f"{payload['current_owner']} now owns {resolved.scope_path}.",
                f"  gaps transferred:  {len(transferred['gaps_assigned'])} "
                f"(+{len(transferred['gaps_kept_owner'])} already owned)",
                f"  questions open:    {len(transferred['questions'])}",
                f"  conflicts open:    {transferred['conflicts']}",
                f"  acceptance record: {payload['acceptance_path']}",
                "",
                "Open items transferred with owners rather than being closed. "
                "`adopt handover status` is the record.",
            ]
        )
    )


@app.command("status")
def status(
    system: SystemOption = None,
    scope: ScopeOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Every step recorded, what comes next, and what is still open.

    Reads only, and answers for a closed handover as readily as an open one --
    the record is what both parties keep, and it must still describe itself a
    year later.
    """
    from adopt_handover import VERB_FOR, record_payload

    from adopt_cli.commands import _handover_support as support
    from adopt_cli.store_option import writer_identity

    handle = _open(store, writing=False)
    try:
        resolved = support.resolve_system(handle, system=system, scope=scope)
        records = support.records_for(handle, resolved.system_id)
        if not records:
            payload: dict[str, Any] = {
                "state": "none",
                "system_id": resolved.system_id,
                "scope": resolved.scope_path,
            }
        else:
            record = records[0]
            now = support.now_of(handle)
            gaps = support.open_gaps(handle, system=resolved, now=now)
            questions = support.open_questions(handle, system_id=resolved.system_id)
            conflicts = support.open_conflicts(handle, system=resolved)
            owner = support.current_owner_of(handle, system_id=resolved.system_id, at=now)
            payload = {
                "state": "closed" if record.is_closed else "open",
                "handover_id": record.id,
                "system_id": resolved.system_id,
                "scope": record.scope,
                "receiving_owner": record.receiving_owner,
                "current_owner": owner,
                "steps": support.record_detail_for_status(record),
                "next_step": record.next_step,
                "next_verb": None if record.next_step is None else VERB_FOR[record.next_step],
                "malformed_events": list(record.malformed),
                "open_items": {
                    "gaps": len(gaps),
                    "questions": len(questions),
                    "conflicts": len(conflicts),
                },
                "record": record_payload(
                    record,
                    open_gaps=gaps,
                    open_questions=questions,
                    open_conflicts=conflicts,
                    system_slug=resolved.slug,
                    system_owner=owner,
                    written_by=writer_identity(),
                ),
            }
    finally:
        handle.close()

    if json_output:
        emit(payload, as_json=True)
        return
    if payload["state"] == "none":
        typer.echo(
            f"No handover has been started for {payload['scope']}.\n"
            "`adopt handover start --receiving-owner <group>` opens one."
        )
        return
    lines = [
        f"Handover {payload['handover_id']} [{payload['state']}] {payload['scope']}",
        f"  receiving owner: {payload['receiving_owner']}",
        f"  owner now:       {payload['current_owner'] or 'nobody'}",
        "",
    ]
    for step in payload["steps"]:
        mark = "x" if step["done"] else " "
        when = f"  {step['at']}" if step["at"] else ""
        who = f" by {step['by']}" if step["by"] else ""
        lines.append(f"  [{mark}] {step['step']}{when}{who}")
    open_items = payload["open_items"]
    lines.extend(
        [
            "",
            f"  open: {open_items['gaps']} gaps, {open_items['questions']} questions, "
            f"{open_items['conflicts']} conflicts",
        ]
    )
    if payload["next_verb"]:
        lines.append(f"  next: {payload['next_verb']}")
    if payload["malformed_events"]:
        lines.append(
            f"  WARNING: {len(payload['malformed_events'])} event(s) carry an "
            "unreadable detail; the trail was edited outside this product."
        )
    typer.echo("\n".join(lines))


def _write_acceptance(
    handle: Any, resolved: Any, *, out: Path | None, render: Any, now: Any
) -> Path | None:
    """Render `acceptance.json` beside the bundle it describes.

    Re-rendered at close so the transfer block is filled in. It is a rendering of
    the audit trail rather than a second copy of it, so re-running this can only
    ever produce what the store already says.
    """
    from adopt_handover import ACCEPTANCE_FILENAME

    from adopt_cli.commands import _handover_support as support
    from adopt_cli.store_option import writer_identity

    if out is None:
        return None
    record = support.records_for(handle, resolved.system_id)[0]
    owner = support.current_owner_of(handle, system_id=resolved.system_id, at=now)
    document = render(
        record,
        open_gaps=support.open_gaps(handle, system=resolved, now=now),
        open_questions=support.open_questions(handle, system_id=resolved.system_id),
        open_conflicts=support.open_conflicts(handle, system=resolved),
        system_slug=resolved.slug,
        system_owner=owner,
        written_by=writer_identity(),
    )
    out.mkdir(parents=True, exist_ok=True)
    path = out / ACCEPTANCE_FILENAME
    path.write_text(document, encoding="utf-8", newline="\n")
    return path


def _snapshot_dir(record: Any) -> Path | None:
    """Where the snapshot wrote, so `close` re-renders the record beside it."""
    recorded = record.snapshot.get("out_dir")
    return Path(str(recorded)) if recorded else None


def _next_verb(record: Any) -> str:
    from adopt_handover import VERB_FOR

    step = record.next_step
    return "adopt handover status" if step is None else VERB_FOR[step]


def _start_findings(opening: dict[str, Any]) -> list[str]:
    """What an operator should know before running this event.

    The sales-motion note, made operational: v6.1 §6 records that the handover
    *"cannot be sold into an engagement that is already ending"* because its
    value is proportional to accumulated state. A store with no confirmed
    knowledge is exactly that case, and saying so at `start` is more use than
    saying it in a document nobody opens at closing time.
    """
    findings: list[str] = []
    if not opening["confirmed_items"]:
        findings.append(
            "this system has no confirmed knowledge, so the packs will be an "
            "inventory plus gaps. The handover event assumes the store filled up "
            "during the engagement -- run `adopt ingest`, `adopt harvest` and the "
            "review queue first if there is time."
        )
    if opening["current_owner"] is None:
        findings.append(
            "nobody owns this system today. That is not an error, and the close "
            "will assign the receiving owner -- but check that no assignment was "
            "meant to exist."
        )
    return findings
