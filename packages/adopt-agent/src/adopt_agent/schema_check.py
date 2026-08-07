"""A bounded JSON Schema validator for `AgentRequest.output_schema`.

**Why not a JSON Schema library.** One would be a new `in-binary` dependency, and
`03` §7.3 makes that a licence decision rather than a convenience. The schemas
this seam validates are the ones `04` §5.1 declares -- strict, closed, and using
a small keyword set -- so the whole surface is implementable in one file.

**Unsupported keywords are refused, never ignored.** That is the load-bearing
choice. A validator that skipped a keyword it did not understand would report
"valid" about a constraint it never checked, and the failure would be silent and
**in the permissive direction** -- the same shape CR-39 called out for the
envelope deny-list. Refusing means a schema this file cannot fully enforce is a
loud error at the seam rather than a quiet pass at the egress boundary.

**It validates; it never coerces.** `04` §5.1's schema is the egress allowlist
for model output, and a validator that repaired its input would be deciding what
the model meant.
"""

from typing import Any, Final

__all__ = ["SchemaViolation", "UnsupportedSchema", "validate_against_schema"]

#: Everything this file enforces. A schema using anything else is refused.
_SUPPORTED: Final[frozenset[str]] = frozenset(
    {
        "type",
        "enum",
        "const",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "description",
        "title",
    }
)

_TYPES: Final[dict[str, type | tuple[type, ...]]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


class SchemaViolation(Exception):
    """The value does not satisfy the schema. Carries the JSON path."""


class UnsupportedSchema(Exception):
    """The schema uses a keyword this validator does not enforce.

    A programming error in the caller, not a model failure -- which is why it is
    a distinct exception. Reporting an unsupported schema as a model schema
    failure would burn the seam's single retry on a request that can never pass.
    """


def _check_supported(schema: dict[str, Any], path: str) -> None:
    unsupported = sorted(set(schema) - _SUPPORTED)
    if unsupported:
        raise UnsupportedSchema(
            f"{path or '$'}: unsupported keyword(s) {', '.join(unsupported)}. "
            "This validator refuses what it cannot enforce; a skipped keyword "
            "would report valid about a constraint nobody checked."
        )


def _fail(path: str, message: str) -> SchemaViolation:
    return SchemaViolation(f"{path or '$'}: {message}")


def _check_type(value: object, declared: object, path: str) -> None:
    if not isinstance(declared, str):
        raise UnsupportedSchema(f"{path or '$'}: `type` must be a single string")
    expected = _TYPES.get(declared)
    if expected is None:
        raise UnsupportedSchema(f"{path or '$'}: unknown type {declared!r}")
    # `bool` is a subclass of `int` in Python and is not an integer in JSON.
    # Without this, `True` satisfies `{"type": "integer"}` and a boolean reaches
    # a consumer expecting a count.
    if declared in {"integer", "number"} and isinstance(value, bool):
        raise _fail(path, f"expected {declared}, got boolean")
    if not isinstance(value, expected):
        raise _fail(path, f"expected {declared}, got {type(value).__name__}")


def validate_against_schema(value: object, schema: dict[str, Any], path: str = "") -> None:
    """Raise `SchemaViolation` if `value` does not satisfy `schema`.

    Returns `None` on success. Raising rather than returning a bool is
    deliberate: the seam records the *reason* in the trace's validation-retry
    step, and a bool would leave it recording that something was wrong.
    """
    _check_supported(schema, path)

    if "const" in schema and value != schema["const"]:
        raise _fail(path, f"expected the constant {schema['const']!r}")

    if "enum" in schema and value not in schema["enum"]:
        raise _fail(path, f"{value!r} is not one of {schema['enum']!r}")

    if "type" in schema:
        _check_type(value, schema["type"], path)

    if isinstance(value, str):
        _check_string(value, schema, path)
    if isinstance(value, int | float) and not isinstance(value, bool):
        _check_number(value, schema, path)
    if isinstance(value, list):
        _check_array(value, schema, path)
    if isinstance(value, dict):
        _check_object(value, schema, path)


def _check_string(value: str, schema: dict[str, Any], path: str) -> None:
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    if isinstance(minimum, int) and len(value) < minimum:
        raise _fail(path, f"is {len(value)} characters, under minLength {minimum}")
    if isinstance(maximum, int) and len(value) > maximum:
        raise _fail(path, f"is {len(value)} characters, over maxLength {maximum}")


def _check_number(value: float, schema: dict[str, Any], path: str) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, int | float) and value < minimum:
        raise _fail(path, f"{value} is below minimum {minimum}")
    if isinstance(maximum, int | float) and value > maximum:
        raise _fail(path, f"{value} is above maximum {maximum}")


def _check_array(value: list[Any], schema: dict[str, Any], path: str) -> None:
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if isinstance(minimum, int) and len(value) < minimum:
        raise _fail(path, f"has {len(value)} items, under minItems {minimum}")
    if isinstance(maximum, int) and len(value) > maximum:
        raise _fail(path, f"has {len(value)} items, over maxItems {maximum}")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            validate_against_schema(item, items, f"{path}[{index}]")


def _check_object(value: dict[str, Any], schema: dict[str, Any], path: str) -> None:
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}

    required = schema.get("required")
    if isinstance(required, list):
        for name in required:
            if name not in value:
                raise _fail(path, f"is missing required property {name!r}")

    # `additionalProperties: false` is the closed-schema rule `04` §5.1 relies
    # on and `03` §1.1 calls the egress allowlist. Defaulting to *permissive*
    # when the key is absent matches JSON Schema; the schemas we ship set it.
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            raise _fail(path, f"has undeclared propert(ies) {', '.join(extra)}")

    for name, sub in properties.items():
        if name in value and isinstance(sub, dict):
            validate_against_schema(value[name], sub, f"{path}.{name}")
