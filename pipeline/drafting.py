"""The request step: Claude turns a plain-English request into a draft spec.

Claude only (1) parses the request into countries, years and variables and
(2) picks indicator codes from the source catalogs. Python then checks every
code exists, copies each unit, price basis, base year, currency and official
definition from the catalog, takes comparison thresholds from config.yaml,
and rejects any number Claude wrote that is not in the request itself or in
the chosen indicator's own catalog wording. Anything Python cannot fill in
from the catalog is left as FILL_ME for you to decide.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import yaml

from .catalog import search
from .config import Config
from .connectors import IndicatorInfo, Sources, UnknownCode
from .sectors import AGGREGATES, ISIC4_SECTIONS
from .spec import SPEC_VERSION, SpecError, parse_spec

FILL_ME = "FILL_ME"
SEARCHABLE = ("wdi", "pwt")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_ROUNDS = 25
SEARCH_LIMIT = 15
DEFINITION_PREVIEW = 300

SYSTEM_PROMPT = """\
You help an economist who studies productivity and growth in Central Asia (sectoral value added, \
employment, investment, capital stocks) turn a plain-English data request into a draft spec for a \
data pipeline. The economist reviews and confirms every draft; deterministic code then downloads \
and processes the data.

Your job is only to (1) parse the request into countries, years and a list of variables, and \
(2) map each variable to indicator codes from the source catalogs. You never write data values, \
thresholds, units or base years: the pipeline copies units, price bases, base years, currencies \
and official definitions from the catalogs itself, and it rejects any number in your submission \
that does not appear in the request or in the chosen indicator's catalog name.

Tools: search_indicators (keyword search of one catalog), get_indicator (the full catalog entry \
for one code), and submit_spec (your draft; the pipeline checks it and replies "accepted" or with \
problems to fix).

Sources you can search now:
- wdi: World Bank World Development Indicators.
- pwt: Penn World Table 11.0 (real GDP at constant 2021 national prices, capital stock, labour \
share, persons engaged, hours, human capital, TFP and more).
ILOSTAT, IMF, FAOSTAT and national statistics offices are not searchable yet. If the request \
needs them (for example employment counts by sector), use the closest WDI or PWT series and say \
so in open_questions.

