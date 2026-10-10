"""Validated, login-facing forms for score and match-outcome predictions."""

from __future__ import annotations

from django import forms

from matches.models import Match, Team, Venue


_TOSS_CHOICES = [("", "Unknown / not supplied")] + list(Match.TossDecision.choices)


class _NamedModelChoiceField(forms.ModelChoiceField):
    """Accept a primary key from HTML or a stable model name from JSON."""

    def to_python(self, value):
        if value not in (None, "") and not hasattr(value, "_meta"):
            text = str(value)
            if not text.isdigit():
                value = self.queryset.filter(name__iexact=text).first()
        return super().to_python(value)


class _PredictionFormBase(forms.Form):
    """Shared team/venue/toss validation for both prediction tasks."""

    batting_team = _NamedModelChoiceField(
        queryset=Team.objects.order_by("name"),
        label="Batting team",
        help_text="The team whose score or match participation is being predicted.",
    )
    bowling_team = _NamedModelChoiceField(
        queryset=Team.objects.order_by("name"),
        label="Opponent",
        help_text="The opposing team.",
    )
    venue = _NamedModelChoiceField(
        queryset=Venue.objects.order_by("name"),
        label="Venue",
    )
    toss_winner = _NamedModelChoiceField(
        queryset=Team.objects.order_by("name"),
        required=False,
        label="Toss winner",
    )
    toss_decision = forms.ChoiceField(
        choices=_TOSS_CHOICES,
        required=False,
        label="Toss decision",
    )
    season = forms.IntegerField(
        required=True,
        min_value=2000,
        max_value=2100,
        label="Season",
    )

    def __init__(self, data=None, *args, **kwargs):
        if data is not None:
            data = data.copy()
            aliases = {
                "team1": "batting_team",
                "team_a": "batting_team",
                "batting": "batting_team",
                "team2": "bowling_team",
                "team_b": "bowling_team",
                "bowling": "bowling_team",
                "ground": "venue",
                "stadium": "venue",
                "toss": "toss_winner",
            }
            for source, target in aliases.items():
                if target not in data and source in data:
                    data[target] = data[source]
            if data.get("toss_decision"):
                data["toss_decision"] = str(data["toss_decision"]).lower()
        super().__init__(data, *args, **kwargs)

    def clean_toss_decision(self):
        value = self.data.get("toss_decision") if self.data is not None else None
        return str(value).lower() if value else value

    def clean(self):
        cleaned = super().clean()
        batting = cleaned.get("batting_team")
        bowling = cleaned.get("bowling_team")
        toss_winner = cleaned.get("toss_winner")
        if batting and bowling and batting.pk == bowling.pk:
            self.add_error("bowling_team", "Choose a different opponent team.")
        if toss_winner and batting and bowling and toss_winner.pk not in {batting.pk, bowling.pk}:
            self.add_error("toss_winner", "The toss winner must be one of the two teams.")
        if not cleaned.get("toss_decision"):
            cleaned["toss_decision"] = ""
        return cleaned


class ScorePredictionForm(_PredictionFormBase):
    """Inputs for a first-innings or chase score estimate.

    The optional live values are persisted for auditability.  The default
    artifact produced from aggregate ``MatchInnings`` data is a
    start-of-innings forecast; a model trained from actual delivery snapshots
    may additionally consume these fields when its metadata lists them.
    """

    current_score = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=1000,
        label="Current score",
    )
    overs_completed = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=20,
        decimal_places=2,
        max_digits=5,
        label="Overs completed",
    )
    wickets = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=10,
        label="Wickets",
    )
    current_run_rate = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        decimal_places=2,
        max_digits=7,
        label="Current run rate",
    )
    last_5_overs_runs = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=200,
        label="Runs in the last 5 overs",
    )
    recent_wickets = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=10,
        label="Wickets in the last 5 overs",
    )
    target_score = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=1000,
        label="Target score (if chasing)",
    )

    def clean(self):
        cleaned = super().clean()
        overs = cleaned.get("overs_completed")
        score = cleaned.get("current_score")
        target = cleaned.get("target_score")
        if overs is not None and score is not None and overs == 0 and score != 0:
            self.add_error("overs_completed", "At 0 completed overs, the current score must be 0.")
        if overs is not None and score is not None and overs > 0:
            # A run rate inferred from the entered state is useful when an API
            # client omits it, while still allowing rain-affected 0-over data.
            if cleaned.get("current_run_rate") in (None, ""):
                inferred_rate = float(score) * 6 / float(overs)
                if inferred_rate > 100:
                    self.add_error(
                        "overs_completed",
                        "The inferred run rate is unrealistically high; check the score and overs.",
                    )
                else:
                    cleaned["current_run_rate"] = inferred_rate
            elif float(cleaned["current_run_rate"]) > 100:
                self.add_error("current_run_rate", "The run rate must be 100 or fewer.")
            if float(score) > 1000:
                self.add_error("current_score", "The score must be 1000 or fewer.")
        recent_wickets = cleaned.get("recent_wickets")
        if (
            recent_wickets is not None
            and cleaned.get("wickets") is not None
            and recent_wickets > cleaned["wickets"]
        ):
            self.add_error("recent_wickets", "Recent wickets cannot exceed total wickets fallen.")
        if overs == 0 and cleaned.get("last_5_overs_runs") not in (None, 0):
            self.add_error("last_5_overs_runs", "Enter 0 when no overs have been completed.")
        if target is not None and score is not None and score >= target and target > 0:
            # A chase can equal the target only at completion; rejecting an
            # impossible live state catches the common target/label swap.
            self.add_error("target_score", "The current score must be below the target during a chase.")
        return cleaned


class OutcomePredictionForm(_PredictionFormBase):
    """Inputs for a pre-match winner classification."""

    pass


# Short aliases are useful to callers and preserve the conventional names used
# by older templates/tests.
PredictionForm = ScorePredictionForm
ScoreForm = ScorePredictionForm
OutcomeForm = OutcomePredictionForm
MatchOutcomeForm = OutcomePredictionForm
