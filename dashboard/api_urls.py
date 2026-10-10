"""JSON API routes owned by the dashboard application."""

from django.urls import path

from . import views

urlpatterns = [
    path("dashboard-stats/", views.dashboard_stats_api, name="dashboard-stats"),
    path("dashboard-stats/", views.dashboard_stats_api, name="dashboard-stats-api"),
    path("stats/", views.dashboard_stats_api, name="dashboard-stats-alias"),
    path("dashboard-stats/", views.dashboard_stats_api, name="dashboard_stats_api"),
    path("dashboard-stats/", views.dashboard_stats_api, name="dashboard_stats"),
]
