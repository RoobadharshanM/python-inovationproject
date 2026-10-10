"""Dashboard and account URLs.

``config.urls`` mounts this module at both ``/`` and ``/accounts/`` so the
familiar root-level ``/dashboard/``, ``/login/``, and profile routes are kept
available.  No namespace is declared because Django's configured
``LOGIN_REDIRECT_URL = "dashboard"`` resolves the root dashboard directly.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("home/", views.home, name="home-alias"),
    path("about/", views.about, name="about"),
    path("health/", views.health, name="health"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("register/", views.register, name="register"),
    path("signup/", views.register, name="signup"),
    path("login/", views.user_login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("profile/", views.profile, name="profile"),
    # Descriptive aliases for integrations that use view-style names.
    path("dashboard/", views.dashboard, name="dashboard-view"),
    path("login/", views.user_login, name="login-view"),
    path("register/", views.register, name="register-view"),
    path("logout/", views.logout, name="logout-view"),
    path("profile/", views.profile, name="profile-view"),
    path("", views.home, name="home-view"),
    path("", views.home, name="dashboard-home"),
    path("about/", views.about, name="about-view"),
    path("dashboard/", views.dashboard, name="dashboard-home"),
    path("register/", views.register, name="signup-view"),
]
