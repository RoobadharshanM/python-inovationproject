# Data directory

The application never invents historical IPL records. Place the user's Kaggle
CSV files in the project-level `DataSets/` directory (or use
`python manage.py import_ipl_data --data-dir /path/to/csv/directory`).

The importer primarily supports the widely used Kaggle IPL schema:

### `matches.csv`

Required logical fields: match id, season, date, teams, venue. Optional fields
include city, toss winner/decision, winner, win margin, player of match,
umpires, and the result flag.

### `deliveries.csv`

Required logical fields: match id, inning, batting team, bowling team, over,
ball, batsman, bowler, batsman runs, and total runs. Dismissal, fielder,
wide, no-ball, bye, leg-bye, penalty, and super-over fields are used when
present.

Raw CSVs remain in `DataSets/`. Imported normalized data is stored in Django's
database. `data/processed/` contains generated training snapshots and the
SHA-256 import manifest used by the one-command launcher to avoid unnecessary
re-imports. `data/models/` stores joblib model artifacts.
