"""Read the unit, price basis, base year and currency from a source's own wording,
and compare them with what a spec expects.

Only clear, recognised wording is interpreted. Anything else is left as
unknown (None) and reported, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .spec import LOCAL_CURRENCY, SourceRef


@dataclass(frozen=True)
class UnitInfo:
    text: str  # the unit exactly as the source words it
    price_basis: str | None  # current, constant, not_applicable, or None when unclear
    base_year: int | None
    currency: str | None  # ISO 4217 code, "LCU" (each country's own currency), or None


_WDI_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"^constant (\d{4}) US\$$"), "constant", "USD"),
    (re.compile(r"^current US\$$"), "current", "USD"),
    (re.compile(r"^constant LCU$"), "constant", "LCU"),
    (re.compile(r"^current LCU$"), "current", "LCU"),
    (re.compile(r"^constant (\d{4}) international \$$"), "constant", None),
    (re.compile(r"^current international \$$"), "current", None),
    (re.compile(r"%"), "not_applicable", None),
]


def from_wdi_name(name: str) -> UnitInfo:
    """WDI puts the unit in parentheses in the indicator name, e.g.
    'GDP (constant 2015 US$)' or 'Employment in agriculture (% of total employment) (modeled ILO estimate)'."""
    groups = re.findall(r"\(([^()]*)\)", name)
    for text in reversed(groups):
        for pattern, basis, currency in _WDI_PATTERNS:
            m = pattern.search(text)
            if m:
                year = int(m.group(1)) if m.groups() else None
                return UnitInfo(text.strip(), basis, year, currency)
    return UnitInfo(groups[-1].strip() if groups else "", None, None, None)


def from_pwt_label(label: str) -> UnitInfo:
    """PWT states units inside its variable labels, e.g.
    'Real GDP at constant 2021 national prices (in mil. 2021US$)'."""
    money = re.search(r"\(in mil\. (\d{4}) ?US\$\)", label)
    money_text = f"millions of {money.group(1)} US$" if money else ""
    index = re.search(r"\((\d{4})=1\)", label)
    m = re.search(r"at constant (\d{4}) national prices", label)
    if m:
        text = money_text or (f"index, {index.group(1)}=1" if index else "")
        return UnitInfo(text, "constant", int(m.group(1)), "USD" if money else None)
    if "at chained PPPs" in label and money:
        return UnitInfo(money_text, "constant", int(money.group(1)), "USD")
    if "at current national prices" in label:
        return UnitInfo("share" if label.startswith("Share of") else "", "current", None, None)
    if "(in millions)" in label:
        return UnitInfo("millions", "not_applicable", None, None)
    if index:
        return UnitInfo(f"index, {index.group(1)}=1", "not_applicable", int(index.group(1)), None)
    if "(USA=1)" in label:
        return UnitInfo("index, USA=1", "not_applicable", None, None)
    return UnitInfo(money_text, None, None, None)


def compare(ref: SourceRef, unit: UnitInfo) -> tuple[list[str], list[str]]:
    """Return (errors, warnings): errors are definite disagreements between the
    spec and the source; warnings are things the source does not state clearly."""
    errors: list[str] = []
    warnings: list[str] = []
    wording = f"'{unit.text}'" if unit.text else "its wording"

    if unit.price_basis is None:
        warnings.append(f"the source's unit ({wording}) does not state a price basis clearly; "
                        f"check price_basis: {ref.price_basis} by hand")
    elif unit.price_basis != ref.price_basis:
        errors.append(f"the spec says price_basis {ref.price_basis}, but the source says {unit.price_basis} ({wording})")

    if unit.base_year is not None and ref.base_year != unit.base_year:
        errors.append(f"the spec says base_year {ref.base_year}, but the source says {unit.base_year} ({wording})")
    elif unit.base_year is None and ref.base_year is not None and unit.price_basis == "constant":
        warnings.append(f"the source does not state one base year ({wording}); check base_year {ref.base_year} by hand")

    if unit.currency == LOCAL_CURRENCY:
        if ref.currency is None:
            errors.append(f"the source is in local currency units ({wording}), but the spec gives no currency")
        elif ref.currency != LOCAL_CURRENCY:
            warnings.append(f"the source is in each country's own currency ({wording}); the spec says "
                            f"{ref.currency}, which only fits a single country (use {LOCAL_CURRENCY} otherwise)")
    elif unit.currency is not None and ref.currency != unit.currency:
        errors.append(f"the spec says currency {ref.currency}, but the source says {unit.currency} ({wording})")
    return errors, warnings
