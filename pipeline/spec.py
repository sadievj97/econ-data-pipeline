"""The spec: an exact, checkable description of the data to build.

The request step drafts a spec from a plain-English request; you confirm or
edit it; the build follows it to the letter. Checks are strict, so a typo or a
missing detail fails loudly instead of being silently ignored.

Transforms (interpolation, extrapolation, rebasing, splicing, scaling) happen
only when a variable lists them here, and each one is logged with its method.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

import pycountry
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .sectors import CLASSIFICATIONS, sector_label

SPEC_VERSION = 1

FIXED_SOURCES = ("wdi", "pwt", "ilostat", "imf", "faostat", "comtrade", "manual")
NATIONAL_SOURCE = re.compile(r"^nso_[a-z]{3}$")  # a national statistics office, e.g. nso_uzb

LOCAL_CURRENCY = "LCU"  # each country's own currency, as in WDI's "constant LCU"

# Codes some sources use that are not in ISO 3166-1.
EXTRA_COUNTRY_CODES = {"XKX": "Kosovo"}

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_NAME_LENGTH = 32  # Stata's limit for variable names
MAX_LABEL_LENGTH = 80  # Stata's limit for variable labels
KEY_COLUMNS = frozenset({"iso3", "country", "year"})
RESERVED_WORDS = frozenset(
    {"byte", "double", "float", "if", "in", "int", "long", "strl", "using", "with"}  # Stata
    | {"break", "else", "for", "function", "next", "repeat", "while"}  # R
)
STATA_STR_TYPE = re.compile(r"^str\d+$")

PriceBasis = Literal["current", "constant", "not_applicable"]
Measure = Literal["level", "share", "percent", "growth_rate", "index"]
ScaleUnit = Literal["units", "thousands", "millions", "billions", "percent", "fraction"]
COUNT_SCALES = frozenset({"units", "thousands", "millions", "billions"})


def country_name(iso3: str) -> str | None:
    if iso3 in EXTRA_COUNTRY_CODES:
        return EXTRA_COUNTRY_CODES[iso3]
    match = pycountry.countries.get(alpha_3=iso3)
    return match.name if match else None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------- sources


class SourceRef(_Strict):
    """Where one series comes from, and the units it is expected to arrive in.

    The build compares these expectations with the source's own metadata and
    stops if they disagree.
    """

    source: str
    code: str = Field(min_length=1)
    dataset: str | None = None
    filters: dict[str, str] = Field(default_factory=dict)
    unit: str = Field(min_length=1)
    currency: str | None = None
    price_basis: PriceBasis
    base_year: int | None = Field(None, ge=1900, le=2100)
    definition: str | None = None
    file: str | None = None
    note: str | None = None

    @field_validator("source")
    @classmethod
    def _known_source(cls, v: str) -> str:
        if v in FIXED_SOURCES or NATIONAL_SOURCE.match(v):
            return v
        raise ValueError(
            f"unknown source '{v}'; use one of {', '.join(FIXED_SOURCES)}, "
            "or nso_<country code> for a national statistics office (e.g. nso_uzb)"
        )

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.upper()
        if v != LOCAL_CURRENCY and pycountry.currencies.get(alpha_3=v) is None:
            raise ValueError(
                f"'{v}' is not an ISO 4217 currency code (for example USD, UZS, KZT), "
                f"or {LOCAL_CURRENCY} for each country's own currency"
            )
        return v

    @model_validator(mode="after")
    def _consistent(self) -> SourceRef:
        if self.price_basis == "constant" and self.base_year is None:
            raise ValueError("a constant-price series needs a base_year")
        if self.price_basis == "current" and self.base_year is not None:
            raise ValueError("a current-price series has no base_year; remove it or use price_basis: constant")
        if self.price_basis == "not_applicable" and self.currency is not None:
            raise ValueError("a series with a currency needs price_basis: current or constant")
        if self.source == "manual":
            if not self.file:
                raise ValueError("a manual source needs file: (a path inside data/manual/)")
            p = PurePosixPath(self.file.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts or ":" in self.file:
                raise ValueError("file: must be a path inside data/manual/, such as uzstat/gdp_2024.xlsx")
        elif self.file:
            raise ValueError("file: is only used with source: manual")
        return self


# ------------------------------------------------------------- transforms


class _Transform(_Strict):
    note: str | None = None


class Interpolate(_Transform):
    """Fill interior gaps of at most max_gap consecutive missing years."""

    kind: Literal["interpolate"]
    method: Literal["linear", "log_linear"]
    max_gap: int = Field(ge=1)


class Extrapolate(_Transform):
    """Extend the series beyond its ends using another variable's growth rates."""

    kind: Literal["extrapolate"]
    method: Literal["proxy_growth"]
    proxy: str
    direction: Literal["backward", "forward", "both"]


