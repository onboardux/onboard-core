"""orders urlconf."""

from django.urls import path
from orders import views

urlpatterns = [
    path("", views.OrderListView.as_view()),
    path("<int:order_id>/", views.OrderDetailView.as_view()),
    path("<int:order_id>/items/", views.OrderItemListView.as_view()),
    path("<int:order_id>/cancel/", views.OrderCancelView.as_view()),
    path("search/", views.OrderSearchView.as_view()),
    path("export/", views.OrderExportView.as_view()),
]
