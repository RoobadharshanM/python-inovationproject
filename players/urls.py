"""URL routes for player profiles and rankings."""

from django.urls import path

from players import views

app_name = "players"

urlpatterns = [
    path("", views.player_list, name="player_list"),
    path("rankings/", views.player_rankings, name="player_rankings"),
    path("<int:pk>/", views.player_detail, name="player_detail"),
    # Short aliases keep direct integrations readable without changing the
    # canonical names used by the application templates.
    path("", views.player_list, name="list"),
    path("rankings/", views.player_rankings, name="rankings"),
    path("<int:pk>/", views.player_detail, name="detail"),
]
