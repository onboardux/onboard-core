"""Registration, manifest validation and the static audit -- `03` §5.8.

Three things live here and they are deliberately one module, because they are
one question: **may this code run against a client tree, and what is it allowed
to say?**

1. **Registration.** An extractor is admitted by manifest. Its `kinds` are
   checked against Build 0's closed `IdentityKind` enum at registration rather
   than at first emission, so an extractor that would have widened the vocabulary
   never reaches a tree at all (`02` §7 obligation 4, `01` F2.2).
2. **Pack gating.** `extractors.<pack>.enabled`, default **off** beyond `common`
   until each pack's sprint exits (`01` F7.6, `01` §9).
3. **The static audit.** Nine rules over the module's AST. It is what makes
   `01` F7.2's static-only guarantee a property of the code rather than a claim
   about intent, and it is the gate `04` §6's quarantine pipeline reuses for
   agent-authored extractors in S1.7.

**The audit reads source, never imports it.** `ast.parse` on text: a module that
detonates on import (the `poisoned-import` fixture) is audited without ever being
executed, which is the whole point. An audit that imported to introspect would
have to run the thing it exists to refuse.

**Rule 9 reads the scheme from `adopt_const.URI_SCHEME` and never restates it**
(B1-CR-26). The two audits this rule replaces both scanned for a literal
`adopt://`, which Build 0 CR-06 had already made unreachable -- so generated code
minting a forged `onboard-v1://` URI would have passed them as clean. A rule that
hard-codes the value it forbids goes blind on the day that value changes, and
nothing tells you.
"""

import ast
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Final, get_args

from adopt_const import URI_SCHEME
from adopt_map.schemas.surface import Extractor, ExtractorManifest
from adopt_model._enums import IdentityKind
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "AUDIT_RULES",
    "DEFAULT_ENABLED_PACKS",
    "PERMITTED_IMPORT_ROOTS",
    "AuditFinding",
    "ExtractorRegistry",
    "audit_source",
    "manifests",
    "require_clean",
]

#: Build 0's closed kind enum, read from the `Literal` rather than restated --
#: the same move `adopt_map.schemas.attributes` makes for its registry check. A
#: second copy of the thirteen members is a second thing to keep in step.
_IDENTITY_KINDS: Final[frozenset[str]] = frozenset(get_args(IdentityKind))

#: The packs on by default. Every other pack flips at its own sprint exit gate
#: (`01` §9), so a half-built pack cannot reach a client tree because somebody
#: merged it early.
#:
#: **`web` joins at S1.4** and **`ai` at S1.5**, which is what `01` §9's
#: *"`extractors.web.enabled` | on from S1.4"* and *"`extractors.ai.enabled` |
#: off → on at S1.5 exit"* rows mean. `data`,
#: `lowcode` and `platform` stay off until S1.6 passes its own exit gates. An
#: operator overrides either way through `[extractors]` in `.adopt/config.toml`
#: (`adopt_cli.map_config`) -- which S1.4 also had to build, because S1.1's
#: checkbox for it was never implemented and until then no configuration could
#: reach this set at all.
#:
#: **`ai`'s gate is a measurement, not a date.** `01` §9 flips it on outside-VCS
#: recall at or above `MAP_OUTSIDE_VCS_RECALL_FLOOR`, and
#: `scripts/label_eval.py --fixture langgraph-support` reports **1.000** (8 of 8
#: labeled outside-VCS identities) against a labeled set derived from the
#: fixture's specification. Had it come in below, this line would still read
#: `{"common", "web"}` and the sprint would have said so.
DEFAULT_ENABLED_PACKS: Final[frozenset[str]] = frozenset({"common", "web", "ai"})

