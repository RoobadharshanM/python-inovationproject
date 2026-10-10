"""Team branding metadata and generated crest artwork.

The application renders a crisp, generated crest for every team instead of
bundling third-party trademark artwork.  Each crest is an inline SVG built from
the franchise's real brand colours, so it stays sharp at any size, costs no
extra network request and never causes layout shift.

If a club later obtains official artwork it can be uploaded through the Django
admin (``Team.logo``).  The templates automatically prefer the uploaded file and
fall back to the generated crest, so adding real artwork never requires a code
change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeamBrand:
    """Presentation metadata for a single franchise."""

    short_name: str
    primary_color: str
    secondary_color: str
    accent_color: str
    note: str = ""


# Colours follow the widely published primary/secondary kit colours for each
# franchise.  Historical sides that no longer compete are included because the
# dataset covers seasons back to 2008.
TEAM_BRANDS: dict[str, TeamBrand] = {
    "Chennai Super Kings": TeamBrand(
        "CSK", "#F9CD05", "#17419E", "#0B3C8C", "Yellow is the primary kit colour."
    ),
    "Deccan Chargers": TeamBrand(
        "DEC", "#0B4EA2", "#F7B500", "#08306B", "Defunct franchise (2008-2012)."
    ),
    "Delhi Capitals": TeamBrand(
        "DC", "#17479E", "#0A2240", "#E4262C", "Originally Delhi Daredevils."
    ),
    "Gujarat Lions": TeamBrand(
        "GL", "#1B3A6B", "#C8A951", "#0E2340", "Defunct franchise (2013 only)."
    ),
    "Gujarat Titans": TeamBrand(
        "GT", "#1D2A44", "#C7A24B", "#0E1626", "Added to the IPL for the 2022 season."
    ),
    "Kochi Tuskers Kerala": TeamBrand(
        "KTK", "#5B2C6F", "#C8A951", "#2A1038", "Defunct franchise (2011 only)."
    ),
    "Kolkata Knight Riders": TeamBrand(
        "KKR", "#3A225D", "#B8A24A", "#1A0F2E", "Gold trim on a deep purple base."
    ),
    "Lucknow Super Giants": TeamBrand(
        "LSG", "#0057E2", "#0B1F45", "#0A3FA8", "Added to the IPL for the 2022 season."
    ),
    "Mumbai Indians": TeamBrand(
        "MI", "#045093", "#0057E2", "#D4AF37", "Classic blue with gold accents."
    ),
    "Pune Warriors": TeamBrand(
        "PW", "#2B2B2B", "#C8283A", "#141414", "Defunct franchise (2011-2012)."
    ),
    "Punjab Kings": TeamBrand(
        "PBKS", "#D71920", "#8C1D2B", "#B01C2E", "Red with silver detailing."
    ),
    "Rajasthan Royals": TeamBrand(
        "RR", "#E6017F", "#1D1D6F", "#8E0B4F", "Pink with a navy base."
    ),
    "Rising Pune Supergiant": TeamBrand(
        "RPS", "#2C2C2C", "#E31837", "#141414", "Defunct under this name (2013-2018)."
    ),
    "Royal Challengers Bangalore": TeamBrand(
        "RCB", "#D81920", "#1A1A1A", "#9E1414", "Red and black."
    ),
    "Sunrisers Hyderabad": TeamBrand(
        "SRH", "#F26522", "#1B1B1B", "#B8480F", "Orange and black."
    ),
}

# Used when a team is missing from the table (for example a newly added side).
FALLBACK_BRAND = TeamBrand("", "#4D8DFF", "#8257FF", "#2B5FCC")

# Secondary colours are resolved relative to this luminance so the crest ring
# stays legible on both very light (CSK yellow) and very dark (KKR purple) bases.
_LIGHT_THRESHOLD = 0.62


def _relative_luminance(hex_colour: str) -> float:
    """Return the perceived brightness of a ``#rrggbb`` colour in ``0..1``."""

    value = hex_colour.lstrip("#")
    if len(value) != 6:
        return 0.0
    try:
        red, green, blue = (
            int(value[index : index + 2], 16) / 255 for index in (0, 2, 4)
        )
    except ValueError:
        return 0.0

    def channel(component: float) -> float:
        return component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def brand_for(name: str) -> TeamBrand:
    """Return the brand entry for ``name`` or a neutral fallback."""

    return TEAM_BRANDS.get(name, FALLBACK_BRAND)


def text_colour_for(background: str) -> str:
    """Pick black or white text depending on how bright ``background`` is.

    Chennai Super Kings' yellow crest is far too light for white text, which is
    exactly the kind of contrast bug that makes generated avatars unreadable.
    """

    return "#111827" if _relative_luminance(background) > _LIGHT_THRESHOLD else "#FFFFFF"


def monogram_for(short_name: str, full_name: str, limit: int = 4) -> str:
    """Return an uppercase monogram for a crest.

    Prefers an explicit short code (``MI``, ``RCB``, ``PBKS``).  Otherwise the
    initials of the first few significant words are used, so "Rising Pune
    Supergiant" becomes ``RPS`` rather than ``RP``.
    """

    code = "".join(character for character in (short_name or "") if character.isalnum())
    if code:
        return code[:limit].upper()
    words = [word for word in (full_name or "").split() if word[:1].isalpha()]
    if not words:
        return "?"
    return "".join(word[0] for word in words[:limit]).upper()
