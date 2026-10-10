"""JSON API URLs for predictions.

These views intentionally use Django's normal CsrfViewMiddleware protection;
API clients should send the CSRF token in the standard ``X-CSRFToken``
header. The endpoints remain protected by the middleware.
"""

from django.urls import path

from . import views


urlpatterns = [
    path("score/", views.score_prediction_api, name="prediction_score_api"),
    path("score", views.score_prediction_api, name="prediction_score_api_no_slash"),
    path("score-prediction/", views.score_prediction_api, name="prediction_score_api_alias"),
    path("predict-score/", views.score_prediction_api, name="predict_score_api"),
    path("outcome/", views.outcome_prediction_api, name="prediction_outcome_api"),
    path("outcome", views.outcome_prediction_api, name="prediction_outcome_api_no_slash"),
    path("outcome-prediction/", views.outcome_prediction_api, name="prediction_outcome_api_alias"),
    path("predict-outcome/", views.outcome_prediction_api, name="predict_outcome_api"),
    path("history/", views.prediction_history_api, name="prediction_history_api"),
    path("history", views.prediction_history_api, name="prediction_history_api_no_slash"),
    path("history/<int:pk>/", views.prediction_detail_api, name="prediction_detail_api"),
    path("history/<int:pk>/delete/", views.delete_prediction_api, name="prediction_delete_api"),
    path("delete/<int:pk>/", views.delete_prediction_api, name="prediction_delete_api_alias"),
    path("status/", views.model_status_api, name="prediction_model_status_api"),
    path("status", views.model_status_api, name="prediction_model_status_api_no_slash"),
]
