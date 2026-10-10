#!/usr/bin/env sh
#
# Container entrypoint.
#
# Responsibilities, in order:
#   1. Wait for the database to accept connections (Postgres starts slowly).
#   2. Apply migrations.
#   3. Collect static files so gunicorn can serve them without a separate nginx.
#   4. Make sure the IPL dataset is imported exactly once.
#   5. Make sure the ML model artifacts exist.
#   6. Hand over to the requested process (gunicorn by default).
#
# Every step is idempotent: restarting a container re-runs all of them but only
# imports data when the recorded manifest no longer matches the CSV source.

set -eu

log() { printf '[entrypoint] %s\n' "$*"; }

wait_for_database() {
    # SQLite has no server to wait for.
    if [ "${DB_ENGINE:-sqlite3}" != "postgresql" ]; then
        return 0
    fi

    attempts="${DB_WAIT_ATTEMPTS:-60}"
    log "waiting for postgres (up to ${attempts}s)..."
    i=0
    while [ "$i" -lt "$attempts" ]; do
        if python -c "
import os, sys
import psycopg
try:
    psycopg.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=os.environ.get('DB_PORT', '5432'),
        dbname=os.environ.get('DB_NAME', 'ipl'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', ''),
        connect_timeout=3,
    ).close()
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
            log "postgres is ready"
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done

    log "ERROR: postgres did not become ready in ${attempts}s"
    exit 1
}

ensure_dataset() {
    if [ "${IMPORT_SKIP_DATA:-0}" = "1" ]; then
        log "IMPORT_SKIP_DATA=1 - skipping dataset import"
        return 0
    fi
    # --if-needed compares the CSV checksums, the importer version and the row
    # count, so this is a no-op on every restart after the first boot.
    log "checking IPL dataset"
    python manage.py import_ipl_data --if-needed
}

models_present() {
    directory="${MODEL_DIRECTORY:-/app/data/models}"
    for artifact in \
        score_model.joblib score_preprocessor.joblib score_metadata.json \
        outcome_model.joblib outcome_preprocessor.joblib outcome_metadata.json
    do
        [ -s "${directory}/${artifact}" ] || return 1
    done
    return 0
}

ensure_models() {
    if [ "${TRAIN_SKIP_MODELS:-0}" = "1" ]; then
        log "TRAIN_SKIP_MODELS=1 - skipping ML model check"
        return 0
    fi
    log "checking ML model artifacts"
    if models_present; then
        log "model artifacts already present - skipping training"
        return 0
    fi
    log "training ML models (first boot only, this takes a few minutes)"
    python scripts/train_score_model.py
    python scripts/train_outcome_model.py
    models_present || {
        log "ERROR: training finished but artifacts are still missing"
        exit 1
    }
}

wait_for_database

log "applying migrations"
python manage.py migrate --noinput

log "collecting static files"
python manage.py collectstatic --noinput --clear

ensure_dataset
ensure_models

if [ "${CREATE_SUPERUSER:-0}" = "1" ]; then
    log "ensuring a superuser exists"
    python - <<'PY'
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model

username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")

if username and password:
    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=username,
        defaults={"email": email, "is_staff": True, "is_superuser": True},
    )
    changed = False
    for field, value in (("is_staff", True), ("is_superuser", True), ("email", email)):
        if value and getattr(user, field) != value:
            setattr(user, field, value)
            changed = True
    if created or changed:
        user.set_password(password)
        user.save()
    print("superuser ready:", username, "(created)" if created else "(updated)")
PY
fi

log "starting: $*"
exec "$@"