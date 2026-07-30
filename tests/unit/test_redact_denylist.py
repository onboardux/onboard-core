"""The field deny-list drops content at any depth, and counts what it dropped.

Defect sentence: *fails when* a deny-listed field survives redaction, or when a
drop happens without being counted. *Matters because* the count is what makes an
attempted leak visible -- a silent drop is indistinguishable from a caller who
never passed the field, so without the count nobody ever learns that a code path
is trying to log a body. *No other instrument catches it because* the type
checker sees `dict[str, Any]` and has no opinion about key names.
"""

import pytest

from adopt_obs import DENIED_FIELDS, redact


@pytest.mark.unit
@pytest.mark.parametrize("denied", sorted(DENIED_FIELDS))
def test_every_denied_field_is_dropped_and_counted(denied: str) -> None:
    result = redact({denied: "PLANTED", "path": "/safe"})

    assert denied not in result.value
    assert result.value == {"path": "/safe"}
    assert result.dropped == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected_dropped", "why"),
    [
        ({"path": "/x", "count": 3}, 0, "no denied field present"),
        ({"BODY": "PLANTED"}, 1, "matching is case-insensitive"),
        ({"Content": "PLANTED"}, 1, "mixed case is still denied"),
        ({"outer": {"prompt": "PLANTED"}}, 1, "nested one level"),
        ({"a": {"b": {"c": {"output": "PLANTED"}}}}, 1, "nested four levels"),
        ({"rows": [{"text": "PLANTED"}, {"ok": 1}]}, 1, "inside a list of dicts"),
        ({"body": "P1", "answer": "P2", "question": "P3"}, 3, "several denied siblings"),
        ({"body_length": 42}, 0, "a prefix is not a match -- only whole field names"),
        ({"summary_text": "safe"}, 0, "a suffix is not a match either"),
        ({"source": "PLANTED", "source_type": "commit"}, 1, "exact name only"),
    ],
)
def test_redaction_table(payload: dict[str, object], expected_dropped: int, why: str) -> None:
    result = redact(payload)

    assert result.dropped == expected_dropped, why
    flattened = repr(result.value)
    assert "PLANTED" not in flattened, why


@pytest.mark.unit
def test_deeply_nested_structures_are_truncated_rather_than_walked_forever() -> None:
    """A hostile input must cap the work done, and must cap it by *replacing*.

    Returning the value unwalked would let content past the deny-list simply by
    burying it deeply enough.
    """
    payload: dict[str, object] = {"content": "PLANTED"}
    for _ in range(200):
        payload = {"nest": payload}

    result = redact(payload)

    assert "PLANTED" not in repr(result.value)
