"""Celery application and beat schedule."""

from celery.schedules import crontab

beat_schedule = {
    "job-0": {
        "task": "orders.tasks.reconcile_payments",
        "schedule": crontab(hour=3, minute=0),
    },
    "job-1": {
        "task": "billing.tasks.issue_invoices",
        "schedule": crontab(hour=2, minute=30),
    },
    "job-2": {
        "task": "catalog.tasks.refresh_price_cache",
        "schedule": crontab(minute=15),
    },
    "job-3": {
        "task": "analytics.tasks.rollup_daily",
        "schedule": crontab(hour=4, minute=0),
    },
}
