"""Initial migration for billing."""

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    operations = [
        migrations.CreateModel(
            name="Invoice",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("number", models.CharField(max_length=32, unique=True)),
                ("order_id", models.IntegerField(null=False)),
                ("issued_at", models.DateTimeField(null=False)),
                ("total_cents", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="InvoiceLine",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("invoice_id", models.IntegerField(null=False)),
                ("description", models.CharField(max_length=128)),
                ("amount_cents", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Payment",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("invoice_id", models.IntegerField(null=False)),
                ("processor", models.CharField(max_length=32)),
                ("amount_cents", models.IntegerField(null=False)),
                ("captured_at", models.DateTimeField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name="Refund",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("payment_id", models.IntegerField(null=False)),
                ("amount_cents", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="DunningAttempt",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("invoice_id", models.IntegerField(null=False)),
                ("attempt", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="TaxRate",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("region", models.CharField(max_length=16)),
                ("basis_points", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Ledger",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("account", models.CharField(max_length=32)),
                ("balance_cents", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="LedgerEntry",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("ledger_id", models.IntegerField(null=False)),
                ("delta_cents", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Coupon",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("code", models.CharField(max_length=32, unique=True)),
                ("percent_off", models.IntegerField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name="CreditNote",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("invoice_id", models.IntegerField(null=False)),
                ("amount_cents", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="PayoutBatch",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("settled_at", models.DateTimeField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name="Chargeback",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("payment_id", models.IntegerField(null=False)),
                ("opened_at", models.DateTimeField(null=False)),
            ],
        ),
    ]
