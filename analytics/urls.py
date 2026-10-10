"""Advanced analytics HTML routes."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.analytics_dashboard, name="analytics-dashboard"),
    path("dashboard/", views.analytics_dashboard, name="analytics-dashboard-alias"),
    path("overview/", views.analytics_dashboard, name="analytics-overview"),
    path(
        "team-comparison/",
        views.team_comparison,
        name="analytics-team-comparison",
    ),
    path("teams/compare/", views.team_comparison, name="analytics-team-comparison-alt"),
    path(
        "player-comparison/",
        views.player_comparison,
        name="analytics-player-comparison",
    ),
    path("players/compare/", views.player_comparison, name="analytics-player-comparison-alt"),
    path("venues/", views.venue_analysis, name="analytics-venue-analysis"),
    path("venue-analysis/", views.venue_analysis, name="analytics-venue-analysis-alt"),
    path("players/trend/", views.player_trend, name="analytics-player-trend"),
    path("player-trend/", views.player_trend, name="analytics-player-trend-alt"),
    path("head-to-head/", views.head_to_head, name="analytics-head-to-head"),
    path("head_to_head/", views.head_to_head, name="analytics-head-to-head-alt"),
    # Short integration aliases; canonical template-facing names remain above.
    path("analytics/", views.analytics_dashboard, name="analytics"),
    path("compare/teams/", views.team_comparison, name="analytics-team-compare"),
    path("compare/players/", views.player_comparison, name="analytics-player-compare"),
    path("venue/", views.venue_analysis, name="analytics-venue"),
    path("player-trend/", views.player_trend, name="analytics-trend"),
    path("head-to-head/", views.head_to_head, name="analytics-headtohead"),
    path("", views.analytics_dashboard, name="analytics_dashboard"),
    path("team-comparison/", views.team_comparison, name="analytics_team_comparison"),
    path("player-comparison/", views.player_comparison, name="analytics_player_comparison"),
    path("venues/", views.venue_analysis, name="analytics_venue_analysis"),
    path("players/trend/", views.player_trend, name="analytics_player_trend"),
    path("head-to-head/", views.head_to_head, name="analytics_head_to_head"),
    path("players/trends/", views.player_trend, name="analytics-player-trends"),
    path("venues/analysis/", views.venue_analysis, name="analytics-venues-analysis"),
]
