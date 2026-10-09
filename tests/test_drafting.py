"""The request step, with a scripted stand-in for Claude (no key, no cost)."""

import copy
from types import SimpleNamespace

import pytest
import yaml

from pipeline.config import load_config
from pipeline.drafting import (FALLBACK_BETA, FILL_ME, TOOLS, DraftError, DraftRejected, assemble, draft_spec,
                               draft_yaml, review_markdown)
from pipeline.spec import SpecError, parse_spec

REQUEST = "Uzbekistan and Kazakhstan, 2000-2023: real value added in agriculture, GDP from WDI and PWT, labor share"

GOOD = {
    "name": "uzb_kaz_test",
    "title": "Uzbekistan and Kazakhstan, 2000-2023",
    "countries": ["UZB", "KAZ"],
    "year_start": 2000,
    "year_end": 2023,
    "sector_classification": "isic4_3sector",
    "source_choice": "same_for_all",
    "variables": [
        {"name": "va_agr_real", "label": "Value added, agriculture (constant 2015 US$)", "sector": "AGR",
         "measure": "level", "sources": [{"source": "wdi", "code": "NV.AGR.TOTL.KD"}],
         "alternatives": [{"source": "wdi", "code": "NV.AGR.TOTL.KN", "why": "local currency"}], "note": None},
        {"name": "gdp_real_wdi", "label": "GDP (constant 2015 US$)", "sector": None, "measure": "level",
         "sources": [{"source": "wdi", "code": "NY.GDP.MKTP.KD"}], "alternatives": [], "note": None},
        {"name": "gdp_real_pwt", "label": "Real GDP (mil. 2021 US$), PWT", "sector": None, "measure": "level",
         "sources": [{"source": "pwt", "code": "rgdpna"}], "alternatives": [], "note": None},
        {"name": "labor_share", "label": "Labour share of GDP", "sector": None, "measure": "share",
         "sources": [{"source": "pwt", "code": "labsh"}], "alternatives": [], "note": None},
    ],
    "comparisons": [{"a": "gdp_real_wdi", "b": "gdp_real_pwt", "compare": "growth"}],
    "open_questions": ["Employment counts by sector need ILOSTAT."],
}


@pytest.fixture
def config():
    return load_config()


def submission(**changes):
    s = copy.deepcopy(GOOD)
    s.update(changes)
    return s


def test_good_submission_is_filled_from_the_catalog(sources, config):
    result = assemble(submission(), REQUEST, sources, config)
    spec = result.spec
    assert spec["confirmed"] is False and spec["request"] == REQUEST
    agr = spec["variables"][0]["sources"][0]
    assert (agr["unit"], agr["price_basis"], agr["base_year"], agr["currency"]) == ("constant 2015 US$", "constant", 2015, "USD")
    assert agr["definition"].startswith("Agriculture, forestry, and fishing")
    pwt = spec["variables"][2]["sources"][0]
    assert (pwt["dataset"], pwt["base_year"]) == ("11.0", 2021)
    assert spec["comparisons"][0]["threshold"] == config.checks.comparison_threshold.growth
    assert result.to_fill == []
    parse_spec(spec)  # a complete, valid spec


def test_year_not_in_the_request_is_rejected(sources, config):
    with pytest.raises(DraftRejected, match="year_end 2024 does not appear in the request"):
        assemble(submission(year_end=2024), REQUEST, sources, config)


def test_missing_year_becomes_fill_me(sources, config):
    result = assemble(submission(year_end=None, title="Uzbekistan since 2000"), "Uzbekistan since 2000: GDP",
                      sources, config)
    assert result.spec["years"]["end"] == FILL_ME
    assert "years -> end (the request does not state it)" in result.to_fill
    with pytest.raises(SpecError):
        parse_spec(result.spec)  # check-spec fails until the user fills it in


def test_numbers_must_come_from_the_request_or_the_catalog(sources, config):
    bad = submission()
    bad["variables"][1]["label"] = "GDP (constant 2017 US$)"
    with pytest.raises(DraftRejected, match="contains 2017"):
        assemble(bad, REQUEST, sources, config)


def test_unknown_codes_are_sent_back(sources, config):
    bad = submission()
    bad["variables"][0]["sources"][0]["code"] = "NV.AGR.MADEUP"
    with pytest.raises(DraftRejected, match="NV.AGR.MADEUP"):
        assemble(bad, REQUEST, sources, config)


