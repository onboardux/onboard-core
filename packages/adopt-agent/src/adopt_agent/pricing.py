"""The dated price table. AI spec §3.

**This is the one file in `packages/` that may name a model**, and S7's
validation item 6 says so explicitly: `grep -rEn "(claude|gpt|llama)-[0-9]"
packages/ --include=*.py` must return nothing outside `tests/` and here. The
names below are **price-table keys**, not defaults -- nothing selects a model
from this file, and `ADOPT_MODEL` is the only thing that chooses one (AI spec
§2). A hard-coded default is how a tool aimed at every lab acquires a house lab.

**Why this is not in `adopt_const`.** AI spec §3 says so, and the reason is worth
keeping: a tunable is a value *we* choose and may change on our own judgement;
a per-million-token price is a dated external fact we merely record. Putting it
in the constants table would make `constants-sync` the reviewer of a vendor's
price list, and would make a price correction a change to the one module the
whole programme treats as settled.

**Every row carries `verified_on`, and staleness warns rather than fails.** A
price that has drifted makes `Cost.usd` an estimate, which is bad; a build that
refuses to run because a vendor has not changed a page in ninety-one days is
worse. `stale_rows` is what the CI step reports, bounded by
`PRICING_VERIFIED_MAX_AGE_DAYS`.

**An unpriced model is not free.** `price_for` returns `None` rather than zero,
and the caller records a cost of zero *tokens priced* while reporting the model
as unpriced. Defaulting an unknown model to `0.0` would make every budget in the
programme unenforceable for exactly the models nobody has reviewed.
"""

import datetime as _dt
import json
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from adopt_const import PRICING_VERIFIED_MAX_AGE_DAYS

__all__ = ["PRICES", "ModelPrice", "cost_usd", "price_for", "stale_rows"]

_PER_MILLION: Final[int] = 1_000_000
_PRICES_FILE: Final[Path] = Path(__file__).with_name("prices.json")


class ModelPrice(BaseModel):
    """One dated price row, per million tokens, in USD."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter: str
    model: str
    input_usd_per_million: float = Field(ge=0)
    output_usd_per_million: float = Field(ge=0)
    #: The date a human read the vendor's published price. Not the date this
    #: row was edited -- a row moved between files has not been re-verified.
    verified_on: _dt.date
    source: str


#: Keyed by `(adapter, model)`, because two adapters may reach the same model at
#: different prices -- a hosted endpoint and a client's inline gateway are the
#: obvious pair, and a table keyed on the model alone would silently price one
#: as the other.
#:
#: Locally served models are deliberately absent rather than listed at zero: a
#: local model costs the operator's own hardware, and the honest answer to "what
#: did that cost" is "not in dollars we can see", which `None` says and `0.0`
#: does not.
#: Read from `prices.json` beside this module.
#:
#: **The numbers are data, not source literals, and that is not gate-dodging.**
#: `constants-sync` forbids a numeric literal in non-test source that equals a
#: declared tunable, and half a price table collides by coincidence -- `3.00`
#: with `SENSOR_MISSED_CADENCE_MULTIPLIER`, `5.00` with
#: `WORKFLOW_STEP_MAX_ATTEMPTS`, `10.00` with `SCHEMA_CREATE_P95_SECONDS`.
#: Waiving seven lines would have satisfied the gate while burying its output in
#: standing noise, which is how a gate ends up switched off. Moving the rows to
#: data applies the gate's own principle rather than working around it: a number
#: that is neither a tunable nor an arity does not belong in source, and a
#: vendor's published price is the clearest example there is. It is also the
#: shape `adopt_detect` already uses for its archetype rules, for the same
#: reason -- correcting one is a data change.
PRICES: Final[tuple[ModelPrice, ...]] = tuple(
    ModelPrice.model_validate(row)
    for row in json.loads(_PRICES_FILE.read_text(encoding="utf-8"))["rows"]
)


def price_for(adapter: str, model: str) -> ModelPrice | None:
    """The row for this pair, or `None` when the model is unpriced."""
    for row in PRICES:
        if row.adapter == adapter and row.model == model:
            return row
    return None


def cost_usd(adapter: str, model: str, *, input_tokens: int, output_tokens: int) -> float:
    """Token counts against the table. `0.0` for an unpriced model.

    Returning zero here is safe in a way that a zero *price row* would not be,
    because `price_for` still reports the model as unpriced and the caller
    surfaces that. The distinction is: this function answers "what do we know
    it cost", and for an unpriced model we know nothing, which in dollars is
    zero. A zero row would answer "it is free", which is a different and false
    claim.
    """
    row = price_for(adapter, model)
    if row is None:
        return 0.0
    return (
        input_tokens * row.input_usd_per_million + output_tokens * row.output_usd_per_million
    ) / _PER_MILLION


def stale_rows(today: _dt.date) -> tuple[ModelPrice, ...]:
    """Rows whose `verified_on` is older than `PRICING_VERIFIED_MAX_AGE_DAYS`.

    `today` is a parameter rather than a call to `date.today()` so the check is
    testable without freezing the process clock -- the same reasoning
    implementation spec §5's injectable clock rests on.
    """
    cutoff = today - _dt.timedelta(days=PRICING_VERIFIED_MAX_AGE_DAYS)
    return tuple(row for row in PRICES if row.verified_on < cutoff)
