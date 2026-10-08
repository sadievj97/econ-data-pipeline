"""Sector codes a spec may use, based on ISIC Rev.4.

A spec picks one classification. Each variable's `sector` must come from the
codes that classification allows. Variables without a `sector` are economy-wide.
"""

ISIC4_SECTIONS: dict[str, str] = {
    "A": "Agriculture, forestry and fishing",
    "B": "Mining and quarrying",
    "C": "Manufacturing",
    "D": "Electricity, gas, steam and air conditioning supply",
    "E": "Water supply; sewerage, waste management and remediation",
    "F": "Construction",
    "G": "Wholesale and retail trade; repair of motor vehicles",
    "H": "Transportation and storage",
    "I": "Accommodation and food service activities",
    "J": "Information and communication",
    "K": "Financial and insurance activities",
    "L": "Real estate activities",
    "M": "Professional, scientific and technical activities",
    "N": "Administrative and support service activities",
    "O": "Public administration and defence; compulsory social security",
    "P": "Education",
    "Q": "Human health and social work activities",
    "R": "Arts, entertainment and recreation",
    "S": "Other service activities",
    "T": "Activities of households as employers",
    "U": "Activities of extraterritorial organizations and bodies",
}

# Broad aggregates and the ISIC Rev.4 sections each one covers.
AGGREGATES: dict[str, tuple[str, tuple[str, ...]]] = {
    "AGR": ("Agriculture (ISIC A)", ("A",)),
    "IND": ("Industry incl. construction (ISIC B-F)", ("B", "C", "D", "E", "F")),
    "MFG": ("Manufacturing (ISIC C)", ("C",)),
    "SER": ("Services (ISIC G-U)", tuple("GHIJKLMNOPQRSTU")),
}

CLASSIFICATIONS: dict[str, frozenset[str]] = {
    "isic4_3sector": frozenset({"AGR", "IND", "SER"}),
    "isic4_3sector_mfg": frozenset({"AGR", "IND", "MFG", "SER"}),
    "isic4_sections": frozenset(ISIC4_SECTIONS) | frozenset(AGGREGATES),
}


def sector_label(code: str) -> str:
    if code in AGGREGATES:
        return AGGREGATES[code][0]
    return f"{ISIC4_SECTIONS[code]} (ISIC {code})"
