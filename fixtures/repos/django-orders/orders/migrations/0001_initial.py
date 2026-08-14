"""Initial migration for orders."""

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    operations = [
        migrations.CreateModel(
            name="Order",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("reference", models.CharField(max_length=32, unique=True)),
                ("status", models.CharField(max_length=16, db_index=True)),
                ("placed_at", models.DateTimeField(null=False)),
                ("total_cents", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("order_id", models.IntegerField(null=False, db_index=True)),
                ("sku", models.CharField(max_length=64)),
                ("quantity", models.IntegerField(null=False)),
                ("unit_price_cents", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="OrderNote",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("order_id", models.IntegerField(null=False)),
                ("body", models.TextField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name="OrderEvent",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("order_id", models.IntegerField(null=False)),
                ("kind", models.CharField(max_length=32)),
                ("occurred_at", models.DateTimeField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Shipment",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("order_id", models.IntegerField(null=False)),
                ("carrier", models.CharField(max_length=32)),
                ("tracking", models.CharField(max_length=64, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="ShipmentLeg",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("shipment_id", models.IntegerField(null=False)),
                ("sequence", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Fulfilment",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("order_id", models.IntegerField(null=False)),
                ("warehouse", models.CharField(max_length=32)),
            ],
        ),
        migrations.CreateModel(
            name="ReturnRequest",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("order_id", models.IntegerField(null=False)),
                ("reason", models.CharField(max_length=64)),
            ],
        ),
        migrations.CreateModel(
            name="ReturnLine",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("return_id", models.IntegerField(null=False)),
                ("quantity", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Reservation",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("sku", models.CharField(max_length=64)),
                ("quantity", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="OrderTag",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("order_id", models.IntegerField(null=False)),
                ("label", models.CharField(max_length=32)),
            ],
        ),
        migrations.CreateModel(
            name="OrderHold",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("order_id", models.IntegerField(null=False)),
                ("released_at", models.DateTimeField(null=True)),
            ],
        ),
    ]
