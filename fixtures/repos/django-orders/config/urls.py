"""Root urlconf. Every app is mounted under a prefix here."""

from config import views
from django.urls import include, path

urlpatterns = [
    path("healthz/", views.health_check),
    path("api/v1/orders/", include("orders.urls")),
    path("api/v1/billing/", include("billing.urls")),
    path("api/v1/catalog/", include("catalog.urls")),
    path("api/v1/identity/", include("identity.urls")),
    path("admin/", include("django.contrib.admin.urls")),
]
