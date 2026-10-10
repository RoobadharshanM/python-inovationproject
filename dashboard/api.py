"""Compatibility exports for dashboard JSON API views.

The URL configuration keeps the implementation in :mod:`dashboard.views`, but
some clients import the API surface from ``dashboard.api`` directly.
"""

from .views import dashboard_stats_api

stats_api = dashboard_stats_api
dashboard_stats = dashboard_stats_api

__all__ = ["dashboard_stats_api", "dashboard_stats", "stats_api"]
