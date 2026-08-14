"""identity views. Class-based, so the served methods are declarable."""


class CustomerListView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def post(self, request, *args, **kwargs):
        """Handle POST."""
        return


class CustomerDetailView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def patch(self, request, *args, **kwargs):
        """Handle PATCH."""
        return


class AddressListView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def post(self, request, *args, **kwargs):
        """Handle POST."""
        return
