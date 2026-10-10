"""HTML URLs for score, outcome, and saved prediction history."""

from django.urls import path

from . import views


urlpatterns = [
    path("score/", views.score_prediction, name="score_prediction"),
    path("score", views.score_prediction, name="score_prediction_no_slash"),
    path("score-prediction/", views.score_prediction, name="score_prediction_alias"),
    path("predict-score/", views.score_prediction, name="predict_score"),
    path("prediction/", views.score_prediction, name="prediction"),
    path("outcome/", views.outcome_prediction, name="outcome_prediction"),
    path("outcome", views.outcome_prediction, name="outcome_prediction_no_slash"),
    path("outcome-prediction/", views.outcome_prediction, name="outcome_prediction_alias"),
    path("predict-outcome/", views.outcome_prediction, name="predict_outcome"),
    path("history/", views.prediction_history, name="prediction_history"),
    path("history", views.prediction_history, name="prediction_history_no_slash"),
    path("history/<int:pk>/delete/", views.delete_prediction, name="delete_prediction"),
    path("delete/<int:pk>/", views.delete_prediction, name="delete_prediction_alias"),
    path("result/<int:pk>/", views.prediction_result, name="prediction_result"),
    # A useful default landing page for users who enter /predictions/.
    path("", views.score_prediction, name="prediction_home"),
]
