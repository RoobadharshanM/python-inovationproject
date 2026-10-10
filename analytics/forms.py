"""GET-friendly filtering forms for the advanced analytics pages."""

from django import forms
from django.db import DatabaseError

from matches.models import Match, Team, Venue
from players.models import Player


def _protect_model_querysets(form):
    """Keep filter forms renderable while the schema is being created."""
    for field in form.fields.values():
        if isinstance(field, forms.ModelChoiceField):
            try:
                field.queryset.exists()
            except DatabaseError:
                field.queryset = field.queryset.none()
    return form


def season_choices(seasons=None, *, include_all=True, blank_label="All seasons"):
    if seasons is None:
        seasons = Match.objects.values_list("season", flat=True).distinct().order_by("season")
    try:
        seasons = list(seasons)
    except DatabaseError:
        # Allow the analytics shell to render a useful empty filter state while
        # a development database is being migrated.
        seasons = []
    choices = []
    if include_all:
        choices.append(("", blank_label))
    choices.extend((str(season), str(season)) for season in seasons)
    return choices


class SeasonFilterForm(forms.Form):
    season = forms.ChoiceField(required=False, label="Season")
    team = forms.ModelChoiceField(
        queryset=Team.objects.all(), required=False, label="Team"
    )
    venue = forms.ModelChoiceField(
        queryset=Venue.objects.all(), required=False, label="Venue"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["season"].choices = season_choices()
        _protect_model_querysets(self)


class TeamComparisonForm(SeasonFilterForm):
    team_a = forms.ModelChoiceField(queryset=Team.objects.all(), label="First team")
    team_b = forms.ModelChoiceField(queryset=Team.objects.all(), label="Second team")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A matchup already has two explicit team controls; the generic
        # overview ``team`` filter would be ambiguous here.
        self.fields.pop("team", None)
        # Put the matchup controls first, while retaining season and venue.
        order = ["team_a", "team_b", "season", "venue"]
        self.order_fields(order)

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("team_a")
            and cleaned.get("team_b")
            and cleaned["team_a"].pk == cleaned["team_b"].pk
        ):
            raise forms.ValidationError("Choose two different teams to compare.")
        return cleaned


class PlayerComparisonForm(forms.Form):
    player_a = forms.ModelChoiceField(queryset=Player.objects.all(), label="First player")
    player_b = forms.ModelChoiceField(queryset=Player.objects.all(), label="Second player")
    season = forms.ChoiceField(required=False, label="Season")
    team = forms.ModelChoiceField(
        queryset=Team.objects.all(), required=False, label="Team"
    )
    venue = forms.ModelChoiceField(
        queryset=Venue.objects.all(), required=False, label="Venue"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["season"].choices = season_choices()
        _protect_model_querysets(self)
        self.order_fields(["player_a", "player_b", "season", "team", "venue"])

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("player_a")
            and cleaned.get("player_b")
            and cleaned["player_a"].pk == cleaned["player_b"].pk
        ):
            raise forms.ValidationError("Choose two different players to compare.")
        return cleaned


class VenueAnalysisForm(forms.Form):
    venue = forms.ModelChoiceField(queryset=Venue.objects.all(), label="Venue")
    season = forms.ChoiceField(required=False, label="Season")
    team = forms.ModelChoiceField(
        queryset=Team.objects.all(), required=False, label="Team"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["season"].choices = season_choices()
        _protect_model_querysets(self)


class PlayerTrendForm(forms.Form):
    player = forms.ModelChoiceField(queryset=Player.objects.all(), label="Player")
    team = forms.ModelChoiceField(
        queryset=Team.objects.all(), required=False, label="Team"
    )
    venue = forms.ModelChoiceField(
        queryset=Venue.objects.all(), required=False, label="Venue"
    )
    start_season = forms.ChoiceField(required=False, label="From season")
    end_season = forms.ChoiceField(required=False, label="To season")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = season_choices(include_all=False)
        self.fields["start_season"].choices = [("", "First available")] + choices
        self.fields["end_season"].choices = [("", "Latest available")] + choices
        _protect_model_querysets(self)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_season"), cleaned.get("end_season")
        if start and end and int(start) > int(end):
            raise forms.ValidationError("The start season must not be after the end season.")
        return cleaned


class HeadToHeadForm(TeamComparisonForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A head-to-head starts with the matchup, with venue as a further split.
        self.order_fields(["team_a", "team_b", "season", "venue"])


# Compatibility names for code that uses the shorter form labels.
AnalyticsFilterForm = SeasonFilterForm
TeamCompareForm = TeamComparisonForm
PlayerCompareForm = PlayerComparisonForm
VenueFilterForm = VenueAnalysisForm
PlayerTrendFilterForm = PlayerTrendForm
