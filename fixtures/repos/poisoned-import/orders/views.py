"""Plausible client code that also runs at import -- the ordinary case."""

from pathlib import Path

# A metrics client registered at import. Not an attack: normal, and exactly why
# the tool reads bytes instead of importing.
Path(__file__).with_name("REGISTERED.txt").write_text("metrics registered\n", encoding="utf-8")


def list_orders(request):
    return []


def get_order(request, order_id):
    return {"id": order_id}


class OrderDetailView:
    def get(self, request, order_id):
        return {"id": order_id}
