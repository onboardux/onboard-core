"""Initial migration for identity."""

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    operations = [
        migrations.CreateModel(
            name="Customer",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("email", models.CharField(max_length=255, unique=True)),
                ("created_at", models.DateTimeField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Address",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("customer_id", models.IntegerField(null=False)),
                ("line1", models.CharField(max_length=255)),
                ("country", models.CharField(max_length=2)),
            ],
        ),
        migrations.CreateModel(
            name="ContactMethod",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("customer_id", models.IntegerField(null=False)),
                ("channel", models.CharField(max_length=16)),
            ],
        ),
        migrations.CreateModel(
            name="Consent",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("customer_id", models.IntegerField(null=False)),
                ("granted", models.BooleanField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="LoyaltyAccount",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("customer_id", models.IntegerField(null=False)),
                ("points", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Segment",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("key", models.CharField(max_length=32, unique=True)),
            ],
        ),
    ]