class Rebase(_Transform):
    """index: value / value[year] * 100.
    shift_reference_year: rescale a constant-price series so it equals the
    current-price series in `year` (needs current_price_variable)."""

    kind: Literal["rebase"]
    method: Literal["index", "shift_reference_year"]
    year: int = Field(ge=1900, le=2100)
    current_price_variable: str | None = None

    @model_validator(mode="after")
    def _needs_partner(self) -> Rebase:
        if self.method == "shift_reference_year" and not self.current_price_variable:
            raise ValueError("shift_reference_year needs current_price_variable")
        if self.method == "index" and self.current_price_variable:
            raise ValueError("current_price_variable is only used with shift_reference_year")
        return self


class Splice(_Transform):
    """Join an older series from with_source onto the preferred series,
    scaled by the ratio of the two in link_year."""

    kind: Literal["splice"]
    method: Literal["ratio_at_link_year"]
    with_source: str
    link_year: int = Field(ge=1900, le=2100)


class Scale(_Transform):
    """Change the scale only, e.g. thousands -> units, or percent -> fraction."""

    kind: Literal["scale"]
    from_unit: ScaleUnit
    to_unit: ScaleUnit

    @model_validator(mode="after")
    def _compatible(self) -> Scale:
        if self.from_unit == self.to_unit:
            raise ValueError("from_unit and to_unit are the same")
        if (self.from_unit in COUNT_SCALES) != (self.to_unit in COUNT_SCALES):
            raise ValueError("can only scale between units/thousands/millions/billions, or between percent/fraction")
        return self


Transform = Annotated[Interpolate | Extrapolate | Rebase | Splice | Scale, Field(discriminator="kind")]


# -------------------------------------------------------------- variables


class Variable(_Strict):
    """One column of the panel. The first source listed is preferred; the rest
    are fallbacks, used only as the spec's source_choice describes."""

    name: str
    label: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    sector: str | None = None
    measure: Measure
    allow_negative: bool = False
    valid_range: tuple[float, float] | None = None
    sources: list[SourceRef] = Field(min_length=1)
    transforms: list[Transform] = Field(default_factory=list)
    note: str | None = None

    @field_validator("name")
    @classmethod
    def _name_rules(cls, v: str) -> str:
        if not NAME_PATTERN.match(v):
            raise ValueError(f"'{v}' must start with a lowercase letter and use only lowercase letters, digits and _")
        if len(v) > MAX_NAME_LENGTH:
            raise ValueError(f"'{v}' is {len(v)} characters long; Stata allows at most {MAX_NAME_LENGTH}")
        if v in KEY_COLUMNS:
            raise ValueError(f"'{v}' is reserved for the panel's key columns")
        if v in RESERVED_WORDS or STATA_STR_TYPE.match(v):
            raise ValueError(f"'{v}' is a reserved word in Stata or R")
        return v

    @model_validator(mode="after")
    def _checks(self) -> Variable:
        if self.valid_range is not None and self.valid_range[0] >= self.valid_range[1]:
            raise ValueError("valid_range must be [lowest, highest] with lowest below highest")
        for t in self.transforms:
            if isinstance(t, Splice):
                matches = [s for s in self.sources if s.source == t.with_source]
                if len(matches) != 1:
                    raise ValueError(f"splice with_source '{t.with_source}' must name exactly one of this variable's sources")
                if self.sources[0].source == t.with_source:
                    raise ValueError("splice with_source must be a fallback source, not the preferred one")
        return self


