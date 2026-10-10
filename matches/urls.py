"""URL routes for match discovery and scorecards."""

from django.urls import path

from matches import views

app_name = "matches"

urlpatterns = [
    path("", views.match_list, name="match_list"),
    path("<int:pk>/", views.match_detail, name="match_detail"),
    # Short aliases are convenient for project templates and older clients.
    path("", views.match_list, name="list"),
    path("<int:pk>/", views.match_detail, name="detail"),
]
