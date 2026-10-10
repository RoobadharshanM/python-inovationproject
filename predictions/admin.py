"""Admin registration for saved prediction records."""

from django.contrib import admin

from .models import Prediction


@admin.register(Prediction)
class PredictionAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "user",
        "prediction_type",
        "batting_team",
        "bowling_team",
        "predicted_score",
        "outcome_winner",
        "model_version",
    ]
    list_filter = ["prediction_type", "created_at", "season", "venue", "batting_team", "bowling_team"]
    search_fields = [
        "user__username",
        "user__email",
        "batting_team__name",
        "bowling_team__name",
        "venue__name",
        "outcome_winner__name",
        "model_version",
    ]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at"]
    fieldsets = (
        (
            "Request",
            {
                "fields": (
                    "user",
                    "prediction_type",
                    "batting_team",
                    "bowling_team",
                    "venue",
                    "toss_winner",
                    "toss_decision",
                    "season",
                )
            },
        ),
        (
            "Result",
            {
                "fields": (
                    "predicted_score",
                    "prediction_low",
                    "prediction_high",
                    "outcome_winner",
                    "outcome_probabilities",
                    "model_version",
                )
            },
        ),
        (
            "Audit",
            {"fields": ("input_values", "created_at")},
        ),
    )
