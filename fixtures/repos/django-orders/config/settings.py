"""Django settings. Fixture data; not a runnable configuration."""

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "orders",
        "CONN_MAX_AGE": 60,
    }
}

DEBUG = False
ALLOWED_HOSTS = ["orders.example.com"]
TIME_ZONE = "UTC"
USE_TZ = True
LANGUAGE_CODE = "en-gb"
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
STATIC_URL = "/static/"
MEDIA_URL = "/media/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_AGE = 1209600
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_SSL_REDIRECT = True
X_FRAME_OPTIONS = "DENY"
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.example.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = "orders@example.com"
CELERY_BROKER_URL = "redis://cache:6379/0"
CELERY_RESULT_BACKEND = "redis://cache:6379/1"
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_TIME_LIMIT = 1800
CELERY_WORKER_CONCURRENCY = 8
CACHE_TTL_SECONDS = 300
ORDER_EXPIRY_MINUTES = 45
ORDER_MAX_ITEMS = 200
INVOICE_DUE_DAYS = 30
DUNNING_MAX_ATTEMPTS = 4
REFUND_WINDOW_DAYS = 90
SEARCH_INDEX_BATCH = 500
PRICE_CACHE_SECONDS = 600
FEATURE_NEW_CHECKOUT = False
FEATURE_SPLIT_SHIPMENTS = True
BILLING_API_BASE = "https://billing.example.com"
WAREHOUSE_API_BASE = "https://warehouse.example.com"
