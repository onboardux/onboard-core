"""`web.celery.jobs` -- task declarations and beat schedules.

`02` §3.1 gives `job` the runner as its namespace (`celery`) and *"task or schedule
identifier"* as the key. A Celery task's identifier is its **dotted path** --
`orders.tasks.reconcile_payments` -- because that is what Celery registers it as
and what appears in a broker message. Keying on the bare function name would fork
two apps' `cleanup` tasks into one identity.

**Two declarations, one referent.** A task is declared by `@shared_task` on a
function; its *schedule* is declared separately in a `beat_schedule` mapping that
names the same dotted path. Emitting the schedule as its own identity would
double-count, so a beat entry contributes the `schedule` attribute to the task it
names, and only mints on its own when it names a task this tree does not declare
-- which is a real case (a task in another service) and is recorded rather than
dropped.
"""

from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, FactRelation, SourceRef, SurfaceFact
from tree_sitter import Node

from adopt_extractors_web._grammar import matches, node_text, parse, string_value

__all__ = ["MANIFEST", "CeleryJobsExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.celery.jobs",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["job"],
    method="grammar",
)

_NAMESPACE: Final[str] = "celery"

#: A decorated function -- `@shared_task(queue="billing")` or bare `@app.task`.
_TASK_PATTERN: Final[str] = """
(decorated_definition
  (decorator) @decorator
  definition: (function_definition name: (identifier) @name)) @decorated
"""

#: `"orders.tasks.reconcile": {"task": "...", "schedule": crontab(hour=3)}`
_BEAT_PATTERN: Final[str] = """
(pair key: (string) @key value: (dictionary) @body) @pair
"""

_TASK_MARKERS: Final[tuple[str, ...]] = ("shared_task", ".task", "periodic_task")


class CeleryJobsExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return any(Path(root).rglob("*.py"))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `job` per declared task, with its beat schedule attached."""
        schedules = _beat_schedules(ctx)
        declared: set[str] = set()
        for entry in ctx.files(language="python"):
            ctx.budget.check()
            text = ctx.text(entry)
            if not any(marker in text for marker in _TASK_MARKERS):
                continue
            root, data = parse("python", text)
            module = ".".join(
                [*PurePosixPath(entry.path).parts[:-1], PurePosixPath(entry.path).stem]
            )
            for fact in _tasks(root, data, module, entry.path, entry.blob_sha, schedules):
                declared.add(fact.local_key)
                yield fact
        yield from _orphan_schedules(schedules, declared)


def _beat_schedules(ctx: ExtractorContext) -> dict[str, tuple[str, str]]:
    """`{dotted task: (schedule expression, declaring path)}`.

    Read in its own pass because a beat schedule lives in a settings module that
    is not itself a task module, so the pass over task files would never see it.
    """
    found: dict[str, tuple[str, str]] = {}
    for entry in ctx.files(language="python"):
        ctx.budget.check()
        text = ctx.text(entry)
        if "beat_schedule" not in text:
            continue
        root, data = parse("python", text)
        for capture in matches("python", _BEAT_PATTERN, root):
            bodies = capture.get("body") or []
            if not bodies:
                continue
            body = node_text(bodies[0], data)
            task = _pair_value(bodies[0], data, "task")
            schedule = _pair_value(bodies[0], data, "schedule") or _schedule_call(body)
            if task is not None and schedule is not None:
                found[task] = (schedule, entry.path)
    return found


def _pair_value(body: Node, data: bytes, key: str) -> str | None:
    for child in body.children:
        if child.type != "pair":
            continue
        name = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if name is None or value is None:
            continue
        if string_value(name, data) != key:
            continue
        return (
            string_value(value, data) if value.type == "string" else node_text(value, data).strip()
        )
    return None


def _schedule_call(body: str) -> str | None:
    marker = "crontab("
    if marker in body:
        start = body.index(marker)
        end = body.find(")", start)
        return body[start : end + 1] if end != -1 else None
    return None


def _tasks(
    root: Node,
    data: bytes,
    module: str,
    path: str,
    blob_sha: str,
    schedules: dict[str, tuple[str, str]],
) -> Iterator[SurfaceFact]:
    for capture in matches("python", _TASK_PATTERN, root):
        names = capture.get("name") or []
        decorators = capture.get("decorator") or []
        if not names:
            continue
        decorator_text = " ".join(node_text(node, data) for node in decorators)
        if not any(marker in decorator_text for marker in _TASK_MARKERS):
            continue
        name = node_text(names[0], data)
        dotted = f"{module}.{name}"
        schedule = schedules.get(dotted)
        yield SurfaceFact(
            identity_kind="job",
            namespace=_NAMESPACE,
            local_key=dotted,
            title=name,
            attributes={
                "schedule": None if schedule is None else schedule[0],
                "target_symbol": dotted,
                "queue": _keyword(decorator_text, "queue"),
                "retry_policy": _retry_policy(decorator_text),
                "timeout_seconds": _int_keyword(decorator_text, "time_limit"),
            },
            relations=[
                FactRelation(
                    predicate="scheduled_by",
                    target_kind="symbol",
                    target_namespace="python",
                    target_local_key=dotted,
                )
            ],
            source_refs=[
                SourceRef(path=path, start_line=names[0].start_point[0] + 1, blob_sha=blob_sha)
            ],
        )


def _orphan_schedules(
    schedules: dict[str, tuple[str, str]], declared: set[str]
) -> Iterator[SurfaceFact]:
    """Beat entries naming a task this tree does not declare.

    Real and worth minting: a schedule pointing at another service's task is a
    behaviour this system owns, and dropping it would understate what the system
    does on a timer.
    """
    for dotted in sorted(set(schedules) - declared):
        schedule, path = schedules[dotted]
        yield SurfaceFact(
            identity_kind="job",
            namespace=_NAMESPACE,
            local_key=dotted,
            title=dotted.rsplit(".", 1)[-1],
            attributes={"schedule": schedule, "target_symbol": dotted},
            source_refs=[SourceRef(path=path)],
        )


def _keyword(text: str, name: str) -> str | None:
    marker = f"{name}="
    if marker not in text:
        return None
    tail = text[text.index(marker) + len(marker) :].strip()
    for quote in ('"', "'"):
        if tail.startswith(quote) and quote in tail[1:]:
            return tail[1 : tail.index(quote, 1)]
    return None


def _int_keyword(text: str, name: str) -> int | None:
    marker = f"{name}="
    if marker not in text:
        return None
    digits = ""
    for character in text[text.index(marker) + len(marker) :].strip():
        if not character.isdigit():
            break
        digits += character
    return int(digits) if digits else None


def _retry_policy(text: str) -> str | None:
    """Whatever the decorator declares about retrying, as written.

    `02` §4.2 puts the retry policy in the `job` **semantic** projection, so a
    change to it must change the digest -- which means recording the declaration
    rather than a boolean summary of it.
    """
    parts = [
        f"{name}={_keyword(text, name) or _int_keyword(text, name)}"
        for name in ("autoretry_for", "max_retries", "retry_backoff")
        if f"{name}=" in text
    ]
    return ",".join(parts) if parts else None
