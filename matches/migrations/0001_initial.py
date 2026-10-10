# Generated manually for the initial IPL domain schema.

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Team",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=120, unique=True)),
                ("short_name", models.CharField(blank=True, max_length=20)),
                ("primary_color", models.CharField(default="#6C63FF", max_length=7)),
                (
                    "logo",
                    models.ImageField(blank=True, null=True, upload_to="team-logos/"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Venue",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=180, unique=True)),
                ("city", models.CharField(blank=True, db_index=True, max_length=100)),
                ("short_name", models.CharField(blank=True, max_length=120)),
            ],
            options={"ordering": ["name"]},
        ),
    ]
