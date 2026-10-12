import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest
import yaml

from pipeline.cache import RawCache
from pipeline.connectors import Sources
from pipeline.paths import SPECS_DIR

EXAMPLE_SPEC = SPECS_DIR / "example_uzb_kaz.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
PWT_FIXTURE = FIXTURES / "pwt" / "pwt_sample.dta"


@pytest.fixture
def example_data() -> dict:
    """The example spec as plain data, fresh for each test so tests can edit it."""
    return copy.deepcopy(yaml.safe_load(EXAMPLE_SPEC.read_text(encoding="utf-8")))


@pytest.fixture
def write_spec(tmp_path: Path):
    def _write(data: dict, name: str = "spec.yaml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    return _write


def dataverse_listing(content: bytes, sha1: str | None = None) -> dict:
    return {"status": "OK", "data": {"latestVersion": {
        "versionNumber": 2, "versionMinorNumber": 0, "license": {"name": "CC-BY-4.0"},
        "files": [{"dataFile": {"filename": "pwt110.dta", "id": 554030, "filesize": len(content),
                                "checksum": {"type": "SHA-1", "value": sha1 or hashlib.sha1(content).hexdigest()}}}],
    }}}


class FakeInternet:
    """Answers the World Bank and Dataverse requests from saved fixture files,
    and counts every request so tests can tell a download from a cache hit."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.overrides: dict[str, list[httpx.Response]] = {}
        self.pwt_bytes = PWT_FIXTURE.read_bytes()
        self.pwt_sha1: str | None = None

    def add(self, path: str, *responses: httpx.Response) -> None:
        """Queue special answers for a URL path (used once each, in order)."""
        self.overrides.setdefault(path, []).extend(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.overrides.get(path):
            return self.overrides[path].pop(0)
        host = request.url.host
        if host == "rplumber.ilo.org":
            if path == "/metadata/toc/indicator/":
                return httpx.Response(200, content=(FIXTURES / "ilostat" / "toc.csv").read_bytes())
            f = FIXTURES / "ilostat" / f"{request.url.params['id']}_{request.url.params['ref_area']}.csv"
            return httpx.Response(200, content=f.read_bytes() if f.exists() else b"")
        if host == "api.imf.org":
            if "/dataflow/" in path:
                return httpx.Response(200, content=(FIXTURES / "imf" / "weo_structure.xml").read_bytes())
            iso3, code = path.rsplit("/", 1)[1].split(".")[:2]
            f = FIXTURES / "imf" / f"weo_{iso3}_{code}.csv"
            if f.exists():
                return httpx.Response(200, content=f.read_bytes())
            return httpx.Response(404, content=b"<error>NoResultsFound</error>")
        if host == "bulks-faostat.fao.org":
            if path.endswith("datasets_E.json"):
                return httpx.Response(200, content=(FIXTURES / "faostat" / "datasets_E.json").read_bytes())
            return httpx.Response(200, content=(FIXTURES / "faostat" / "CS.zip").read_bytes())
        if host == "api.siat.stat.uz":
            if path == "/sdmx/":
                return httpx.Response(200, content=(FIXTURES / "siat" / "catalog.json").read_bytes())
            if path.endswith("/table/download/"):
                code = path.split("/")[2]
                if not (FIXTURES / "siat" / f"table_{code}.json").exists():
                    return httpx.Response(200, json={"detail": "not found"})
                return httpx.Response(200, json={"file": f"https://api.siat.stat.uz/media/uploads/sdmx/sdmx_data_{code}.json",
                                                 "updated_at": "2026-07-31T10:14:57+05:00"})
            code = path.rsplit("_", 1)[1].split(".")[0]
            return httpx.Response(200, content=(FIXTURES / "siat" / f"table_{code}.json").read_bytes())
        if path == "/v2/source/2/indicator":
            return httpx.Response(200, content=(FIXTURES / "wdi" / "catalog_sample.json").read_bytes())
        if path.startswith("/v2/country/"):
            _, _, _, iso3, _, code = path.split("/")
            f = FIXTURES / "wdi" / f"data_{code}_{iso3}.json"
            if f.exists():
                return httpx.Response(200, content=f.read_bytes())
            empty = [{"page": 1, "pages": 1, "per_page": 20000, "total": 0, "sourceid": "2", "lastupdated": "2026-10-08"}, None]
            return httpx.Response(200, content=json.dumps(empty).encode())
        if path == "/api/datasets/:persistentId/":
            return httpx.Response(200, json=dataverse_listing(self.pwt_bytes, self.pwt_sha1))
        if path == "/api/access/datafile/554030":
            return httpx.Response(200, content=self.pwt_bytes)
        return httpx.Response(404, content=b"not found")


@pytest.fixture
def internet() -> FakeInternet:
    return FakeInternet()


@pytest.fixture
def sources(tmp_path: Path, internet: FakeInternet) -> Sources:
    client = httpx.Client(transport=httpx.MockTransport(internet))
    srcs = Sources(RawCache(tmp_path / "raw"), client, attempts=3, sleep=lambda _: None)
    yield srcs
    srcs.close()
