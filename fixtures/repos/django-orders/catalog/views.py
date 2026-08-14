"""catalog views. Class-based, so the served methods are declarable."""


class ProductListView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def post(self, request, *args, **kwargs):
        """Handle POST."""
        return


class ProductDetailView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return

    def put(self, request, *args, **kwargs):
        """Handle PUT."""
        return

    def delete(self, request, *args, **kwargs):
        """Handle DELETE."""
        return


class CategoryListView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return


class PriceListView:
    def get(self, request, *args, **kwargs):
        """Handle GET."""
        return
