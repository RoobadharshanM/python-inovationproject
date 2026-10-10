"""Import validated IPL matches and delivery data from CSV files."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from matches.importer import (
    DataImportError,
    IPLDataImporter,
    discover_ipl_data_files,
    import_manifest_path,
)

# Must match run.MINIMUM_IMPORTER_VERSION.  An older manifest cannot describe a
# database that went through the current name-normalisation rules.
REQUIRED_IMPORTER_VERSION = 4


def dataset_is_current(data_dir: Path | None) -> tuple[bool, str]:
    """Return ``(up_to_date, reason)`` for the recorded import fingerprint.

    The importer publishes a manifest after each successful run.  Comparing the
    CSV checksums, the importer version and the row count is what lets a
    container restart skip a two-minute re-import.

    This mirrors ``run.import_manifest_matches`` so the launcher and the
    container always agree on when data must be refreshed.
    """

    manifest_file = import_manifest_path()
    if not manifest_file.is_file():
        return False, "no import manifest found"

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        matches_path, deliveries_path = discover_ipl_data_files(data_dir)
        digest = _file_sha256

        if manifest.get("status") != "complete":
            return False, "previous import did not complete"
        if int(manifest.get("importer_version", 0)) < REQUIRED_IMPORTER_VERSION:
            return False, (
                f"manifest was written by importer version "
                f"{manifest.get('importer_version')} "
                f"(needs >= {REQUIRED_IMPORTER_VERSION})"
            )
        if manifest.get("matches_sha256") != digest(matches_path):
            return False, "matches.csv changed on disk"
        if manifest.get("deliveries_sha256") != digest(deliveries_path):
            return False, "deliveries.csv changed on disk"

        expected = manifest.get("matches_imported")
        try:
            from matches.models import Match

            actual = Match.objects.filter(
                external_id__isnull=False
            ).distinct().count()
        except Exception:  # pragma: no cover - database not migrated yet
            return True, "database not queryable yet; relying on the manifest"
        if expected is not None and int(expected) != actual:
            return False, f"database holds {actual} matches, manifest records {expected}"
        return True, "dataset is up to date"
    except (OSError, ValueError, TypeError, json.JSONDecodeError, DataImportError) as exc:
        return False, f"could not read import manifest ({exc})"


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Command(BaseCommand):
    help = "Import and aggregate IPL matches.csv and deliveries.csv data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-dir",
            type=Path,
            default=None,
            help=(
                "Directory containing the CSV files. The project root and its "
                "DataSets/ or data/raw/ subdirectories are also accepted."
            ),
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Replace all existing IPL match, team, venue and player data.",
        )
        parser.add_argument(
            "--if-needed",
            action="store_true",
            dest="if_needed",
            help=(
                "Skip the import when the recorded fingerprint still matches the "
                "CSV source and the database row count. Safe to call on every "
                "container start."
            ),
        )

    def handle(self, *args, **options):
        if options.get("if_needed"):
            current, reason = dataset_is_current(options["data_dir"])
            if current:
                self.stdout.write(
                    self.style.SUCCESS(f"IPL dataset already imported ({reason}).")
                )
                return

        try:
            summary = IPLDataImporter(data_dir=options["data_dir"]).run(
                replace=options["replace"]
            )
        except (DataImportError, IntegrityError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "IPL import complete: "
                f"{summary.matches_created} matches created, "
                f"{summary.matches_updated} updated, "
                f"{summary.performances_imported} player performances and "
                f"{summary.season_performances_imported} season rows aggregated."
            )
        )
        self.stdout.write(
            f"Imported Teams: {summary.teams_imported} | "
            f"Venues: {summary.venues_imported} | "
            f"Players: {summary.players_imported} | "
            f"Innings: {summary.innings_imported}"
        )
        self.stdout.write(
            f"Source: {summary.source_files['matches']} | "
            f"{summary.source_files['deliveries']}"
        )
        if summary.duplicate_match_rows or summary.duplicate_delivery_rows:
            self.stdout.write(
                "Ignored duplicate source rows: "
                f"{summary.duplicate_match_rows} match, "
                f"{summary.duplicate_delivery_rows} delivery."
            )
        if summary.warnings:
            self.stdout.write(self.style.WARNING("Import warnings:"))
            for warning in summary.warnings[:20]:
                self.stdout.write(f"  - {warning}")
            if len(summary.warnings) > 20:
                self.stdout.write(f"  - ...and {len(summary.warnings) - 20} more")
