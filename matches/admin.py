"""Admin configuration for teams, venues, matches and scorecards."""

from django.contrib import admin
from django.utils.html import format_html

from matches.models import Match, MatchInnings, Team, Venue


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "short_name",
        "colour_swatch",
        "logo_column",
        "current_player_count",
        "match_count",
    )
    search_fields = ("name", "short_name")
    ordering = ("name",)
    list_per_page = 50
    fieldsets = (
        (
            "Identity",
            {
                "fields": ("name", "short_name"),
                "description": "Short name is used as the crest monogram.",
            },
        ),
        (
            "Branding",
            {
                "fields": ("logo", "primary_color"),
                "description": (
                    "Upload official artwork here to replace the generated "
                    "crest. Leave the logo blank to keep the generated crest, "
                    "which is drawn from the colours below."
                ),
            },
        ),
    )

    @admin.display(description="Colour", ordering="primary_color")
    def colour_swatch(self, obj):
        return format_html(
            '<span style="display:inline-block;width:44px;height:18px;'
            'border-radius:4px;border:1px solid rgba(128,128,128,.5);'
            'background:{}" title="{}"></span>',
            obj.primary_color,
            obj.primary_color,
        )

    @admin.display(description="Logo")
    def logo_column(self, obj):
        if obj.logo:
            return format_html(
                '<img src="{}" alt="" style="width:32px;height:32px;'
                'object-fit:contain;">',
                obj.logo.url,
            )
        return format_html(
            '<span style="color:#888">generated ({} )</span>', obj.monogram
        )

    @admin.display(description="Current players")
    def current_player_count(self, obj):
        return obj.current_players.count()

    @admin.display(description="Matches")
    def match_count(self, obj):
        return obj.matches_as_team1.count() + obj.matches_as_team2.count()


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "match_count")
    search_fields = ("name", "city", "short_name")
    list_filter = ("city",)
    ordering = ("name",)
    list_per_page = 50

    @admin.display(description="Matches")
    def match_count(self, obj):
        return obj.matches.count()


class MatchInningsInline(admin.TabularInline):
    model = MatchInnings
    extra = 0
    fields = ("innings", "team", "runs", "wickets", "balls", "extras", "overs_display")
    readonly_fields = ("overs_display",)
    ordering = ("innings",)


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "date",
        "season",
        "team1",
        "team2",
        "winner",
        "result_summary",
        "match_type",
    )
    list_filter = ("season", "match_type", "result_type", "dl_applied", "venue__city")
    search_fields = (
        "external_id",
        "team1__name",
        "team2__name",
        "winner__name",
        "venue__name",
        "city",
        "player_of_match__name",
    )
    autocomplete_fields = ("player_of_match",)
    date_hierarchy = "date"
    ordering = ("-date", "-external_id")
    list_select_related = ("venue", "team1", "team2", "winner", "player_of_match")
    list_per_page = 50
    readonly_fields = ("created_at", "updated_at")
    inlines = (MatchInningsInline,)
    fieldsets = (
        ("Identity", {"fields": ("external_id", "season", "date", "match_type")}),
        ("Teams", {"fields": ("team1", "team2", "toss_winner", "toss_decision")}),
        ("Venue", {"fields": ("venue", "city")}),
        ("Result", {"fields": ("winner", "result_type", "result", "dismissal_method")}),
        ("Scores", {"fields": ("target_runs", "target_overs", "win_by_runs", "win_by_wickets", "dl_applied")}),
        ("People", {"fields": ("player_of_match", "umpires")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
