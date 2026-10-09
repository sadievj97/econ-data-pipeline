import pytest

from pipeline.spec import SourceRef
from pipeline.units import UnitInfo, compare, from_pwt_label, from_wdi_name


@pytest.mark.parametrize("name, expected", [
    ("GDP (constant 2015 US$)", UnitInfo("constant 2015 US$", "constant", 2015, "USD")),
    ("GDP (current US$)", UnitInfo("current US$", "current", None, "USD")),
    ("Gross fixed capital formation (constant LCU)", UnitInfo("constant LCU", "constant", None, "LCU")),
    ("GDP, PPP (constant 2021 international $)", UnitInfo("constant 2021 international $", "constant", 2021, None)),
    ("Employment in agriculture (% of total employment) (modeled ILO estimate)",
     UnitInfo("% of total employment", "not_applicable", None, None)),
    ("GDP growth (annual %)", UnitInfo("annual %", "not_applicable", None, None)),
    ("Population, total", UnitInfo("", None, None, None)),
])
def test_wdi_names(name, expected):
    assert from_wdi_name(name) == expected


@pytest.mark.parametrize("label, expected", [
    ("Real GDP at constant 2021 national prices (in mil. 2021US$)",
     UnitInfo("millions of 2021 US$", "constant", 2021, "USD")),
    ("Expenditure-side real GDP at chained PPPs (in mil. 2021US$)",
     UnitInfo("millions of 2021 US$", "constant", 2021, "USD")),
    ("Share of labour compensation in GDP at current national prices", UnitInfo("share", "current", None, None)),
    ("Number of persons engaged (in millions)", UnitInfo("millions", "not_applicable", None, None)),
    ("Capital services at constant 2021 national prices (2021=1)", UnitInfo("index, 2021=1", "constant", 2021, None)),
    ("TFP at constant national prices (2021=1)", UnitInfo("index, 2021=1", "not_applicable", 2021, None)),
    ("Capital stock at current PPPs (in mil. 2021US$)", UnitInfo("millions of 2021 US$", None, None, None)),
])
def test_pwt_labels(label, expected):
    assert from_pwt_label(label) == expected


def ref(**kw):
    base = dict(source="wdi", code="X", unit="u", price_basis="constant", base_year=2015, currency="USD")
    return SourceRef(**(base | kw))


def test_matching_expectations_give_no_errors():
    assert compare(ref(), UnitInfo("constant 2015 US$", "constant", 2015, "USD")) == ([], [])


def test_disagreements_are_errors():
    errors, _ = compare(ref(), UnitInfo("current US$", "current", None, "USD"))
    assert any("price_basis constant" in e for e in errors)
    errors, _ = compare(ref(), UnitInfo("constant 2010 US$", "constant", 2010, "USD"))
    assert any("base_year 2015" in e for e in errors)
    errors, _ = compare(ref(currency="EUR"), UnitInfo("constant 2015 US$", "constant", 2015, "USD"))
    assert any("currency EUR" in e for e in errors)


def test_unclear_wording_is_a_warning_not_a_guess():
    errors, warnings = compare(ref(price_basis="not_applicable", base_year=None, currency=None),
                               UnitInfo("", None, None, None))
    assert errors == [] and any("does not state a price basis" in w for w in warnings)


def test_local_currency_rules():
    lcu = UnitInfo("constant LCU", "constant", None, "LCU")
    assert compare(ref(currency="LCU"), lcu)[0] == []
    assert any("one base year" in w for w in compare(ref(currency="LCU"), lcu)[1])
    assert any("only fits a single country" in w for w in compare(ref(currency="UZS"), lcu)[1])
    assert compare(ref(price_basis="constant", currency=None), lcu)[0]
