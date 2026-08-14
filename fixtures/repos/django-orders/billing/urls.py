"""billing urlconf."""

from billing import views
from django.urls import path

urlpatterns = [
    path("invoices/", views.InvoiceListView.as_view()),
    path("invoices/<int:invoice_id>/", views.InvoiceDetailView.as_view()),
    path("payments/", views.PaymentListView.as_view()),
    path("payments/<int:payment_id>/refund/", views.RefundView.as_view()),
    path("dunning/", views.DunningView.as_view()),
]
