"""orders background tasks."""

from celery import shared_task


@shared_task(queue="orders", max_retries=3)
def reconcile_payments():
    """Run the reconcile payments job."""
    return


@shared_task(queue="orders")
def expire_reservations():
    """Run the expire reservations job."""
    return


@shared_task(queue="search", time_limit=600)
def rebuild_search_index():
    """Run the rebuild search index job."""
    return
