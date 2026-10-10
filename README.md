# IPL Match Performance Analysis and Score Prediction System

A production-style Django application for exploring historical Indian Premier
League data, comparing teams and players, and generating machine-learning
score and match-outcome estimates. Every dashboard value, ranking and model
result comes from the imported Kaggle data or a trained model. The project does
not contain fabricated historical statistics.

## Project overview

- Normalized Django models for teams, venues, matches, innings, players and
  player performances
- Import support for the common Kaggle `matches.csv` + `deliveries.csv` schemas
- Searchable and filterable match and player catalogues
- Dynamic IPL dashboard with responsive Chart.js visualizations
- Team, venue, season, head-to-head and player-comparison analytics
- Scikit-learn score regressor and outcome classifier
- Authenticated prediction forms, JSON APIs and prediction history
- Django Admin, light/dark themes and responsive layouts
- SQLite by default, with an environment-driven PostgreSQL configuration

## Quick Start — one command

### macOS / Linux

```bash
cd /path/to/python-inovationproject
python3 run.py
```

### Windows

```powershell
cd path\to\python-inovationproject
python run.py
```

No virtual-environment activation is required. The root-level `run.py`
launcher automatically:

- creates `venv/` when it does not exist and reuses it on later runs;
- installs missing or changed dependencies from `requirements.txt` without
  reinstalling them every day;
- creates and applies Django migrations without deleting the database, users,
  or existing records;
- detects `matches.csv` and `deliveries.csv`;
- imports the IPL dataset only when its SHA-256 source manifest is missing or
  stale, using the project's idempotent `import_ipl_data` management command;
- load-tests complete score/outcome model artifacts and retrains only missing
  or invalid ones when the dataset is available;
- runs `python manage.py check`;
- starts Django at <http://127.0.0.1:8000/> and safely opens the default
  browser.

Safe repeated execution never duplicates imported matches, deletes users,
resets the database, or retrains valid model artifacts. The raw dataset and
generated artifacts are preserved.

Useful optional commands:

```bash
python3 run.py --check-only       # complete setup without starting the server
python3 run.py --server           # immediately start with the existing venv
python3 run.py --import-data      # safely re-run the idempotent importer
python3 run.py --train            # intentionally retrain both ML models
python3 run.py --no-browser       # do not open a browser automatically
python3 run.py --port 8001        # use a different development port
```

## Technology stack

**Python 3.11+ · Django 5.x · Django ORM · SQLite/PostgreSQL · Pandas ·
NumPy · scikit-learn · joblib · Bootstrap 5 · Chart.js · HTML5 · CSS3 ·
vanilla JavaScript**

## Project structure

```text
.
├── run.py                  one-command setup and server launcher
├── manage.py
├── config/                 Django project configuration
├── dashboard/              landing, dashboard, authentication and core API
├── matches/                teams, venues, matches, innings and CSV importer
├── players/                players, affiliations and performance statistics
├── analytics/              team, venue, player and comparison analytics
├── predictions/            prediction views, persistence and model service
├── scripts/                model-training entry points
├── data/
│   ├── models/             generated joblib model artifacts
│   ├── processed/          optional generated feature tables
│   └── raw/                schema and pipeline documentation
├── DataSets/               user's Kaggle CSV files
├── static/                 CSS, JavaScript and SVG artwork
└── templates/              shared HTML templates
```

## Manual installation (advanced troubleshooting)

The normal setup is the one-command Quick Start above. If you need to diagnose
an individual layer, use these commands manually. Python 3.11 or newer is
recommended:

### macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
```

### Windows (PowerShell)

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The repository can also be run with [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
```

## Configuration

Local development works with SQLite and safe development defaults. For a
production deployment, copy `.env.example` to `.env`, generate a long random
`DJANGO_SECRET_KEY`, set `DJANGO_DEBUG=False`, configure allowed hosts and
enable HTTPS security settings. For PostgreSQL, set `DB_ENGINE=postgresql` and
the `DB_*` variables; install the PostgreSQL driver if your environment needs
it (`psycopg[binary]`).

Never commit `.env` or the development database.

## Database setup

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

The application includes its initial migrations; `run.py` also checks for new
model changes on every safe setup. When customizing models manually, run
`makemigrations` as shown above.

## Dataset setup and import

Place these files in the project-level `DataSets/` directory:

```text
DataSets/
├── matches.csv
└── deliveries.csv
```

The supplied dataset is already in that location. Column names are detected
case- and punctuation-insensitively, and common aliases from other Kaggle IPL
versions are supported. A directory can also be supplied explicitly:

```bash
python manage.py import_ipl_data
python manage.py import_ipl_data --data-dir /path/to/ipl/csv/files
python manage.py import_ipl_data --replace
```

The command validates required fields, parses dates, normalizes team and venue
names, removes duplicate records, imports teams/venues/matches, aggregates
innings and player performance, prints counts and raises a clear error for
invalid data. `--replace` removes the prior imported match data before loading;
use it when a dataset has been replaced.

