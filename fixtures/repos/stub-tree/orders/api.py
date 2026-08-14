"""Order endpoints. Never imported by the tool -- read as text only."""


class OrderDetailView:
    def get(self, request, order_id):
        return {"id": order_id}


def reconcile_payments(window_days):
    return window_days
