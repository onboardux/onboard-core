"""billing background tasks."""

from celery import shared_task


@shared_task(queue="billing", max_retries=5)
def issue_invoices():
    """Run the issue invoices job."""
    return


@shared_task(queue="billing", retry_backoff=True)
def retry_failed_payments():
    """Run the retry failed payments job."""
    return


@shared_task(queue="billing")
def close_ledger_period():
    """Run the close ledger period job."""
    return
