"""identity urlconf."""

from django.urls import path
from identity import views

urlpatterns = [
    path("customers/", views.CustomerListView.as_view()),
    path("customers/<int:customer_id>/", views.CustomerDetailView.as_view()),
    path("customers/<int:customer_id>/addresses/", views.AddressListView.as_view()),
]