#: What an extractor may import. **An allowlist, not a deny-list**, because a
#: deny-list is a list of the ways somebody already thought of: `socket` is
#: obvious, `asyncio` reaching a transport is not, and neither is a client
#: package that happens to share a name with a standard module.
#:
#: Absent on purpose: `os` and `sys` (an extractor gets the file index, not the
#: process), `subprocess` (the `adopt_map.execseam` allowlist is the only route,
#: and it is the framework's to call), `socket`/`http`/`urllib`/`ftplib`
#: (`01` N7), `importlib`/`ctypes`/`pickle` (all load-and-execute), and `shutil`
#: (it writes). A pack needing an addition states the reason here rather than
#: silencing the rule at its own call site.
#:
#: **S1.4's four additions, each with its reason** (`03` §2, B1-CR-65). All four
#: are *parsers*: they turn text into a tree and hand it back. That is the test an
#: addition has to pass -- not "is it on our dependency list", which
#: `openapi-spec-validator` would also have passed while adding an execution
#: surface nobody needed:
#:
#: * `tree_sitter` / `tree_sitter_language_pack` -- the grammar rung. Parsing is
#:   the whole API surface; there is no evaluator to reach.
#: * `ast_grep_py` -- structural patterns over the same trees. Matching only.
#: * `graphql` (`graphql-core`) -- SDL parsing. Note it *does* ship an executor,
#:   which is why obligation 1's other rules still apply to a module importing
#:   it: `graphql.graphql()` needs a resolver map, no extractor builds one, and
#:   the audit's `dynamic_execution` rules do not depend on this list.
#:
#: **`sqlalchemy` is deliberately not here** even though `03` §2 named it: its
#: metadata populates only from a live `Engine` or by importing client models,
#: so admitting it would have widened the allowlist for a capability obligation 1
#: forbids using (OD-12).
PERMITTED_IMPORT_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "adopt_const",
        "adopt_map",
        "adopt_model",
        "ast_grep_py",
        "base64",
        "graphql",
        "collections",
        "configparser",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fnmatch",
        "functools",
        "hashlib",
        "itertools",
        "json",
        "math",
        "pathlib",
        "re",
        "string",
        "textwrap",
        "tomllib",
        "tree_sitter",
        "tree_sitter_language_pack",
        "typing",
        "unicodedata",
        "yaml",
    }
)

#: Callables an extractor may never name. `exec`, `eval`, `compile` and
#: `__import__` execute; the rest reach a process, a socket or a shell.
_FORBIDDEN_CALLS: Final[dict[str, str]] = {
    "exec": "executes code",
    "eval": "evaluates code",
    "compile": "compiles code for execution",
    "__import__": "dynamically imports a module",
    "globals": "reaches the module namespace, which is how `exec` is smuggled",
    "vars": "reaches an object namespace, which is how `exec` is smuggled",
}

#: Attribute paths that reach a subprocess, a socket or the environment, however
#: the module happened to import their root.
_FORBIDDEN_ATTRIBUTES: Final[dict[str, str]] = {
    "subprocess.run": "runs a subprocess",
    "subprocess.Popen": "runs a subprocess",
    "subprocess.call": "runs a subprocess",
    "subprocess.check_output": "runs a subprocess",
    "os.system": "runs a shell",
    "os.popen": "runs a shell",
    "os.execv": "replaces the process",
    "os.spawnv": "spawns a process",
    "os.environ": "reads process environment, which is where secrets live",
    "os.getenv": "reads process environment, which is where secrets live",
    "socket.socket": "opens a socket",
    "socket.create_connection": "opens a socket",
    "importlib.import_module": "dynamically imports a module",
}

#: The nine rules, named. `05` S1.3 asks for **one rejection test per audit
#: rule**, and a test parameterized over this tuple is one that cannot silently
#: stop covering a rule somebody added.
AUDIT_RULES: Final[tuple[str, ...]] = (
    "client_import",
    "dynamic_execution",
    "subprocess",
    "socket",
    "write_open",
    "environment",
    "self_confidence",
    "undeclared_kind",
    "uri_construction",
)

#: Write-mode indicators in an `open()` mode string. `01` F7.2 and `03` §6 give
#: an extractor a read-only tree; the framework owns every writable path.
_WRITE_MODES: Final[frozenset[str]] = frozenset({"w", "a", "x", "+"})


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """One rejection, with the line that caused it.

    `rule` is a member of `AUDIT_RULES`; `detail` names what was found rather
    than quoting the source, because an audit report that echoes client or
    agent-authored code back into a log line is a leak with a good excuse.
    """

    rule: str
    line: int
    detail: str


def _attribute_path(node: ast.AST) -> str | None:
    """`a.b.c` for an attribute chain rooted in a plain name, else `None`."""
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _import_roots(tree: ast.AST) -> Iterator[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom):
            # A relative import (`level > 0`) stays inside the extractor's own
            # package, which is already audited; `module` is None there.
            if node.level:
                continue
            yield (node.module or "").split(".")[0], node.lineno


