#!/usr/bin/env python3
"""One-command launcher for the IPL analytics Django project.

The launcher is deliberately self-contained: it uses only Python's standard
library, creates/uses ``venv/`` without requiring shell activation, installs
changed dependencies, migrates and verifies the configured database, safely
imports the user's Kaggle CSVs, trains missing model artifacts, checks Django,
and starts the development server.

Run from any directory with::

    python3 run.py             # macOS/Linux
    python run.py               # Windows
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent
MANAGE_PY = PROJECT_ROOT / "manage.py"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
VENV_DIRECTORY = PROJECT_ROOT / "venv"
MODEL_DIRECTORY = PROJECT_ROOT / "data" / "models"
IMPORT_MANIFEST = PROJECT_ROOT / "data" / "processed" / "ipl_import_manifest.json"
MINIMUM_PYTHON = (3, 11)

# These are imports used by this project. The module names differ from their
# pip package names for dotenv, Pillow and psycopg.
REQUIRED_IMPORTS = {
    "Django": "django",
    "pandas": "pandas",
    "numpy": "numpy",
    "scikit-learn": "sklearn",
    "joblib": "joblib",
    "matplotlib": "matplotlib",
    "seaborn": "seaborn",
    "python-dotenv": "dotenv",
    "Pillow": "PIL",
    "PostgreSQL driver": "psycopg",
    "timezone data": "tzdata",
}

FALLBACK_REQUIREMENTS = """\
Django>=5.1,<5.3
pandas>=2.2,<3.1
numpy>=1.26,<2.4
scikit-learn>=1.5,<1.8
joblib>=1.4,<2.0
matplotlib>=3.9,<3.11
seaborn>=0.13,<0.14
python-dotenv>=1.0,<2.0
Pillow>=10.4,<12.0
psycopg[binary]>=3.2,<4.0
tzdata>=2024.1
"""

DATASET_DIRECTORIES = (
    PROJECT_ROOT / "DataSets",
    PROJECT_ROOT / "datasets",
    PROJECT_ROOT / "dataset",
    PROJECT_ROOT / "data" / "raw",
    PROJECT_ROOT / "data",
)

MATCH_FILENAMES = (
    "matches.csv",
    "match.csv",
    "ipl_matches.csv",
    "matches_data.csv",
)
DELIVERY_FILENAMES = (
    "deliveries.csv",
    "delivery.csv",
    "ipl_deliveries.csv",
    "deliveries_data.csv",
)

MODEL_ARTIFACTS = {
    "score": {
        "script": PROJECT_ROOT / "scripts" / "train_score_model.py",
        "model": (
            "score_model.joblib",
            "score_model.pkl",
            "score_pipeline.joblib",
            "score.joblib",
        ),
        "preprocessor": ("score_preprocessor.joblib", "score_preprocessor.pkl"),
        "metadata": ("score_metadata.json",),
    },
    "outcome": {
        "script": PROJECT_ROOT / "scripts" / "train_outcome_model.py",
        "model": (
            "outcome_model.joblib",
            "outcome_prediction_model.pkl",
            "outcome_model.pkl",
        ),
        "preprocessor": (
            "outcome_preprocessor.joblib",
            "outcome_preprocessor.pkl",
        ),
        "metadata": ("outcome_metadata.json",),
    },
}

# Must stay in step with ``importer_version`` written by
# ``matches/importer.py``.  Bumping the importer changes how rows are
# normalised (player-name dedup, team branding), so an older manifest cannot be
# trusted to describe a correctly imported database.  ``test_run_launcher``
# asserts the two values match so they cannot silently drift apart.
MINIMUM_IMPORTER_VERSION = 4


class LauncherError(RuntimeError):
    """A concise, expected setup failure suitable for beginners."""


@dataclass(frozen=True)
class DatasetFiles:
    directory: Path
    matches: Path
    deliveries: Path


@dataclass
class SetupState:
    environment_ready: bool = False
    dependencies_ready: bool = False
    database_ready: bool = False
    dataset_available: bool = False
    dataset_imported: bool = False
    models_ready: bool = False
    django_checked: bool = False


def _supports_unicode(stream=sys.stdout) -> bool:
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "✓ ⚠ ✗ →".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


UNICODE = _supports_unicode()
SUCCESS = "✓" if UNICODE else "[OK]"
WARNING = "⚠" if UNICODE else "[!]"
ERROR = "✗" if UNICODE else "[ERROR]"
ARROW = "→" if UNICODE else "->"


def print_header(title: str) -> None:
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


def step(number: int, total: int, title: str) -> None:
    print(f"\n[{number}/{total}] {title}")


def success(message: str) -> None:
    print(f"  {SUCCESS} {message}")


def warning(message: str) -> None:
    print(f"  {WARNING} {message}")


def fail(message: str) -> None:
    print(f"  {ERROR} {message}", file=sys.stderr)


def info(message: str) -> None:
    print(f"  {ARROW} {message}")


def command_environment() -> dict[str, str]:
    """Make project imports and UTF-8 terminal output predictable."""

    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.setdefault("PYTHONUNBUFFERED", "1")
    # Always isolate this root-level launcher from an unrelated ambient
    # DJANGO_SETTINGS_MODULE inherited from the user's shell.
    environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
    return environment


def run_command(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    label: str,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and preserve its useful output."""

    command = [os.fspath(item) for item in arguments]
    info(label)
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=command_environment(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=capture,
            check=False,
        )
    except OSError as exc:
        raise LauncherError(f"Could not start {command[0]}: {exc}") from exc

    if capture:
        if completed.stdout:
            print(textwrap.indent(completed.stdout.rstrip(), "    "))
        if completed.stderr:
            print(textwrap.indent(completed.stderr.rstrip(), "    "), file=sys.stderr)
    if check and completed.returncode != 0:
        if capture:
            raise LauncherError(
                f"{label} failed with exit code {completed.returncode}. "
                "The command output above contains the useful error."
            )
        raise LauncherError(f"{label} failed with exit code {completed.returncode}.")
    return completed


