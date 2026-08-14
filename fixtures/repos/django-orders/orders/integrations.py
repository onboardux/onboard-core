"""Outbound calls this service makes."""

import requests


def charge_invoice(payload):
    """Ask the billing service to charge an invoice."""
    return requests.post("https://billing.example.com/v1/charges", json=payload)


def fetch_stock(sku):
    """Read current stock from the warehouse service."""
    return requests.get("https://warehouse.example.com/v1/stock")


def notify_carrier(payload):
    """Hand a shipment to the carrier."""
    return requests.put("https://carrier.example.com/v2/shipments", json=payload)