Guidance:
- Use only codes that appeared in your search or get results. Try several phrasings if needed, \
and read definitions: choose the code whose definition, unit and price basis match the request \
("real" means constant prices).
- List a variable's sources in order of preference. Add a fallback source only for the same \
concept in the same unit, because the pipeline does not allow mixing units. When two sources \
cover the same concept in different units or base years (for example GDP in WDI and in PWT), \
make two variables and add a comparison: "growth" when units or base years differ, "level" only \
when units are identical.
- Sectors: AGR (ISIC A), IND (ISIC B-F), MFG (ISIC C), SER (ISIC G-U), or single ISIC sections \
A to U. sector_classification is isic4_3sector (AGR, IND, SER), isic4_3sector_mfg (also MFG) or \
isic4_sections (sections A-U plus the aggregates). "Broad sectors" means isic4_3sector unless the \
request mentions manufacturing. Use sector null for economy-wide variables, and \
sector_classification null when no variable has a sector.
- measure: level (amounts), share (a fraction from 0 to 1), percent (values in percent, such as \
% of GDP), growth_rate (annual % change), or index.
- Variable names: lowercase letters, digits and underscores, starting with a letter, at most 32 \
characters, and not iso3, country or year. Labels: at most 80 characters, worded from the \
indicator name. The spec name becomes a folder name, so use the same rules.
- Countries: ISO 3166 alpha-3 codes. Years: copy the start and end years from the request; use \
null for any year the request does not state.
- Do not add transformations (interpolation, extrapolation, splicing, rebasing, unit scaling). If \
the request asks for one, describe it in open_questions so the economist can add it to the spec.
- alternatives: for each variable, up to three other codes worth considering, each with a short \
reason.
- open_questions: decisions the economist should make, one sentence each. Leave out anything you \
resolved confidently.
When the draft is ready, call submit_spec. If problems come back, fix them and submit again.
"""

_SOURCE_ENUM = {"type": "string", "enum": list(SEARCHABLE)}
_SECTOR_CODES = sorted(AGGREGATES) + sorted(ISIC4_SECTIONS)


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SUBMIT_SCHEMA = _object({
    "name": {"type": "string", "description": "Spec name, used as the output folder name."},
    "title": {"type": "string"},
    "countries": {"type": "array", "items": {"type": "string"}, "description": "ISO 3166 alpha-3 codes."},
    "year_start": _nullable({"type": "integer"}),
    "year_end": _nullable({"type": "integer"}),
    "sector_classification": _nullable(
        {"type": "string", "enum": ["isic4_3sector", "isic4_3sector_mfg", "isic4_sections"]}),
    "source_choice": {"type": "string", "enum": ["same_for_all", "per_country"]},
    "variables": {"type": "array", "items": _object({
        "name": {"type": "string"},
        "label": {"type": "string"},
        "sector": _nullable({"type": "string", "enum": _SECTOR_CODES}),
        "measure": {"type": "string", "enum": ["level", "share", "percent", "growth_rate", "index"]},
        "sources": {"type": "array", "items": _object({"source": _SOURCE_ENUM, "code": {"type": "string"}}),
                    "description": "Preferred source first, then same-unit fallbacks."},
        "alternatives": {"type": "array", "items": _object(
            {"source": _SOURCE_ENUM, "code": {"type": "string"}, "why": {"type": "string"}})},
        "note": _nullable({"type": "string"}),
    })},
    "comparisons": {"type": "array", "items": _object({
        "a": {"type": "string"}, "b": {"type": "string"},
        "compare": {"type": "string", "enum": ["growth", "level"]},
    })},
    "open_questions": {"type": "array", "items": {"type": "string"}},
})

TOOLS = [
    {
        "name": "search_indicators",
        "description": "Keyword search of one source's indicator catalog. Returns up to 15 matches with code, "
                       "name, unit, price basis, base year and the start of the official definition.",
        "strict": True,
        "input_schema": _object({"source": _SOURCE_ENUM, "query": {"type": "string"}}),
    },
    {
        "name": "get_indicator",
        "description": "The full catalog entry for one code: name, unit and the complete official definition.",
        "strict": True,
        "input_schema": _object({"source": _SOURCE_ENUM, "code": {"type": "string"}}),
    },
    {
        "name": "submit_spec",
        "description": "Submit the draft spec. Returns 'accepted', or a list of problems to fix and resubmit.",
        "strict": True,
        "input_schema": SUBMIT_SCHEMA,
    },
]


class DraftError(RuntimeError):
    pass


class DraftRejected(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class DraftResult:
    spec: dict[str, Any]
    to_fill: list[str]
    open_questions: list[str]
    review: list[dict[str, Any]]
    searches: list[str] = field(default_factory=list)
    model: str = ""
    tokens: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------ number guard

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(text: str | None) -> set[str]:
    return set(_NUMBER.findall(text or ""))


def _check_numbers(text: str | None, allowed: set[str], where: str, problems: list[str]) -> None:
    extra = sorted(numbers_in(text) - allowed)
    if extra:
        problems.append(f"{where} contains {', '.join(extra)}, which is not in the request or the indicator's "
                        "catalog name; remove it (the pipeline fills numbers from the catalog)")


# ---------------------------------------------------------- tool handlers


def _brief(info: IndicatorInfo, full: bool = False) -> dict[str, Any]:
    definition = info.definition if full else info.definition[:DEFINITION_PREVIEW]
    out = {"code": info.code, "name": info.name, "unit": info.unit.text, "price_basis": info.unit.price_basis,
           "base_year": info.unit.base_year, "currency": info.unit.currency, "definition": definition}
    if full and info.details:
        out["details"] = info.details
    return out


def run_search(sources: Sources, source: str, query: str) -> str:
    if source not in SEARCHABLE:
        raise ValueError(f"'{source}' cannot be searched yet; use one of {', '.join(SEARCHABLE)}")
    hits = search(sources.get(source).catalog().values(), query, SEARCH_LIMIT)
    return json.dumps({"source": source, "query": query, "results": [_brief(h) for h in hits]}, ensure_ascii=False)


def run_get(sources: Sources, source: str, code: str) -> str:
    if source not in SEARCHABLE:
        raise ValueError(f"'{source}' cannot be searched yet; use one of {', '.join(SEARCHABLE)}")
    return json.dumps(_brief(sources.get(source).info(code), full=True), ensure_ascii=False)


# ---------------------------------------------------------------- assembly


def _source_entry(info: IndicatorInfo, to_fill: list[str], where: str) -> dict[str, Any]:
    unit = info.unit
    entry: dict[str, Any] = {"source": info.source}
    if info.source == "pwt":
        entry["dataset"] = "11.0"
    entry["code"] = info.code
    entry["unit"] = unit.text or FILL_ME
    if unit.currency is not None:
        entry["currency"] = unit.currency
    entry["price_basis"] = unit.price_basis or FILL_ME
    if unit.base_year is not None:
        entry["base_year"] = unit.base_year
    elif unit.price_basis == "constant":
        entry["base_year"] = FILL_ME
    entry["definition"] = info.definition or info.name
    for key in ("unit", "price_basis", "base_year"):
        if entry.get(key) == FILL_ME:
            to_fill.append(f"{where} -> {key} (the catalog does not state it clearly)")
    return entry


def _validation_copy(spec: dict[str, Any]) -> dict[str, Any]:
    """Swap FILL_ME placeholders for neutral stand-ins so everything else can be checked now."""
    data = copy.deepcopy(spec)
    years = data["years"]
    known = [y for y in (years["start"], years["end"]) if y != FILL_ME]
    for k in ("start", "end"):
        if years[k] == FILL_ME:
            years[k] = known[0] if known else 2000
    for v in data["variables"]:
        for s in v["sources"]:
            if s.get("unit") == FILL_ME:
                s["unit"] = "unit"
            if s.get("base_year") == FILL_ME:
                s["base_year"] = 2000
            if s.get("price_basis") == FILL_ME:
                s["price_basis"] = ("constant" if "base_year" in s else "current" if s.get("currency")
                                    else "not_applicable")
    return data


def assemble(submission: dict[str, Any], request: str, sources: Sources, config: Config) -> DraftResult:
    problems: list[str] = []
    to_fill: list[str] = []
    request_numbers = numbers_in(request)

    _check_numbers(submission["name"], request_numbers, "the spec name", problems)
    _check_numbers(submission["title"], request_numbers, "the title", problems)
    years: dict[str, Any] = {}
    for key, value in (("start", submission["year_start"]), ("end", submission["year_end"])):
        if value is None:
            years[key] = FILL_ME
            to_fill.append(f"years -> {key} (the request does not state it)")
        elif str(value) not in request_numbers:
            problems.append(f"year_{key} {value} does not appear in the request; use null if the request gives no year")
            years[key] = value
        else:
            years[key] = value

    variables, review = [], []
    for i, var in enumerate(submission["variables"], start=1):
        tag = f"variable #{i} ({var['name']})"
        entries, chosen, catalog_numbers = [], [], set()
        for j, src in enumerate(var["sources"], start=1):
            try:
                info = sources.get(src["source"]).info(src["code"])
            except (UnknownCode, ValueError) as exc:
                problems.append(f"{tag}: {exc}; search the catalog and use a code from the results")
                continue
            catalog_numbers |= numbers_in(info.name)
            chosen.append(info)
            entries.append(_source_entry(info, to_fill, f"variables -> {var['name']} -> sources -> "
                                                        f"#{j} ({info.source} {info.code})"))
        if not var["sources"]:
            problems.append(f"{tag}: needs at least one source")
        alternatives = []
        for alt in var["alternatives"]:
            try:
                alt_info = sources.get(alt["source"]).info(alt["code"])
            except (UnknownCode, ValueError) as exc:
                problems.append(f"{tag}: alternative {exc}")
                continue
            alternatives.append((alt_info, alt["why"]))
        allowed = request_numbers | catalog_numbers
        for key in ("name", "label", "note"):
            _check_numbers(var[key], allowed, f"{tag} {key}", problems)
        entry: dict[str, Any] = {"name": var["name"], "label": var["label"]}
        if var["sector"] is not None:
            entry["sector"] = var["sector"]
        entry["measure"] = var["measure"]
        entry["sources"] = entries
        if var["note"]:
            entry["note"] = var["note"]
        variables.append(entry)
        review.append({"name": var["name"], "label": var["label"], "chosen": chosen, "alternatives": alternatives})

    thresholds = config.checks.comparison_threshold
    comparisons = [{"a": c["a"], "b": c["b"], "compare": c["compare"],
                    "threshold": getattr(thresholds, c["compare"])} for c in submission["comparisons"]]

    spec: dict[str, Any] = {
        "spec_version": SPEC_VERSION,
        "name": submission["name"],
        "title": submission["title"],
        "request": request,
        "confirmed": False,
        "countries": submission["countries"],
        "years": years,
    }
    if submission["sector_classification"] is not None:
        spec["sector_classification"] = submission["sector_classification"]
    spec["source_choice"] = submission["source_choice"]
    spec["variables"] = variables
    if comparisons:
        spec["comparisons"] = comparisons

    if not problems:
        try:
            parse_spec(_validation_copy(spec), where="the draft")
        except SpecError as exc:
            problems.extend(exc.problems)
    if problems:
        raise DraftRejected(problems)
    return DraftResult(spec=spec, to_fill=to_fill, open_questions=list(submission["open_questions"]), review=review)


# -------------------------------------------------------------- the loop


def _blocks(response: Any, kind: str) -> list[Any]:
    return [b for b in response.content if b.type == kind]


def draft_spec(request: str, *, client: Any, config: Config, sources: Sources,
               progress: Callable[[str], None] = print, max_rounds: int = MAX_ROUNDS) -> DraftResult:
    model = config.models.spec_drafting
    messages: list[dict[str, Any]] = [{"role": "user", "content": f"Request: {request}"}]
    searches: list[str] = []
    tokens = {"input": 0, "output": 0}

    for _ in range(max_rounds):
        response = client.beta.messages.create(
            model=model,
            max_tokens=config.models.max_tokens,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": config.models.effort},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        tokens["input"] += getattr(response.usage, "input_tokens", 0) or 0
        tokens["output"] += getattr(response.usage, "output_tokens", 0) or 0
        if response.stop_reason == "refusal":
            raise DraftError("Claude declined to draft a spec for this request.")
        if response.stop_reason == "max_tokens":
            raise DraftError("Claude's reply was cut off (max_tokens in config.yaml is too small).")
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = _blocks(response, "tool_use")
        if not tool_uses:
            messages.append({"role": "user", "content": "Please submit the draft with the submit_spec tool."})
            continue

        results, accepted = [], None
        for use in tool_uses:
            is_error = False
            try:
                if use.name == "search_indicators":
                    searches.append(f"{use.input['source']}: {use.input['query']}")
                    progress(f"  searching {use.input['source']} for '{use.input['query']}'")
                    content = run_search(sources, use.input["source"], use.input["query"])
                elif use.name == "get_indicator":
                    progress(f"  reading {use.input['source']} {use.input['code']}")
                    content = run_get(sources, use.input["source"], use.input["code"])
                elif use.name == "submit_spec":
                    accepted = assemble(use.input, request, sources, config)
                    content = "accepted"
                    progress("  draft accepted")
                else:
                    raise ValueError(f"unknown tool {use.name}")
            except DraftRejected as exc:
                is_error = True
                content = "Please fix these problems and submit again:\n- " + "\n- ".join(exc.problems)
                progress(f"  draft sent back with {len(exc.problems)} problem(s) to fix")
            except (UnknownCode, ValueError, KeyError) as exc:
                is_error = True
                content = f"Error: {exc}"
            results.append({"type": "tool_result", "tool_use_id": use.id, "content": content, "is_error": is_error})
        if accepted is not None:
            accepted.searches = searches
            accepted.model = model
            accepted.tokens = tokens
            return accepted
        messages.append({"role": "user", "content": results})

    raise DraftError(f"No acceptable draft after {max_rounds} rounds; try rewording the request.")


# ------------------------------------------------------------ writing out


def draft_yaml(result: DraftResult, review_file: str, drafted_on: str) -> str:
    lines = [
        f"# DRAFT spec, written by {result.model} on {drafted_on} from the request below.",
        "# Nothing has been downloaded yet. Before building:",
        f"#   1. Check each code and its official definition (alternatives are in {review_file}).",
    ]
    step = 2
    if result.to_fill:
        lines.append(f"#   {step}. Replace every {FILL_ME}:")
        lines += [f"#        - {item}" for item in result.to_fill]
        step += 1
    lines.append(f"#   {step}. Change confirmed: false to confirmed: true.")
    if result.open_questions:
        lines.append("# Questions from the drafting step:")
        lines += [f"#   - {q}" for q in result.open_questions]
    body = yaml.safe_dump(result.spec, sort_keys=False, allow_unicode=True, width=100)
    return "\n".join(lines) + "\n\n" + body


def review_markdown(result: DraftResult, spec_file: str, drafted_on: str) -> str:
    def describe(info: IndicatorInfo) -> list[str]:
        unit = info.unit
        basis = unit.price_basis or "unclear"
        year = f", base year {unit.base_year}" if unit.base_year else ""
        return [f"  - Unit: {unit.text or 'not stated'} ({basis}{year})",
                f"  - Definition: {info.definition or info.name}"]

    out = [f"# Review notes for {spec_file}", "",
           f"Drafted by {result.model} on {drafted_on}. Definitions are copied from the source catalogs.", ""]
    for item in result.review:
        out += [f"## {item['name']}", "", item["label"], "", "Chosen:"]
        for k, info in enumerate(item["chosen"]):
            role = "preferred" if k == 0 else f"fallback {k}"
            out.append(f"- **{info.source} {info.code}** ({role}): {info.name}")
            out += describe(info)
        if item["alternatives"]:
            out += ["", "Alternatives to consider:"]
            for info, why in item["alternatives"]:
                out.append(f"- **{info.source} {info.code}**: {info.name}. Why: {why}")
                out += describe(info)
        out.append("")
    if result.open_questions:
        out += ["## Questions", ""] + [f"- {q}" for q in result.open_questions] + [""]
    if result.searches:
        out += ["## Catalog searches made", ""] + [f"- {s}" for s in result.searches] + [""]
    return "\n".join(out)