def _string_constants(tree: ast.AST) -> Iterator[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno


def _keyword_names(tree: ast.AST) -> Iterator[tuple[str, ast.keyword, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg is not None:
                    yield keyword.arg, keyword, node.lineno


def _check_imports(tree: ast.AST) -> Iterator[AuditFinding]:
    for root, line in _import_roots(tree):
        if root.startswith("adopt_extractors_") or root in PERMITTED_IMPORT_ROOTS:
            continue
        detail = (
            f"imports {root!r}, which is not on the extractor import allowlist. "
            "An extractor reads a client tree as text and never imports anything "
            "from it -- `02` §7 obligation 1."
        )
        yield AuditFinding(rule="client_import", line=line, detail=detail)


def _check_calls_and_attributes(tree: ast.AST) -> Iterator[AuditFinding]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_CALLS:
            yield AuditFinding(
                rule="dynamic_execution",
                line=node.lineno,
                detail=f"names {node.id!r}, which {_FORBIDDEN_CALLS[node.id]}",
            )
        if not isinstance(node, ast.Attribute):
            continue
        path = _attribute_path(node)
        if path is None or path not in _FORBIDDEN_ATTRIBUTES:
            continue
        rule = (
            "subprocess"
            if path.startswith("subprocess.") or path in {"os.system", "os.popen"}
            else "socket"
            if path.startswith("socket.")
            else "environment"
            if path in {"os.environ", "os.getenv"}
            else "dynamic_execution"
        )
        yield AuditFinding(
            rule=rule,
            line=node.lineno,
            detail=f"reaches {path!r}, which {_FORBIDDEN_ATTRIBUTES[path]}",
        )


#: Method names that write a file whatever they are called on. Matched on the
#: **attribute name alone**, not on a dotted path: `Path(p).write_text(...)` has
#: a `Call` at the root of its attribute chain, so a dotted-path match returns
#: `None` and the write sails through. That was found by the poisoned-fixture
#: test, which is what the poisoned fixture is for.
_WRITE_METHODS: Final[frozenset[str]] = frozenset(
    {"write_text", "write_bytes", "writelines", "mkdir", "touch", "unlink", "rmtree"}
)


def _check_write_open(tree: ast.AST) -> Iterator[AuditFinding]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func.id if isinstance(node.func, ast.Name) else None
        method = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if method in _WRITE_METHODS:
            yield AuditFinding(
                rule="write_open",
                line=node.lineno,
                detail=f"calls {method}(), which writes; the client tree is opened "
                "read-only (`03` §6)",
            )
            continue
        if target != "open" and method != "open":
            continue
        modes = [argument for argument in node.args[1:2] if isinstance(argument, ast.Constant)]
        modes += [
            keyword.value
            for keyword in node.keywords
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant)
        ]
        for mode in modes:
            if isinstance(mode.value, str) and set(mode.value) & _WRITE_MODES:
                yield AuditFinding(
                    rule="write_open",
                    line=node.lineno,
                    detail=f"opens a file in mode {mode.value!r}; the client tree is "
                    "opened read-only (`03` §6)",
                )


def _check_self_confidence(tree: ast.AST) -> Iterator[AuditFinding]:
    detail = (
        "sets its own confidence. The **framework** assigns confidence from the "
        "declared evidence method (`02` §7 obligation 5); an extractor that could "
        "set it could promote a regex guess to grammar-level certainty and the "
        "degrade ladder would be advisory."
    )
    for name, _keyword, line in _keyword_names(tree):
        if name == "confidence":
            yield AuditFinding(rule="self_confidence", line=line, detail=detail)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "confidence" and _is_store(node):
            yield AuditFinding(rule="self_confidence", line=node.lineno, detail=detail)


def _is_store(node: ast.Attribute) -> bool:
    return isinstance(node.ctx, ast.Store)


def _check_undeclared_kinds(tree: ast.AST, declared: frozenset[str]) -> Iterator[AuditFinding]:
    for name, keyword, line in _keyword_names(tree):
        if name not in {"identity_kind", "target_kind"}:
            continue
        value = keyword.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        if name == "identity_kind" and value.value not in declared:
            yield AuditFinding(
                rule="undeclared_kind",
                line=line,
                detail=f"emits kind {value.value!r}, which its manifest does not declare "
                f"({sorted(declared)})",
            )
        if value.value not in _IDENTITY_KINDS:
            yield AuditFinding(
                rule="undeclared_kind",
                line=line,
                detail=f"names kind {value.value!r}, which is not a member of Build 0's "
                "closed IdentityKind enum. Build 1 never extends it (`01` F2.2)",
            )


def _check_uri_construction(tree: ast.AST) -> Iterator[AuditFinding]:
    """Rule 9 -- B1-CR-26, `04` §6.

    Three shapes, because no one of them is sufficient: a `build_uri` call (the
    framework mints; an extractor never does), a literal carrying the scheme
    label (a hand-assembled URI, whatever built it), and a join over operands
    whose names say they are URI or scope parts (assembly that has not been given
    a scheme yet, and would pass a scheme scan clean).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else (_attribute_path(node.func) or "")
            )
            if called == "build_uri" or called.endswith(".build_uri"):
                yield AuditFinding(
                    rule="uri_construction",
                    line=node.lineno,
                    detail="calls build_uri(). The framework mints every URI from the "
                    "resolved scope; an extractor that minted one could name an "
                    "environment it was never given (`02` §7 obligation 6)",
                )
            if isinstance(node.func, ast.Attribute) and node.func.attr == "join":
                operand = _attribute_path(node.func.value) or ""
                if any(part in operand.lower() for part in ("uri", "scope", "slug", "segment")):
                    yield AuditFinding(
                        rule="uri_construction",
                        line=node.lineno,
                        detail=f"joins over {operand!r}, which is URI or scope assembly",
                    )
    for value, line in _string_constants(tree):
        if URI_SCHEME in value:
            yield AuditFinding(
                rule="uri_construction",
                line=line,
                detail="carries the identity URI scheme label in a string literal. "
                "The label is `adopt_const.URI_SCHEME` and only `build_uri()` "
                "assembles a URI (B1-CR-26)",
            )


def audit_source(source: str, *, declared_kinds: Iterable[str]) -> tuple[AuditFinding, ...]:
    """Audit one extractor module's **text**. Never imports it.

    Args:
        source: The module source. Parsed with `ast.parse`; a module that
            detonates on import is audited without ever being executed, which is
            what `01` F7.2 requires and what the `poisoned-import` fixture proves.
        declared_kinds: The manifest's `kinds`, for rule 8.

    Returns:
        Every finding, ordered by line then rule, so a report over the same
        module is byte-identical on every machine.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` when the source does not parse. A
            module that is not Python cannot be audited, and admitting an
            unauditable extractor would make the guarantee conditional on the
            parser's mood.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise AdoptError(
            ErrorCode.MAP_EXTRACTOR_FAILED,
            message=f"the extractor module does not parse at line {error.lineno}",
            hint="An unauditable module is refused rather than admitted. The static-only "
            "guarantee is not conditional on whether the parser managed to read the file.",
        ) from error

    declared = frozenset(declared_kinds)
    findings = [
        *_check_imports(tree),
        *_check_calls_and_attributes(tree),
        *_check_write_open(tree),
        *_check_self_confidence(tree),
        *_check_undeclared_kinds(tree, declared),
        *_check_uri_construction(tree),
    ]
    return tuple(sorted(findings, key=lambda finding: (finding.line, finding.rule, finding.detail)))


def require_clean(source: str, *, extractor_id: str, declared_kinds: Iterable[str]) -> None:
    """Audit, and refuse the extractor when anything was found.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` naming every rule that fired and the
            line each fired on.
    """
    findings = audit_source(source, declared_kinds=declared_kinds)
    if not findings:
        return
    summary = "; ".join(
        f"line {finding.line}: {finding.rule} -- {finding.detail}" for finding in findings
    )
    raise AdoptError(
        ErrorCode.MAP_EXTRACTOR_FAILED,
        message=f"{extractor_id} failed the static audit: {summary}",
        hint="`01` F7.2 makes the static-only guarantee testable rather than promised. "
        "Fix the module; the audit is not waivable per extractor, because a waiver "
        "is how the guarantee becomes a claim about intent again.",
    )


class ExtractorRegistry:
    """Every extractor this run may use, and nothing else.

    **Manifests are validated at registration, not at first emission.** An
    extractor declaring a kind outside Build 0's closed enum never reaches a
    client tree, so its facts never exist to be rejected later -- which is the
    difference between a coverage arithmetic somebody can check and one that
    depends on when a defect happened to fire.
    """

    __slots__ = ("_by_id", "_enabled_packs")

    def __init__(self, *, enabled_packs: Iterable[str] = DEFAULT_ENABLED_PACKS) -> None:
        self._by_id: dict[str, Extractor] = {}
        self._enabled_packs = frozenset(enabled_packs)

    @property
    def enabled_packs(self) -> frozenset[str]:
        return self._enabled_packs

    def register(self, extractor: Extractor) -> None:
        """Admit one extractor.

        Raises:
            AdoptError: ``MAP_EXTRACTOR_FAILED`` on a duplicate id or a manifest
                naming a kind outside Build 0's closed enum.
        """
        manifest = extractor.manifest()
        if manifest.id in self._by_id:
            raise AdoptError(
                ErrorCode.MAP_EXTRACTOR_FAILED,
                message=f"extractor id {manifest.id!r} is already registered",
                hint="Ids are the attribution key on every `identity_revision` this build "
                "writes (`03` §9). Two extractors sharing one id make a bad pack "
                "unattributable, which is the rollback surface the flag depends on.",
            )
        undeclared = sorted(set(manifest.kinds) - set(_IDENTITY_KINDS))
        if undeclared:
            raise AdoptError(
                ErrorCode.MAP_EXTRACTOR_FAILED,
                message=f"{manifest.id} declares kinds {undeclared}, which are not members "
                "of Build 0's closed IdentityKind enum",
                hint="`01` F2.2: Build 1 never extends the enum. A referent that does not "
                "fit maps to the nearest kind with a namespace that disambiguates; a "
                "genuine gap appearing twice is a Build 0 amendment.",
            )
        self._by_id[manifest.id] = extractor

    def register_all(self, extractors: Iterable[Extractor]) -> None:
        for extractor in extractors:
            self.register(extractor)

    def all(self) -> tuple[Extractor, ...]:
        """Every registered extractor, **sorted by manifest id**.

        Sorted rather than insertion-ordered so the run plan is a property of
        which extractors exist and not of which module imported first -- `02` §7
        obligation 3 is an extractor obligation, and this is the framework
        keeping its own half of it.
        """
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def get(self, extractor_id: str) -> Extractor | None:
        return self._by_id.get(extractor_id)

    def plan(self, *, archetype: str, root: str, tier: str | None = None) -> tuple[Extractor, ...]:
        """The extractors that will run: enabled pack, matching archetype,
        `applies_to` the tree, and no capability above the negotiated tier.

        A capability an extractor declares but the tier does not permit is a
        **skip with a recorded reason** (`01` F13.5), never a silent omission and
        never a downgrade of the extractor's claim.
        """
        selected: list[Extractor] = []
        for extractor in self.all():
            manifest = extractor.manifest()
            if manifest.pack not in self._enabled_packs:
                continue
            if manifest.archetypes and archetype not in manifest.archetypes:
                continue
            if manifest.requires_capability and tier in {None, "T0", "T1"}:
                continue
            if not extractor.applies_to(root):
                continue
            selected.append(extractor)
        return tuple(selected)

    def skipped(
        self, *, archetype: str, root: str, tier: str | None = None
    ) -> tuple[tuple[str, str], ...]:
        """`(extractor id, reason)` for everything `plan` left out.

        Returned rather than logged, because `01` F13.5 and `03` §5.9 both want
        the reason on the first screen: an extractor that did not run and did not
        say so is indistinguishable from one that ran and found nothing.
        """
        reasons: list[tuple[str, str]] = []
        for extractor in self.all():
            manifest = extractor.manifest()
            if manifest.pack not in self._enabled_packs:
                reasons.append((manifest.id, "pack_disabled"))
            elif manifest.archetypes and archetype not in manifest.archetypes:
                reasons.append((manifest.id, "archetype_mismatch"))
            elif manifest.requires_capability and tier in {None, "T0", "T1"}:
                reasons.append((manifest.id, "tier_blocked"))
            elif not extractor.applies_to(root):
                reasons.append((manifest.id, "not_applicable"))
        return tuple(reasons)


def manifests(extractors: Sequence[Extractor]) -> tuple[ExtractorManifest, ...]:
    """The manifests of `extractors`, in order. Used by the run plan and the
    conformance suite so neither calls `manifest()` twice with different results."""
    return tuple(extractor.manifest() for extractor in extractors)