def virtualenv_python() -> Path:
    relative = Path("Scripts") / "python.exe" if os.name == "nt" else Path("bin") / "python"
    return VENV_DIRECTORY / relative


def validate_project_layout() -> None:
    if not MANAGE_PY.is_file():
        raise LauncherError(
            "ERROR: manage.py was not found.\n\n"
            "Django project could not be located. Make sure run.py is placed in "
            "the Django project root."
        )


def create_or_locate_environment() -> Path:
    executable = virtualenv_python()
    if VENV_DIRECTORY.exists() and not executable.is_file():
        raise LauncherError(
            f"The virtual environment exists at {VENV_DIRECTORY}, but its Python "
            f"executable is missing: {executable}\n"
            "Move or remove that incomplete venv directory, then run this command again. "
            "The launcher will not delete it automatically."
        )
    if not VENV_DIRECTORY.exists():
        info(f"Creating virtual environment: {VENV_DIRECTORY}")
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "venv", str(VENV_DIRECTORY)],
                cwd=PROJECT_ROOT,
                env=command_environment(),
                text=True,
                check=False,
            )
        except OSError as exc:
            raise LauncherError(f"Could not create the virtual environment: {exc}") from exc
        if completed.returncode != 0:
            raise LauncherError(
                "Could not create the virtual environment. On Debian/Ubuntu, install "
                "the OS package python3-venv and try again."
            )
    if not executable.is_file():
        raise LauncherError(f"The virtual environment Python was not found: {executable}")
    verify_environment_version(executable)
    return executable