def test_unclear_catalog_units_become_fill_me(sources, config):
    lcu = submission()
    lcu["variables"][0]["sources"] = [{"source": "wdi", "code": "NV.AGR.TOTL.KN"}]
    lcu["variables"][0]["label"] = "Value added, agriculture (constant LCU)"
    result = assemble(lcu, REQUEST, sources, config)
    src = result.spec["variables"][0]["sources"][0]
    assert src["currency"] == "LCU" and src["base_year"] == FILL_ME
    assert any("base_year" in item for item in result.to_fill)


def test_spec_rule_problems_are_sent_back(sources, config):
    bad = submission()
    bad["variables"][1]["name"] = "year"
    with pytest.raises(DraftRejected, match="reserved for the panel's key columns"):
        assemble(bad, REQUEST, sources, config)


def test_draft_files_are_readable(sources, config):
    result = assemble(submission(year_end=None, title="Uzbekistan since 2000"), "Uzbekistan since 2000",
                      sources, config)
    result.model = "claude-test"
    text = draft_yaml(result, "x.review.md", "2026-10-09")
    assert text.startswith("# DRAFT spec, written by claude-test on 2026-10-09")
    assert "FILL_ME" in text and "Employment counts by sector need ILOSTAT." in text
    assert yaml.safe_load(text)["years"]["end"] == FILL_ME
    review = review_markdown(result, "x.yaml", "2026-10-09")
    assert "**wdi NV.AGR.TOTL.KN**" in review and "Why: local currency" in review


# ------------------------------------------------------------- the loop


def tool_use(name, data, id_):
    return SimpleNamespace(type="tool_use", name=name, input=data, id=id_)


def reply(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=SimpleNamespace(input_tokens=10, output_tokens=5))


class FakeClaude:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(copy.deepcopy({k: v for k, v in kwargs.items() if k != "tools"}) | {"tools": kwargs["tools"]})
        return self.replies.pop(0)


def test_loop_searches_fixes_and_finishes(sources, config):
    bad = submission()
    bad["variables"][0]["sources"][0]["code"] = "NV.AGR.MADEUP"
    claude = FakeClaude([
        reply(tool_use("search_indicators", {"source": "wdi", "query": "agriculture value added"}, "t1")),
        reply(tool_use("submit_spec", bad, "t2")),
        reply(tool_use("submit_spec", submission(), "t3")),
    ])
    result = draft_spec(REQUEST, client=claude, config=config, sources=sources, progress=lambda _: None)
    assert result.spec["name"] == "uzb_kaz_test"
    assert result.searches == ["wdi: agriculture value added"]
    assert result.tokens == {"input": 30, "output": 15}

    first = claude.calls[0]
    assert first["model"] == config.models.spec_drafting
    assert first["thinking"] == {"type": "adaptive"} and first["fallbacks"] == "default"
    assert first["betas"] == [FALLBACK_BETA] and first["tools"] == TOOLS
    search_result = claude.calls[1]["messages"][-1]["content"][0]
    assert search_result["is_error"] is False and "NV.AGR.TOTL.KD" in search_result["content"]
    rejection = claude.calls[2]["messages"][-1]["content"][0]
    assert rejection["is_error"] is True and "NV.AGR.MADEUP" in rejection["content"]


def test_loop_nudges_when_claude_stops_without_submitting(sources, config):
    claude = FakeClaude([
        reply(SimpleNamespace(type="text", text="Here is my plan."), stop="end_turn"),
        reply(tool_use("submit_spec", submission(), "t1")),
    ])
    draft_spec(REQUEST, client=claude, config=config, sources=sources, progress=lambda _: None)
    assert "submit_spec" in claude.calls[1]["messages"][-1]["content"]


def test_refusal_and_running_out_of_rounds(sources, config):
    with pytest.raises(DraftError, match="declined"):
        draft_spec(REQUEST, client=FakeClaude([reply(stop="refusal")]), config=config, sources=sources)
    endless = FakeClaude([reply(tool_use("search_indicators", {"source": "wdi", "query": "gdp"}, f"t{i}"))
                          for i in range(3)])
    with pytest.raises(DraftError, match="No acceptable draft after 3 rounds"):
        draft_spec(REQUEST, client=endless, config=config, sources=sources, progress=lambda _: None, max_rounds=3)


def test_tool_schemas_follow_strict_rules():
    def check(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert sorted(schema["required"]) == sorted(schema["properties"])
            for sub in schema["properties"].values():
                check(sub)
        for key in ("items",):
            if key in schema:
                check(schema[key])
        for option in schema.get("anyOf", []):
            check(option)
        for banned in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern"):
            assert banned not in schema
    for tool in TOOLS:
        assert tool["strict"] is True
        check(tool["input_schema"])
