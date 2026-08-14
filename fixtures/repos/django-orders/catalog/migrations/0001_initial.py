"""Initial migration for catalog."""

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("sku", models.CharField(max_length=64, unique=True)),
                ("title", models.CharField(max_length=128)),
                ("active", models.BooleanField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="ProductVariant",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("product_id", models.IntegerField(null=False)),
                ("option", models.CharField(max_length=64)),
            ],
        ),
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("slug", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=128)),
            ],
        ),
        migrations.CreateModel(
            name="ProductCategory",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("product_id", models.IntegerField(null=False)),
                ("category_id", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Price",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("product_id", models.IntegerField(null=False)),
                ("currency", models.CharField(max_length=3)),
                ("amount_cents", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="PriceHistory",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("price_id", models.IntegerField(null=False)),
                ("changed_at", models.DateTimeField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Inventory",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("sku", models.CharField(max_length=64, db_index=True)),
                ("on_hand", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Supplier",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("name", models.CharField(max_length=128)),
            ],
        ),
        migrations.CreateModel(
            name="SupplierProduct",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("supplier_id", models.IntegerField(null=False)),
                ("product_id", models.IntegerField(null=False)),
            ],
        ),
        migrations.CreateModel(
            name="Media",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("product_id", models.IntegerField(null=False)),
                ("url", models.CharField(max_length=255)),
            ],
        ),
    ]