def verify_environment_version(python_executable: Path) -> None:
    probe = subprocess.run(
        [
            os.fspath(python_executable),
            "-c",
            "import sys; print('%d.%d.%d' % sys.version_info[:3])",
        ],
        cwd=PROJECT_ROOT,
        env=command_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise LauncherError(
            f"The virtual environment Python cannot start: {python_executable}\n"
            f"{probe.stderr.strip()}"
        )
    try:
        version = tuple(int(part) for part in probe.stdout.strip().split(".")[:3])
    except (TypeError, ValueError) as exc:
        raise LauncherError("Could not determine the virtual environment Python version.") from exc
    if version < MINIMUM_PYTHON:
        required = ".".join(str(part) for part in MINIMUM_PYTHON)
        found = ".".join(str(part) for part in version)
        raise LauncherError(
            f"venv/ uses Python {found}. Please install Python {required} or newer "
            "and recreate only that virtual environment, then run this command again."
        )


def ensure_pip(python_executable: Path) -> None:
    probe = subprocess.run(
        [os.fspath(python_executable), "-m", "pip", "--version"],
        cwd=PROJECT_ROOT,
        env=command_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        return
    info("pip is missing; bootstrapping it with ensurepip")
    run_command(
        [python_executable, "-m", "ensurepip", "--upgrade"],
        label="Bootstrapping pip",
    )


def ensure_requirements_file() -> None:
    if REQUIREMENTS_FILE.is_file():
        return
    warning("requirements.txt was not found; creating it from this project's imports.")
    try:
        REQUIREMENTS_FILE.write_text(FALLBACK_REQUIREMENTS, encoding="utf-8")
    except OSError as exc:
        raise LauncherError(f"Could not create requirements.txt: {exc}") from exc
    success(f"Created {REQUIREMENTS_FILE.name}")


def _requirements_digest() -> str:
    return hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()


def _dependencies_available(python_executable: Path) -> bool:
    module_list = sorted(REQUIRED_IMPORTS.values())
    code = (
        "import importlib\n"
        f"modules={module_list!r}\n"
        "for module in modules: importlib.import_module(module)\n"
        "import psycopg.pq\n"
        "print('dependencies-ok')\n"
    )
    return (
        subprocess.run(
            [os.fspath(python_executable), "-c", code],
            cwd=PROJECT_ROOT,
            env=command_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def _pip_dependencies_consistent(python_executable: Path) -> bool:
    try:
        return (
            subprocess.run(
                [os.fspath(python_executable), "-m", "pip", "check"],
                cwd=PROJECT_ROOT,
                env=command_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            ).returncode
            == 0
        )
    except OSError:
        return False


def ensure_dependencies(python_executable: Path) -> None:
    ensure_requirements_file()
    ensure_pip(python_executable)
    try:
        digest = _requirements_digest()
    except OSError as exc:
        raise LauncherError(f"Could not read requirements.txt: {exc}") from exc
    digest_file = VENV_DIRECTORY / ".ipl-requirements.sha256"

    if not _dependencies_available(python_executable):
        info("Required packages are missing or incomplete")
    else:
        try:
            previous = digest_file.read_text(encoding="utf-8").strip() if digest_file.is_file() else ""
        except (OSError, UnicodeError):
            previous = ""
        if previous == digest and _pip_dependencies_consistent(python_executable):
            success("Dependencies are ready (requirements unchanged).")
            return
        info("Dependencies are available; recording the current requirements.")

    run_command(
        [python_executable, "-m", "pip", "install", "-r", REQUIREMENTS_FILE],
        label="Installing Python dependencies",
    )
    if not _dependencies_available(python_executable):
        raise LauncherError(
            "Dependencies were installed, but one or more required packages could not "
            "be imported. Review the pip output above."
        )
    if not _pip_dependencies_consistent(python_executable):
        raise LauncherError(
            "Installed packages have incompatible dependencies. Run "
            f"'{virtualenv_python()} -m pip check' and resolve the reported packages."
        )
    try:
        digest_file.write_text(digest + "\n", encoding="utf-8")
    except OSError as exc:
        warning(f"Dependencies are ready, but their hash could not be saved: {exc}")
        return
    success("Python dependencies are ready.")


def django_version(python_executable: Path) -> str:
    completed = run_command(
        [python_executable, "-c", "import django; print(django.get_version())"],
        label="Checking Django",
        capture=True,
    )
    return completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "unknown"


def migrate_database(python_executable: Path) -> int:
    run_command(
        [python_executable, MANAGE_PY, "makemigrations", "--noinput"],
        label="Creating any missing migrations",
    )
    run_command(
        [python_executable, MANAGE_PY, "migrate", "--noinput"],
        label="Applying database migrations",
    )
    probe = run_command(
        [
            python_executable,
            MANAGE_PY,
            "shell",
            "-c",
            (
                "from django.db import connection; "
                "from matches.models import Match; "
                "connection.ensure_connection(); "
                "print('DB_MATCH_COUNT=' + str(Match.objects.count())); "
                "print('DB_EXTERNAL_COUNT=' + str(Match.objects.filter(external_id__isnull=False).count()))"
            ),
        ],
        label="Verifying database connection",
        capture=True,
    )
    values: dict[str, int] = {}
    for line in probe.stdout.splitlines():
        for key in ("DB_MATCH_COUNT", "DB_EXTERNAL_COUNT"):
            if line.startswith(key + "="):
                try:
                    values[key] = int(line.split("=", 1)[1])
                except ValueError:
                    pass
    if "DB_MATCH_COUNT" not in values or "DB_EXTERNAL_COUNT" not in values:
        raise LauncherError("The database check completed, but its match counts were unreadable.")
    return values["DB_MATCH_COUNT"], values["DB_EXTERNAL_COUNT"]


def _file_in_directory(directory: Path, names: Iterable[str]) -> Path | None:
    if not directory.is_dir():
        return None
    files = {
        child.name.casefold(): child
        for child in directory.iterdir()
        if child.is_file()
    }
    for name in names:
        candidate = files.get(name.casefold())
        if candidate is not None:
            return candidate
    return None


def discover_dataset() -> DatasetFiles | None:
    checked: list[Path] = []
    for directory in DATASET_DIRECTORIES:
        try:
            resolved = directory.resolve()
        except OSError:
            resolved = directory
        if resolved in checked:
            continue
        checked.append(resolved)
        matches = _file_in_directory(resolved, MATCH_FILENAMES)
        deliveries = _file_in_directory(resolved, DELIVERY_FILENAMES)
        if matches and deliveries:
            return DatasetFiles(resolved, matches.resolve(), deliveries.resolve())
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_manifest_matches(dataset: DatasetFiles, external_match_count: int) -> bool:
    """Return true only for the same completed CSV source and row count."""

    if not IMPORT_MANIFEST.is_file():
        return False
    try:
        manifest = json.loads(IMPORT_MANIFEST.read_text(encoding="utf-8"))
        return bool(
            manifest.get("status") == "complete"
            and int(manifest.get("importer_version", 0)) >= MINIMUM_IMPORTER_VERSION
            and manifest.get("matches_sha256") == _file_sha256(dataset.matches)
            and manifest.get("deliveries_sha256") == _file_sha256(dataset.deliveries)
            and int(manifest.get("matches_imported", -1)) == external_match_count
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _import_command_available() -> bool:
    command_file = (
        PROJECT_ROOT
        / "matches"
        / "management"
        / "commands"
        / "import_ipl_data.py"
    )
    return command_file.is_file()


def import_dataset(python_executable: Path, files: DatasetFiles) -> tuple[int, int]:
    if _import_command_available():
        run_command(
            [
                python_executable,
                MANAGE_PY,
                "import_ipl_data",
                "--data-dir",
                files.directory,
            ],
            label="Importing and aggregating the IPL Kaggle dataset",
        )
    else:
        fallback = PROJECT_ROOT / "scripts" / "import_dataset.py"
        if not fallback.is_file():
            raise LauncherError(
                "IPL CSV files were found, but this project has no import_ipl_data "
                "management command or scripts/import_dataset.py fallback."
            )
        run_command(
            [python_executable, fallback, "--data-dir", files.directory],
            label="Importing the IPL Kaggle dataset",
        )
    return imported_match_counts(python_executable)


def imported_match_counts(python_executable: Path) -> tuple[int, int]:
    completed = run_command(
        [
            python_executable,
            MANAGE_PY,
            "shell",
            "-c",
            "from matches.models import Match; "
            "print('IPL_MATCH_COUNT=' + str(Match.objects.count())); "
            "print('IPL_EXTERNAL_COUNT=' + str(Match.objects.filter(external_id__isnull=False).count()))",
        ],
        label="Checking imported IPL data",
        capture=True,
    )
    values: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        for key in ("IPL_MATCH_COUNT", "IPL_EXTERNAL_COUNT"):
            if line.startswith(key + "="):
                try:
                    values[key] = int(line.split("=", 1)[1])
                except ValueError:
                    pass
    if "IPL_MATCH_COUNT" not in values or "IPL_EXTERNAL_COUNT" not in values:
        raise LauncherError("Could not determine whether IPL data is already imported.")
    return values["IPL_MATCH_COUNT"], values["IPL_EXTERNAL_COUNT"]


def dataset_warning(files: DatasetFiles | None) -> None:
    if files:
        return
    warning("IPL dataset not found.")
    warning("Expected matches.csv and deliveries.csv in one of:")
    for directory in DATASET_DIRECTORIES:
        warning(f"  - {directory}")
    warning("The website will still start with an informative empty-data state.")


def _first_existing(names: Iterable[str]) -> Path | None:
    for name in names:
        candidate = MODEL_DIRECTORY / name
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def model_is_complete(kind: str) -> bool:
    names = MODEL_ARTIFACTS[kind]
    return all(_first_existing(names[key]) is not None for key in ("model", "preprocessor", "metadata"))


def train_model(python_executable: Path, kind: str) -> bool:
    script = MODEL_ARTIFACTS[kind]["script"]
    if not script.is_file():
        warning(f"{kind.title()} training script not found: {script.relative_to(PROJECT_ROOT)}")
        return False
    try:
        completed = run_command(
            [python_executable, script],
            label=f"Training the {kind} model (this happens only when artifacts are missing)",
            check=False,
        )
    except LauncherError as exc:
        warning(str(exc))
        return False
    if completed.returncode != 0:
        warning(
            f"The {kind} model could not be trained. Analytics will still run; "
            "the prediction page will explain that the model is unavailable."
        )
        return False
    if not model_is_complete(kind):
        warning(f"The {kind} training command finished but did not publish all expected artifacts.")
        return False
    success(f"{kind.title()} model artifacts are ready.")
    return True


def validate_model_artifacts(python_executable: Path) -> set[str]:
    """Load both artifacts through Django and make real smoke predictions."""

    code = r'''
from predictions.ml_model import load_model, predict_outcome, predict_score
valid = []
for kind in ("score", "outcome"):
    try:
        artifacts = load_model(kind)
        if artifacts.metadata.get("kind") != kind:
            raise RuntimeError("metadata kind does not match the artifact")
        if not isinstance(artifacts.metadata.get("feature_names"), list):
            raise RuntimeError("metadata has no feature schema")
        common = {
            "batting_team": "__launcher_validation__",
            "bowling_team": "__launcher_validation_opponent__",
            "team1": "__launcher_validation__",
            "team2": "__launcher_validation_opponent__",
            "venue": "__launcher_validation_venue__",
            "toss_winner": "__launcher_validation__",
            "toss_decision": "bat",
            "season": "2000",
            "match_type": "league",
            "current_score": 0,
            "overs_completed": 0,
            "wickets": 0,
            "current_run_rate": 0,
            "last_5_overs_runs": 0,
            "recent_wickets": 0,
        }
        if kind == "score":
            predict_score(common)
        else:
            predict_outcome(common)
        valid.append(kind)
    except Exception:
        pass
print("VALID_MODELS=" + ",".join(valid))
'''
    completed = subprocess.run(
        [
            os.fspath(python_executable),
            os.fspath(MANAGE_PY),
            "shell",
            "-c",
            code,
        ],
        cwd=PROJECT_ROOT,
        env=command_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        warning("The model validation command could not start; missing models will be retrained.")
        return set()
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("VALID_MODELS="):
            return {item for item in line.split("=", 1)[1].split(",") if item}
    warning("Model validation did not return a status; missing models will be retrained.")
    return set()


def ensure_models(python_executable: Path, dataset: DatasetFiles | None, force: bool) -> bool:
    try:
        MODEL_DIRECTORY.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warning(f"Model directory is not writable: {exc}")
        return False
    if force:
        info("--train requested; both models will be retrained from the current IPL dataset.")
        if dataset is None:
            warning("IPL dataset not found. ML model training was skipped.")
            return False
        results = [train_model(python_executable, kind) for kind in ("score", "outcome")]
        return all(results) and validate_model_artifacts(python_executable) == {
            "score",
            "outcome",
        }

    valid = validate_model_artifacts(python_executable)
    missing = [
        kind
        for kind in ("score", "outcome")
        if kind not in valid or not model_is_complete(kind)
    ]
    if not missing:
        success("Machine learning models found and load-tested; retraining skipped.")
        return True
    if dataset is None:
        warning("One or more ML models are missing or invalid.")
        warning("IPL dataset not found. ML model training was skipped.")
        warning("Place the Kaggle CSVs in DataSets/ and run this command again.")
        return False

    info("Required models are missing or invalid; training only the affected model(s).")
    results = [train_model(python_executable, kind) for kind in missing]
    return all(results) and validate_model_artifacts(python_executable) == {
        "score",
        "outcome",
    }


def django_check(python_executable: Path) -> None:
    run_command(
        [python_executable, MANAGE_PY, "check"],
        label="Running Django system check",
    )


def browser_url(host: str, port: int) -> str:
    browser_host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
    if ":" in browser_host and not browser_host.startswith("["):
        browser_host = f"[{browser_host}]"
    return f"http://{browser_host}:{port}/"


def print_ready_summary(state: SetupState, host: str, port: int) -> None:
    url = browser_url(host, port)
    print_header(" IPL MATCH PERFORMANCE ANALYSIS SYSTEM ")
    environment_label = "Environment ready" if state.environment_ready else "Setup skipped (--server)"
    dependencies_label = "Dependencies ready" if state.dependencies_ready else "Dependencies not checked"
    database_label = "Database ready" if state.database_ready else "Database migrations not checked"
    print(f" {SUCCESS if state.environment_ready else WARNING} {environment_label}")
    print(f" {SUCCESS if state.dependencies_ready else WARNING} {dependencies_label}")
    print(f" {SUCCESS if state.database_ready else WARNING} {database_label}")
    if state.dataset_imported:
        print(f" {SUCCESS} IPL data ready")
    else:
        print(f" {WARNING} IPL data was not checked in --server mode")
    if state.models_ready:
        print(f" {SUCCESS} ML models ready")
    else:
        print(f" {WARNING} ML model readiness was not confirmed")
    if state.django_checked:
        print(f" {SUCCESS} Django system check passed")
    else:
        print(f" {WARNING} Django system check skipped (--server)")
    print("\nServer starting...\n")
    print(f"Open your browser: {url}")
    print("Press CTRL+C to stop the server.")
    print("=" * 66)


def open_browser_when_ready(url: str) -> None:
    # Failure to launch a browser is deliberately non-fatal.
    try:
        if not webbrowser.open_new_tab(url):
            warning("The browser could not be opened automatically; use the URL above.")
    except Exception as exc:  # browser availability differs across desktops
        warning(f"The browser could not be opened automatically ({exc}).")


def _http_server_ready(url: str) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": "IPL-Launcher/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=0.6) as response:
            return 200 <= int(response.status) < 500
    except urllib.error.HTTPError as exc:
        # Django is listening even when it returns a host/permission response.
        return exc.code < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def validate_server_options(host: str, port: int) -> None:
    if not isinstance(host, str) or not host.strip() or any(char.isspace() for char in host):
        raise LauncherError("--host must be a non-empty hostname or IP address without spaces.")
    if "/" in host:
        raise LauncherError("--host must not contain a URL path or slash.")
    if not (1 <= port <= 65535):
        raise LauncherError("--port must be between 1 and 65535.")


def server_address(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def debug_enabled() -> bool:
    """Return whether Django will run in debug mode.

    Mirrors ``env_bool("DJANGO_DEBUG", True)`` from ``config.settings`` so the
    launcher does not need to import the settings module.
    """

    return os.environ.get("DJANGO_DEBUG", "True").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def start_server(
    python_executable: Path,
    state: SetupState,
    host: str,
    port: int,
    open_browser: bool,
) -> int:
    print_ready_summary(state, host, port)
    url = browser_url(host, port)
    command = [
        os.fspath(python_executable),
        os.fspath(MANAGE_PY),
        "runserver",
        server_address(host, port),
    ]
    # Django's cached template loader never re-checks file timestamps: template
    # reloading is driven by the autoreload machinery.  Running with
    # "--noreload" therefore means every saved .html change needs a manual
    # restart, which is very easy to miss.  Keep the reloader on while
    # developing and only disable it when DEBUG is off.
    if not debug_enabled():
        command.append("--noreload")
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=command_environment(),
        )
    except OSError as exc:
        raise LauncherError(f"Could not start Django's server: {exc}") from exc

    browser_opened = False
    readiness_deadline = time.monotonic() + 15
    try:
        while True:
            return_code = process.poll()
            if return_code is not None:
                return return_code
            if (
                open_browser
                and not browser_opened
                and time.monotonic() <= readiness_deadline
                and _http_server_ready(url)
            ):
                open_browser_when_ready(url)
                browser_opened = True
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopping Django server...")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        print("Server stopped.")
        return 0


def server_only(host: str, port: int, open_browser: bool) -> int:
    validate_project_layout()
    executable = virtualenv_python()
    if not executable.is_file():
        executable = Path(sys.executable)
        warning("The venv/ environment is missing; --server is using the current Python.")
    else:
        verify_environment_version(executable)
    django_probe = subprocess.run(
        [os.fspath(executable), "-c", "import django"],
        cwd=PROJECT_ROOT,
        env=command_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if django_probe.returncode != 0:
        launcher = "python run.py" if os.name == "nt" else "python3 run.py"
        raise LauncherError(
            "Django is not available in the selected Python environment. "
            f"Run `{launcher}` once without --server to prepare it."
        )
    state = SetupState(environment_ready=True)
    return start_server(executable, state, host, port, open_browser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set up and run the complete IPL Django project with one command.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the complete safe setup workflow (the default behavior).",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="Skip setup and start the server immediately with the current/venv Python.",
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="Force retraining of both ML models after setup/data import.",
    )
    parser.add_argument(
        "--import-data",
        action="store_true",
        help="Safely re-run the existing idempotent IPL dataset importer.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Complete setup and checks without starting the server.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the default web browser automatically.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Django development server host.")
    parser.add_argument("--port", type=int, default=8000, help="Django development server port.")
    return parser


def normal_setup(args: argparse.Namespace) -> int:
    state = SetupState()
    total_steps = 8
    step(1, total_steps, "Checking Python and Django project")
    version = ".".join(str(part) for part in sys.version_info[:3])
    success(f"Python {version} on {platform.system() or 'unknown OS'}")
    validate_project_layout()
    success(f"Django entry point found: {MANAGE_PY.name}")
    if args.setup:
        info("Complete setup mode selected.")

    step(2, total_steps, "Checking/creating virtual environment")
    python_executable = create_or_locate_environment()
    success(f"Using {python_executable.relative_to(PROJECT_ROOT)}")
    state.environment_ready = True

    step(3, total_steps, "Checking/installing Python dependencies")
    ensure_dependencies(python_executable)
    state.dependencies_ready = True

    step(4, total_steps, "Checking Django")
    success(f"Django {django_version(python_executable)} detected")

    step(5, total_steps, "Updating and verifying database")
    match_count, external_match_count = migrate_database(python_executable)
    success("Database migrations are up to date.")
    state.database_ready = True

    step(6, total_steps, "Checking IPL dataset")
    dataset = discover_dataset()
    if dataset:
        state.dataset_available = True
        success(f"Dataset found: {dataset.directory}")
        info(f"matches: {dataset.matches.name} | deliveries: {dataset.deliveries.name}")
    else:
        dataset_warning(dataset)

    if args.import_data:
        if dataset is None:
            raise LauncherError(
                "--import-data was requested, but no matches.csv/deliveries.csv pair was found. "
                "Place both files in DataSets/ (or data/raw/) and try again."
            )
        match_count, external_match_count = import_dataset(python_executable, dataset)
    elif match_count > 0 and dataset is not None and import_manifest_matches(
        dataset, external_match_count
    ):
        success(f"IPL dataset already imported ({match_count:,} matches). Skipping data import.")
    elif match_count > 0 and dataset is not None:
        info("The CSV source or import manifest changed; safely refreshing imported data.")
        match_count, external_match_count = import_dataset(python_executable, dataset)
    elif match_count > 0:
        success(
            f"IPL records are already present ({match_count:,} matches); "
            "CSV verification was skipped because the source files are unavailable."
        )
    elif dataset:
        match_count, external_match_count = import_dataset(python_executable, dataset)
    else:
        warning("No imported IPL records were found.")

    if match_count > 0:
        state.dataset_imported = True
        success(f"IPL data is ready ({match_count:,} matches).")
    elif args.import_data:
        raise LauncherError("The importer completed but no IPL matches are present in the database.")
    else:
        warning("No IPL matches are currently available; analytics pages will show an empty state.")

    step(7, total_steps, "Checking machine-learning models")
    state.models_ready = ensure_models(python_executable, dataset, force=args.train)
    if args.train and dataset is not None and not state.models_ready:
        raise LauncherError(
            "Model training was explicitly requested, but one or more models could "
            "not be trained and load-tested. Review the training output above."
        )

    step(8, total_steps, "Django system check")
    django_check(python_executable)
    state.django_checked = True
    success("Django system check passed.")

    if args.check_only:
        print_header(" SETUP COMPLETE — SERVER NOT STARTED ")
        success("All requested setup and safety checks completed successfully.")
        return 0
    return start_server(
        python_executable,
        state,
        args.host,
        args.port,
        open_browser=not args.no_browser,
    )


def main(argv: Sequence[str] | None = None) -> int:
    if sys.version_info < MINIMUM_PYTHON:
        raise LauncherError(
            "Python 3 was not detected at version 3.11 or newer.\n\n"
            "Please install Python 3.11 or newer and try again."
        )
    args = build_parser().parse_args(argv)
    validate_server_options(args.host, args.port)
    print_header(" IPL MATCH PERFORMANCE ANALYSIS & SCORE PREDICTION ")
    print("One-command setup and development server")
    if args.server:
        if args.train or args.import_data or args.setup or args.check_only:
            raise LauncherError("--server cannot be combined with setup, train, import, or check-only options.")
        return server_only(args.host, args.port, open_browser=not args.no_browser)
    return normal_setup(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LauncherError as exc:
        fail(str(exc))
        raise SystemExit(1)
    except OSError as exc:
        fail(f"A filesystem or process operation failed: {exc}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        raise SystemExit(130)
