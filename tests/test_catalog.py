from pipeline.catalog import search, tokens


def test_tokens_drop_filler_words_and_plural_s():
    assert tokens("Employment by sectors, in the services") == ["employment", "sector", "service"]


def test_search_finds_and_ranks(sources):
    catalog = sources.get("wdi").catalog().values()
    hits = search(catalog, "gross fixed capital formation constant")
    assert {h.code for h in hits[:2]} == {"NE.GDI.FTOT.KD", "NE.GDI.FTOT.KN"}
    assert search(catalog, "gross fixed capital formation constant") == hits  # same answer every time


def test_search_by_code(sources):
    hits = search(sources.get("pwt").catalog().values(), "rnna")
    assert hits[0].code == "rnna"


def test_phrase_at_start_of_name_ranks_first(sources):
    hits = search(sources.get("pwt").catalog().values(), "capital stock")
    assert hits[0].code == "rnna"


def test_no_match_and_empty_query(sources):
    catalog = sources.get("wdi").catalog().values()
    assert search(catalog, "zzzz") == []
    assert search(catalog, "of the") == []
