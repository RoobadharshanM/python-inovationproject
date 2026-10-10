# Supported Kaggle IPL schema

The canonical dataset used by this project contains:

- `matches.csv`: `id, season, city, date, team1, team2, toss_winner,
  toss_decision, result, dl_applied, winner, win_by_runs, win_by_wickets,
  player_of_match, venue, umpire1, umpire2, umpire3`
- `deliveries.csv`: `match_id, inning, batting_team, bowling_team, over, ball,
  batsman, non_striker, bowler, is_super_over, wide_runs, bye_runs,
  legbye_runs, noball_runs, penalty_runs, batsman_runs, extra_runs,
  total_runs, player_dismissed, dismissal_kind, fielder`

Column matching is case- and punctuation-insensitive. Common aliases from
other Kaggle versions are accepted. A missing required field produces a clear
import error instead of guessed data.
