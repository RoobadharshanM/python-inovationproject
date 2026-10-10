"""Forms used by match views and the Django admin."""

from django import forms

from matches.models import Match, Team, Venue


class MatchFilterForm(forms.Form):
    """GET-friendly match filters with database-backed choices."""

    q = forms.CharField(
        required=False,
        max_length=120,
        label="Search",
        widget=forms.TextInput(
            attrs={"placeholder": "Team, venue, city or match ID", "autocomplete": "off"}
        ),
    )
    season = forms.ChoiceField(required=False, label="Season")
    team = forms.ModelChoiceField(queryset=Team.objects.none(), required=False)
    venue = forms.ModelChoiceField(queryset=Venue.objects.none(), required=False)
    winner = forms.ModelChoiceField(queryset=Team.objects.none(), required=False)

    def __init__(self, *args, seasons=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["season"].choices = [("", "All seasons")] + [
            (str(season), str(season)) for season in seasons
        ]
        self.fields["team"].queryset = Team.objects.order_by("name")
        self.fields["winner"].queryset = Team.objects.order_by("name")
        self.fields["venue"].queryset = Venue.objects.order_by("name")

    def clean_season(self):
        value = self.cleaned_data.get("season")
        if value in {"", None}:
            return None
        try:
            season = int(value)
        except (TypeError, ValueError):
            raise forms.ValidationError("Select a valid season.") from None
        if season <= 0:
            raise forms.ValidationError("Season must be positive.")
        return season


class MatchForm(forms.ModelForm):
    """Small model form retained for create/update administration workflows."""

    class Meta:
        model = Match
        fields = (
            "external_id",
            "season",
            "date",
            "city",
            "venue",
            "team1",
            "team2",
            "toss_winner",
            "toss_decision",
            "winner",
            "result_type",
            "result",
            "dismissal_method",
            "player_of_match",
            "match_type",
            "target_runs",
            "target_overs",
            "win_by_runs",
            "win_by_wickets",
            "dl_applied",
            "umpires",
        )
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["venue"].queryset = Venue.objects.order_by("name")
        self.fields["team1"].queryset = Team.objects.order_by("name")
        self.fields["team2"].queryset = Team.objects.order_by("name")
        self.fields["toss_winner"].queryset = Team.objects.order_by("name")
        self.fields["winner"].queryset = Team.objects.order_by("name")
        self.fields["player_of_match"].queryset = (
            self.fields["player_of_match"].queryset.order_by("name")
        )

    def clean(self):
        cleaned = super().clean()
        team1 = cleaned.get("team1")
        team2 = cleaned.get("team2")
        if team1 and team2 and team1 == team2:
            self.add_error("team2", "The two teams must be different.")
        playing_teams = {team1, team2} - {None}
        for field in ("toss_winner", "winner"):
            team = cleaned.get(field)
            if team and playing_teams and team not in playing_teams:
                self.add_error(field, "This team did not play in the match.")
        return cleaned


# Backwards-compatible descriptive aliases.
MatchSearchForm = MatchFilterForm