See [`data/raw/SCHEMA.md`](data/raw/SCHEMA.md) for accepted columns and
[`data/README.md`](data/README.md) for the raw/processed data policy.

## Machine-learning training

Import the dataset and apply migrations before training:

```bash
python scripts/train_score_model.py
python scripts/train_outcome_model.py
```

The score model uses match-state features available at prediction time and a
regression pipeline. It prints actual MAE, RMSE and R² values. The displayed
prediction interval is based on empirical held-out absolute residuals; it is
not a fabricated confidence percentage. Artifacts are published as
`data/models/score_model.joblib`,
`data/models/score_preprocessor.joblib`, and `data/models/score_metadata.json`.

The outcome model uses teams, venue, toss and season and prints actual
accuracy, precision, recall and F1. Probabilities are shown only when generated
by the trained classifier. Its artifacts are
`data/models/outcome_model.joblib`,
`data/models/outcome_preprocessor.joblib`, and
`data/models/outcome_metadata.json`. Models are loaded lazily during requests
and are never retrained by a page request.

Delete model artifacts and retrain after a substantial dataset replacement:

```bash
rm data/models/*.joblib data/models/*.json
python scripts/train_score_model.py
python scripts/train_outcome_model.py
```

## Running the server

```bash
python manage.py check
python manage.py runserver
```

Open <http://127.0.0.1:8000/>. Browse the public catalogue and analytics pages
anonymously. Sign in to create and manage personal predictions. The Admin site
is available at `/admin/` for users with staff permissions.

## API endpoints

JSON responses are used by the interactive dashboard, analytics and prediction
forms. Most read endpoints accept `GET` query parameters. Prediction endpoints
require an authenticated session and Django CSRF token.

| Endpoint | Purpose |
| --- | --- |
| `/health/` | lightweight local readiness response |
| `/api/dashboard-stats/` | KPI cards and dashboard chart datasets |
| `/api/team-stats/` | Dynamic team records and win rates |
| `/api/player-stats/` | Dynamic player rankings and statistics |
| `/api/season-stats/` | Season-level totals and rates |
| `/api/venue-stats/` | Venue scoring and result analysis |
| `/api/analytics-data/` | Filtered advanced-analytics data |
| `/api/team-comparison/` | Head-to-head comparison of two teams |
| `/api/player-comparison/` | Side-by-side player statistics |
| `/api/predict-score/` | Authenticated JSON score estimate |
| `/api/predict-outcome/` | Authenticated JSON outcome estimate |

The response envelope distinguishes `ok`, `data` and user-friendly `error`
messages. Empty databases return a valid empty-data response rather than
fabricated values.

## Major URLs

- `/` — landing page
- `/dashboard/` — KPI dashboard
- `/matches/` — match search/filter list
- `/matches/<id>/` — match scorecard and toss/result details
- `/players/` — player rankings and filters
- `/players/<id>/` — batting/bowling analysis
- `/analytics/` — team, venue, player and comparison tools
- `/predictions/score/` — score prediction form
- `/predictions/outcome/` — outcome prediction form
- `/predictions/history/` — authenticated prediction history

## Testing and quality checks

```bash
python manage.py check
python manage.py test
python manage.py check --deploy  # review production security warnings
```

The checks cover an empty database, invalid form input, missing model files,
JSON errors, authentication and core data aggregations. The importer is
transactional where practical, and normal request failures produce friendly
Django error pages rather than tracebacks.

## Screenshots

<!-- Add screenshots of the landing page, dashboard, match details, player
analysis, prediction form and mobile view here after deployment/demo. -->

## Troubleshooting

### “No IPL data has been imported”

Run `python manage.py migrate` and then
`python manage.py import_ipl_data --data-dir DataSets`. Confirm that the two
CSV filenames and required columns are present.

### “The prediction model is not trained”

Run both training scripts. A trained model is a generated artifact and is not
fabricated when absent. Browser pages and APIs return a clear message until it
exists.

### CSV column or date errors

The importer reports detected columns and missing requirements. Save the CSV as
UTF-8, inspect its header, and review the supported schema. Dates are parsed
with Pandas; genuinely invalid dates are not guessed.

### Port already in use

Run `python manage.py runserver 8001` and open
<http://127.0.0.1:8001/>.

### PostgreSQL driver missing

Install the DB API driver appropriate for your environment or use SQLite for
local development.

### Static files in production

Run `python manage.py collectstatic` and configure the web server or CDN using
`STATIC_ROOT`; do not serve the development static directory directly.

## Future enhancements

- Extend to more recent Kaggle snapshots and explicit tournament-stage columns
- Add player head-to-head and season-over-season comparison modes
- Add PostgreSQL/Redis deployment profiles and object storage for logos
- Add model-drift monitoring and scheduled retraining jobs
- Add downloadable CSV/JSON reports and a public read-only API
- Add role-based admin workflows and audit logging

---

*Turning IPL data into meaningful insights.*
