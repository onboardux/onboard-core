"""orders views. Class-based, so the served methods are declarable."""


class OrderListView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def post(self, request, *args, **kwargs):
        """Handle POST."""
        return


class OrderDetailView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def put(self, request, *args, **kwargs):
        """Handle PUT."""
        return

    def patch(self, request, *args, **kwargs):
        """Handle PATCH."""
        return

    def delete(self, request, *args, **kwargs):
        """Handle DELETE."""
        return


class OrderItemListView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def post(self, request, *args, **kwargs):
        """Handle POST."""
        return


class OrderCancelView:
    def post(self, request, *args, **kwargs):
        """Handle POST."""
        return


class OrderSearchView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return


class OrderExportView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return
