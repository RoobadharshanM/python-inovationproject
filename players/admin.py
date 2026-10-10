"""Admin configuration for players and their aggregate statistics."""

from django.contrib import admin
from django.db.models import Sum
from django.utils.html import format_html

from players.models import Player, PlayerPerformance, PlayerSeasonPerformance, PlayerTeam


class PlayerTeamInline(admin.TabularInline):
    model = PlayerTeam
    extra = 0
    fields = ("team", "first_season", "last_season", "appearances")
    autocomplete_fields = ("team",)
    ordering = ("-last_season", "team__name")


class PlayerPerformanceInline(admin.TabularInline):
    model = PlayerPerformance
    extra = 0
    fields = (
        "match",
        "season",
        "runs",
        "balls",
        "fours",
        "sixes",
        "wickets",
        "bowling_balls",
        "runs_conceded",
        "catches",
        "highest_score",
    )
    autocomplete_fields = ("match",)
    ordering = ("-season", "match")


class PlayerSeasonPerformanceInline(admin.TabularInline):
    model = PlayerSeasonPerformance
    extra = 0
    fields = (
        "team",
        "season",
        "appearances",
        "batting_innings",
        "runs",
        "balls",
        "wickets",
        "bowling_balls",
        "runs_conceded",
        "catches",
    )
    autocomplete_fields = ("team",)
    ordering = ("-season", "team__name")


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "initials_column",
        "portrait_column",
        "current_team",
        "role",
        "nationality",
        "total_season_runs",
        "total_season_wickets",
        "player_of_match_count",
    )
    list_filter = ("role", "current_team", "nationality")
    search_fields = ("name", "current_team__name", "nationality")
    autocomplete_fields = ("current_team",)
    list_select_related = ("current_team",)
    ordering = ("name",)
    list_per_page = 50
    readonly_fields = ("created_at",)
    inlines = (PlayerTeamInline, PlayerSeasonPerformanceInline, PlayerPerformanceInline)
    fieldsets = (
        ("Profile", {"fields": ("name", "current_team", "role", "nationality")}),
        (
            "Portrait",
            {
                "fields": ("image", "photo_url"),
                "description": (
                    "Optional. Leave both blank to use the generated initials "
                    "avatar, which is tinted with the current team's colours."
                ),
            },
        ),
        ("Audit", {"fields": ("created_at",), "classes": ("collapse",)}),
    )

    @admin.display(description="Initials", ordering="name")
    def initials_column(self, obj):
        return obj.initials

    @admin.display(description="Portrait")
    def portrait_column(self, obj):
        if obj.has_portrait:
            return format_html(
                '<img src="{}" alt="" style="width:32px;height:32px;'
                'border-radius:50%;object-fit:cover;">',
                obj.portrait,
            )
        return format_html(
            '<span style="display:inline-flex;align-items:center;'
            'justify-content:center;width:32px;height:32px;border-radius:50%;'
            'font-size:11px;font-weight:700;color:{};background:{}">{}</span>',
            obj.avatar_text_color,
            obj.avatar_color,
            obj.initials,
        )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            season_run_total=Sum("season_stats__runs"),
            season_wicket_total=Sum("season_stats__wickets"),
        )

    @admin.display(description="Runs", ordering="season_run_total")
    def total_season_runs(self, obj):
        return obj.season_run_total or 0

    @admin.display(description="Wickets", ordering="season_wicket_total")
    def total_season_wickets(self, obj):
        return obj.season_wicket_total or 0

    @admin.display(description="Player of match")
    def player_of_match_count(self, obj):
        return obj.player_of_match_awards.count()


@admin.register(PlayerTeam)
class PlayerTeamAdmin(admin.ModelAdmin):
    list_display = ("player", "team", "first_season", "last_season", "appearances")
    list_filter = ("team", "last_season")
    search_fields = ("player__name", "team__name")
    autocomplete_fields = ("player", "team")
    list_select_related = ("player", "team")
    ordering = ("-last_season", "team__name")


@admin.register(PlayerPerformance)
class PlayerPerformanceAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "match",
        "season",
        "runs",
        "balls",
        "wickets",
        "bowling_balls",
        "runs_conceded",
        "catches",
    )
    list_filter = ("season", "match__season")
    search_fields = ("player__name", "match__external_id", "match__venue__name")
    autocomplete_fields = ("player", "match")
    list_select_related = ("player", "match")
    date_hierarchy = "match__date"
    ordering = ("-match__date", "player__name")
    list_per_page = 50


@admin.register(PlayerSeasonPerformance)
class PlayerSeasonPerformanceAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "team",
        "season",
        "appearances",
        "runs",
        "average",
        "wickets",
        "runs_conceded",
        "economy",
    )
    list_filter = ("season", "team", "player__role")
    search_fields = ("player__name", "team__name")
    autocomplete_fields = ("player", "team")
    list_select_related = ("player", "team")
    ordering = ("-season", "player__name")
    list_per_page = 50
