"""Forms for player discovery and ranking filters."""

from django import forms

from matches.models import Team
from players.models import Player


class PlayerFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=140,
        label="Search",
        widget=forms.TextInput(
            attrs={"placeholder": "Search player or team", "autocomplete": "off"}
        ),
    )
    team = forms.ModelChoiceField(queryset=Team.objects.none(), required=False)
    role = forms.ChoiceField(
        required=False,
        choices=[("", "All roles")] + list(Player.Role.choices),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = Team.objects.order_by("name")


class PlayerRankingFilterForm(PlayerFilterForm):
    METRIC_CHOICES = (
        ("runs", "Most runs"),
        ("wickets", "Most wickets"),
        ("batting_average", "Best batting average"),
        ("strike_rate", "Best strike rate"),
        ("bowling_average", "Best bowling average"),
        ("economy", "Best economy"),
    )

    season = forms.ChoiceField(required=False, label="Season")
    metric = forms.ChoiceField(required=False, choices=METRIC_CHOICES)

    def __init__(self, *args, seasons=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["season"].choices = [("", "Latest season")] + [
            (str(season), str(season)) for season in seasons
        ]
        if not self.is_bound:
            self.fields["metric"].initial = "runs"

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


class PlayerForm(forms.ModelForm):
    class Meta:
        model = Player
        fields = ("name", "current_team", "role", "nationality")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["current_team"].queryset = Team.objects.order_by("name")
        self.fields["current_team"].empty_label = "No team"


# Descriptive aliases used by older integrations.
PlayerSearchForm = PlayerFilterForm
PlayerRankingForm = PlayerRankingFilterForm
