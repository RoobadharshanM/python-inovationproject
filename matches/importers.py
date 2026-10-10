"""Compatibility exports for applications that use the plural importer name.

The implementation lives in :mod:`matches.importer`; keeping this tiny module
avoids forcing integrations to depend on a particular import spelling.
"""

from matches.importer import (  # noqa: F401
    DataDiscoveryError,
    DataImportError,
    DataValidationError,
    ImportIssue,
    ImportSummary,
    IPLDataImporter,
    IPLImporter,
    IPLMatchesImporter,
    clean_text,
    discover_data_files,
    discover_ipl_data_files,
    import_ipl_data,
    normalize_city_name,
    normalize_team_name,
    normalize_venue_name,
    parse_match_date,
)

__all__ = [
    "DataDiscoveryError",
    "DataImportError",
    "DataValidationError",
    "ImportIssue",
    "ImportSummary",
    "IPLDataImporter",
    "IPLImporter",
    "IPLMatchesImporter",
    "clean_text",
    "discover_data_files",
    "discover_ipl_data_files",
    "import_ipl_data",
    "normalize_city_name",
    "normalize_team_name",
    "normalize_venue_name",
    "parse_match_date",
]
