"""Downloading over the internet, with a few polite retries."""

import time
from collections.abc import Callable

import httpx

from . import __version__

# Some services (ILOSTAT) return empty replies to agents that name an HTTP library,
# so the pipeline names only itself.
USER_AGENT = f"econ-data-pipeline/{__version__} (academic research)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class DownloadError(RuntimeError):
    pass


def make_client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def get(client: httpx.Client, url: str, params: dict | None = None, *, attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep, ok_statuses: tuple[int, ...] = (200,),
        headers: dict[str, str] | None = None) -> httpx.Response:
    """ok_statuses lists replies that are answers rather than failures (for
    example 404 "no data for this query" from an SDMX service)."""
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            last = f"network problem: {exc.__class__.__name__}"
        else:
            if response.status_code in ok_statuses:
                return response
            if response.status_code not in RETRY_STATUSES:
                raise DownloadError(f"{response.url} answered with HTTP status {response.status_code}")
            last = f"HTTP status {response.status_code}"
        if attempt < attempts:
            sleep(min(2.0**attempt, 30.0))
    raise DownloadError(f"could not download {url} after {attempts} tries ({last})")
