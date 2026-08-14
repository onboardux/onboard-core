"""A module with a side effect at import scope. THIS MUST NEVER RUN.

The canary lands beside this file, so the assertion needs no environment
variable and no temp-directory bookkeeping: the tree is copied per test, and the
file either exists in the copy afterwards or it does not.
"""

from pathlib import Path

DETONATED = Path(__file__).with_name("DETONATED.txt")
DETONATED.write_text("a client module was imported\n", encoding="utf-8")


def handler(request):
    """A plausible view, so the tree looks like something worth extracting."""
    return {"ok": True}


class OrderService:
    def place(self, order_id: str) -> str:
        return order_id
