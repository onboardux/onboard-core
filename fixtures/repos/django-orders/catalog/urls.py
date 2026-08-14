"""catalog urlconf."""

from catalog import views
from django.urls import path

urlpatterns = [
    path("products/", views.ProductListView.as_view()),
    path("products/<int:product_id>/", views.ProductDetailView.as_view()),
    path("categories/", views.CategoryListView.as_view()),
    path("prices/", views.PriceListView.as_view()),
]
