"""catalog background tasks."""

from celery import shared_task


@shared_task(queue="catalog")
def refresh_price_cache():
    """Run the refresh price cache job."""
    return


@shared_task(queue="catalog", time_limit=900)
def sync_supplier_feed():
    """Run the sync supplier feed job."""
    return
