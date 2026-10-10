"""Root URL configuration."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("dashboard.urls")),
    path("", include("dashboard.urls")),
    path("matches/", include("matches.urls")),
    path("players/", include("players.urls")),
    path("analytics/", include("analytics.urls")),
    path("predictions/", include("predictions.urls")),
    path("api/", include("dashboard.api_urls")),
    path("api/", include("analytics.api_urls")),
    path("api/", include("predictions.api_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = "dashboard.views.error_404"
handler500 = "dashboard.views.error_500"
