"""
utils/http.py
-------------
Retry-enabled HTTP session + media download safety.

Faz 1.1 — requests retry + backoff (rapor2.txt §1.1)
Faz 1.3 — media download boyut limiti (rapor2.txt §1.3)
"""
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MAX_MEDIA_BYTES = 100 * 1024 * 1024  # 100 MB hard limit

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def get_session(retries: int = 3, backoff: float = 1.0) -> requests.Session:
    """Retry-enabled requests session.

    Otomatik olarak 429 / 5xx kodlarında bekleme + tekrar dener.
    backoff_factor=1.0 → bekleme süresi: 0s, 1s, 2s, 4s ...
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(_DEFAULT_HEADERS)
    return session


def download_with_size_limit(url: str, timeout: int = 30) -> bytes:
    """URL'den içerik indir. MAX_MEDIA_BYTES aşılırsa ValueError fırlatır."""
    session = get_session()
    resp = session.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()

    total = 0
    chunks = []
    for chunk in resp.iter_content(chunk_size=8192):
        total += len(chunk)
        if total > MAX_MEDIA_BYTES:
            raise ValueError(
                f"Media too large: >{MAX_MEDIA_BYTES // 1024 // 1024} MB — aborted."
            )
        chunks.append(chunk)
    return b"".join(chunks)
