"""billing views. Class-based, so the served methods are declarable."""


class InvoiceListView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def post(self, request, *args, **kwargs):
        """Handle POST."""
        return


class InvoiceDetailView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def delete(self, request, *args, **kwargs):
        """Handle DELETE."""
        return


class PaymentListView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def post(self, request, *args, **kwargs):
        """Handle POST."""
        return


class RefundView:
    def post(self, request, *args, **kwargs):
        """Handle POST."""
        return


class DunningView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return