# ------------------------------------------------------------------- spec


class YearRange(_Strict):
    start: int = Field(ge=1900, le=2100)
    end: int = Field(ge=1900, le=2100)

    @model_validator(mode="after")
    def _ordered(self) -> YearRange:
        if self.start > self.end:
            raise ValueError(f"start ({self.start}) is after end ({self.end})")
        return self


class Comparison(_Strict):
    """Report years where two variables disagree by more than threshold:
    percentage points of growth, or percent of level."""

    a: str
    b: str
    compare: Literal["growth", "level"]
    threshold: float = Field(gt=0)


class SectorAccounting(_Strict):
    """Growth accounting for one sector. Without capital, only labor productivity is reported."""

    sector: str
    output: str
    labor: str
    capital: str | None = None
    labor_share: str | float | None = None  # a variable or a number; default: the economy-wide labor_share


class GrowthAccounting(_Strict):
    """TFP growth as a Solow residual: g_A = g_Y - (1 - s) g_K - s g_L, with log growth
    rates and labor share s (a variable, averaged over adjacent years, or a number you choose)."""

    output: str
    capital: str
    labor: str
    hours: str | None = None  # average hours per worker: labor input = workers x hours
    human_capital: str | None = None  # human capital index: labor input x index
    labor_share: str | float
    sensitivity: list[float] = Field(default_factory=list)  # constant labor shares to compare
    periods: list[tuple[int, int]] = Field(default_factory=list)  # sub-periods, e.g. [[2000, 2008], [2009, 2023]]
    compare_with: str | None = None  # a TFP series (index or level) whose growth is compared with the residual
    sectors: list[SectorAccounting] = Field(default_factory=list)

    @field_validator("sensitivity")
    @classmethod
    def _shares(cls, v: list[float]) -> list[float]:
        bad = [s for s in v if not 0 < s < 1]
        if bad:
            raise ValueError(f"sensitivity labor shares must be between 0 and 1, not {bad}")
        return v

    def variables_used(self) -> list[tuple[str, str]]:
        """(role, variable name) for every variable this section refers to."""
        used = [("output", self.output), ("capital", self.capital), ("labor", self.labor)]
        used += [(role, name) for role, name in (("hours", self.hours), ("human_capital", self.human_capital),
                                                 ("compare_with", self.compare_with)) if name]
        if isinstance(self.labor_share, str):
            used.append(("labor_share", self.labor_share))
        for s in self.sectors:
            used += [(f"{s.sector} output", s.output), (f"{s.sector} labor", s.labor)]
            if s.capital:
                used.append((f"{s.sector} capital", s.capital))
            if isinstance(s.labor_share, str):
                used.append((f"{s.sector} labor_share", s.labor_share))
        return used


