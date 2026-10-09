"""Downloading over the internet, with a few polite retries."""

import time
from collections.abc import Callable

import httpx

from . import __version__

USER_AGENT = f"econ-data-pipeline/{__version__} (academic research; python-httpx)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class DownloadError(RuntimeError):
    pass


def make_client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def get(client: httpx.Client, url: str, params: dict | None = None, *, attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep) -> httpx.Response:
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, params=params)
        except httpx.TransportError as exc:
            last = f"network problem: {exc.__class__.__name__}"
        else:
            if response.status_code == 200:
                return response
            if response.status_code not in RETRY_STATUSES:
                raise DownloadError(f"{response.url} answered with HTTP status {response.status_code}")
            last = f"HTTP status {response.status_code}"
        if attempt < attempts:
            sleep(min(2.0**attempt, 30.0))
    raise DownloadError(f"could not download {url} after {attempts} tries ({last})")