class Spec(_Strict):
    spec_version: Literal[1]
    name: str
    title: str = Field(min_length=1)
    request: str | None = None
    confirmed: bool = False
    countries: list[str] = Field(min_length=1)
    years: YearRange
    sector_classification: Literal["isic4_3sector", "isic4_3sector_mfg", "isic4_sections"] | None = None
    source_choice: Literal["same_for_all", "per_country"] = "same_for_all"
    variables: list[Variable] = Field(min_length=1)
    comparisons: list[Comparison] = Field(default_factory=list)
    growth_accounting: GrowthAccounting | None = None

    @field_validator("name")
    @classmethod
    def _spec_name(cls, v: str) -> str:
        if not NAME_PATTERN.match(v) or len(v) > 64:
            raise ValueError(
                f"'{v}' is used as the output folder name: start with a lowercase letter, "
                "use only lowercase letters, digits and _, at most 64 characters"
            )
        return v

    @field_validator("countries")
    @classmethod
    def _countries(cls, v: list[str]) -> list[str]:
        codes = [c.upper() for c in v]
        unknown = [c for c in codes if country_name(c) is None]
        if unknown:
            raise ValueError(f"not ISO 3166 alpha-3 country codes: {', '.join(unknown)} (for example UZB, KAZ)")
        repeated = sorted({c for c in codes if codes.count(c) > 1})
        if repeated:
            raise ValueError(f"listed more than once: {', '.join(repeated)}")
        return codes

    @model_validator(mode="after")
    def _cross_checks(self) -> Spec:
        problems: list[str] = []
        names = [v.name for v in self.variables]
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            problems.append(f"variable names used more than once: {', '.join(repeated)}")
        known = set(names)
        for v in self.variables:
            if v.sector is not None:
                if self.sector_classification is None:
                    problems.append(f"{v.name}: has sector {v.sector} but the spec has no sector_classification")
                elif v.sector not in CLASSIFICATIONS[self.sector_classification]:
                    allowed = ", ".join(sorted(CLASSIFICATIONS[self.sector_classification]))
                    problems.append(
                        f"{v.name}: sector '{v.sector}' is not part of {self.sector_classification} (allowed: {allowed})"
                    )
            for t in v.transforms:
                ref = getattr(t, "proxy", None) or getattr(t, "current_price_variable", None)
                if ref is None:
                    continue
                if ref == v.name:
                    problems.append(f"{v.name}: {t.kind} cannot refer to the variable itself")
                elif ref not in known:
                    problems.append(f"{v.name}: {t.kind} refers to '{ref}', which is not a variable in this spec")
        for i, c in enumerate(self.comparisons, start=1):
            for side in (c.a, c.b):
                if side not in known:
                    problems.append(f"comparison #{i}: '{side}' is not a variable in this spec")
            if c.a == c.b:
                problems.append(f"comparison #{i}: compares '{c.a}' with itself")
        problems += self._growth_problems(known)
        if problems:
            raise ValueError("\n".join(problems))
        return self


    def _growth_problems(self, known: set[str]) -> list[str]:
        ga = self.growth_accounting
        if ga is None:
            return []
        problems = []
        by_name = {v.name: v for v in self.variables}
        for role, name in ga.variables_used():
            if name not in known:
                problems.append(f"growth_accounting {role}: '{name}' is not a variable in this spec")
        for role, name in (("output", ga.output), ("capital", ga.capital)):
            v = by_name.get(name)
            if v is not None and any(s.price_basis != "constant" for s in v.sources):
                problems.append(f"growth_accounting {role}: '{name}' must be at constant prices")
        shares = [("labor_share", ga.labor_share)] + [(f"{s.sector} labor_share", s.labor_share) for s in ga.sectors]
        for role, share in shares:
            if isinstance(share, float) and not 0 < share < 1:
                problems.append(f"growth_accounting {role}: {share} must be between 0 and 1")
            if isinstance(share, str) and share in by_name and by_name[share].measure != "share":
                problems.append(f"growth_accounting {role}: '{share}' must have measure: share (a fraction from 0 to 1)")
        for start, end in ga.periods:
            if not (self.years.start <= start < end <= self.years.end):
                problems.append(f"growth_accounting period [{start}, {end}] must lie inside the spec's years "
                                f"and start before it ends")
        for s in ga.sectors:
            if self.sector_classification is None or s.sector not in CLASSIFICATIONS[self.sector_classification]:
                problems.append(f"growth_accounting sector '{s.sector}' is not part of the spec's sector_classification")
        return problems


# ------------------------------------------------------ loading and saving


class SpecError(Exception):
    def __init__(self, where: str, problems: list[str]):
        self.where = where
        self.problems = problems
        super().__init__(str(self))

    def __str__(self) -> str:
        n = len(self.problems)
        lines = [f"{self.where} has {n} problem{'' if n == 1 else 's'}:"]
        lines += [f"  {i}. {p}" for i, p in enumerate(self.problems, start=1)]
        return "\n".join(lines)


_FRIENDLY = {
    "extra_forbidden": "unknown field (check the spelling)",
    "missing": "this field is required",
}


def _location(loc: tuple[Any, ...], data: Any) -> str:
    """Turn ('variables', 2, 'sources', 0, 'base_year') into
    'variables -> #3 (va_ser_real) -> sources -> #1 (wdi) -> base_year'."""
    parts: list[str] = []
    node = data
    for key in loc:
        if isinstance(key, int):
            item = node[key] if isinstance(node, list) and key < len(node) else None
            tag = None
            if isinstance(item, dict):
                tag = item.get("name") or item.get("source") or item.get("kind")
            parts.append(f"#{key + 1}" + (f" ({tag})" if isinstance(tag, str) else ""))
            node = item
        else:
            parts.append(str(key))
            node = node.get(key) if isinstance(node, dict) else None
    return " -> ".join(parts)


def _friendly_errors(exc: ValidationError, data: Any) -> list[str]:
    problems: list[str] = []
    for err in exc.errors():
        msg = _FRIENDLY.get(err["type"], err["msg"]).removeprefix("Value error, ")
        where = _location(err["loc"], data)
        for line in msg.split("\n"):
            problems.append(f"{where}: {line}" if where else line)
    return problems


def parse_spec(data: Any, where: str = "spec") -> Spec:
    if not isinstance(data, dict):
        raise SpecError(where, ["the file should be a list of 'field: value' lines, like the example spec"])
    try:
        return Spec.model_validate(data)
    except ValidationError as exc:
        raise SpecError(where, _friendly_errors(exc, data)) from None


def load_spec(path: str | Path) -> Spec:
    path = Path(path)
    if not path.is_file():
        raise SpecError(str(path), ["file not found"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        at = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        reason = getattr(exc, "problem", None) or str(exc)
        raise SpecError(path.name, [f"{at}not valid YAML ({reason})"]) from None
    return parse_spec(data, where=path.name)


def dump_spec(spec: Spec) -> str:
    data = spec.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


# ------------------------------------------------------------- summaries


def _describe_source(s: SourceRef) -> str:
    if s.price_basis == "constant":
        prices = f"constant prices, base {s.base_year}"
    elif s.price_basis == "current":
        prices = "current prices"
    else:
        prices = "no price basis"
    where = f" {s.dataset}" if s.dataset else ""
    extra = f" file {s.file}" if s.file else ""
    return f"{s.source}{where} {s.code}{extra} | {s.unit} | {prices}"


def describe_spec(spec: Spec) -> str:
    status = "CONFIRMED" if spec.confirmed else "DRAFT - not confirmed yet"
    countries = ", ".join(f"{c} ({country_name(c)})" for c in spec.countries)
    n_years = spec.years.end - spec.years.start + 1
    lines = [
        f"Spec: {spec.name}  ({status})",
        f"Title: {spec.title}",
        f"Countries: {countries}",
        f"Years: {spec.years.start}-{spec.years.end} ({n_years} years)",
    ]
    if spec.sector_classification:
        codes = ", ".join(sorted(CLASSIFICATIONS[spec.sector_classification]))
        lines.append(f"Sectors: {spec.sector_classification} ({codes})")
    choice = {
        "same_for_all": "each variable comes from one source for every country",
        "per_country": "each country may use a different source (units must still match)",
    }[spec.source_choice]
    lines.append(f"Source choice: {spec.source_choice} - {choice}")
    lines.append("")
    lines.append(f"Variables ({len(spec.variables)}):")
    for v in spec.variables:
        scope = sector_label(v.sector) if v.sector else "economy-wide"
        lines.append(f"  {v.name}  [{scope}, {v.measure}]")
        lines.append(f"      {v.label}")
        for i, s in enumerate(v.sources):
            role = "preferred" if i == 0 else f"fallback {i}"
            lines.append(f"      {role}: {_describe_source(s)}")
        if v.transforms:
            for t in v.transforms:
                method = getattr(t, "method", None) or f"{t.from_unit} -> {t.to_unit}"
                lines.append(f"      transform: {t.kind} ({method})")
        else:
            lines.append("      transforms: none")
    if spec.comparisons:
        lines.append("")
        lines.append(f"Comparisons ({len(spec.comparisons)}):")
        for c in spec.comparisons:
            unit = "percentage points" if c.compare == "growth" else "percent"
            lines.append(f"  {c.a} vs {c.b}: {c.compare}, flag differences above {c.threshold:g} ({unit})")
    return "\n".join(lines)
