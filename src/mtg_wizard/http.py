"""Polite HTTP client: retries, per-host rate limit and a small on-disk cache."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

DEFAULT_UA = "MagicWizardShopping/0.1 (+https://github.com/awyplosz/Magic_wizard_shopping)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class HttpClient:
    def __init__(self, cache_dir: str | Path = ".cache", default_delay: float = 0.5,
                 host_delays: dict[str, float] | None = None, timeout: float = 30.0,
                 offline: bool = False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_delay = default_delay
        self.host_delays = host_delays or {}
        self.timeout = timeout
        self.offline = offline
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"GET", "POST"}), respect_retry_after_header=True)
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers["User-Agent"] = DEFAULT_UA

    # ------------------------------------------------------------------ cache
    def _cache_path(self, namespace: str, key: str, suffix: str = ".json") -> Path:
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        d = self.cache_dir / namespace
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{h}{suffix}"

    def cache_get(self, namespace: str, key: str, ttl_hours: float):
        p = self._cache_path(namespace, key)
        if p.exists() and (self.offline or time.time() - p.stat().st_mtime < ttl_hours * 3600):
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return None

    def cache_set(self, namespace: str, key: str, value) -> None:
        p = self._cache_path(namespace, key)
        try:
            p.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:  # cache is best effort
            log.debug("cache write failed: %s", exc)

    # --------------------------------------------------------------- requests
    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        delay = self.host_delays.get(host, self.default_delay)
        with self._lock:
            wait = self._last.get(host, 0) + delay - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.time()

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        if self.offline:
            raise ConnectionError(f"offline mode: not fetching {url}")
        self._throttle(url)
        kwargs.setdefault("timeout", self.timeout)
        resp = self.session.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def get_json_cached(self, namespace: str, url: str, ttl_hours: float, *, method: str = "GET",
                        cache_key: str | None = None, **kwargs):
        key = cache_key or f"{method} {url} {json.dumps(kwargs.get('params'), sort_keys=True)} " \
                           f"{json.dumps(kwargs.get('json'), sort_keys=True)}"
        hit = self.cache_get(namespace, key, ttl_hours)
        if hit is not None:
            return hit
        data = self.request(method, url, **kwargs).json()
        self.cache_set(namespace, key, data)
        return data

    def get_text_cached(self, namespace: str, url: str, ttl_hours: float, **kwargs) -> str:
        key = f"GET {url} {json.dumps(kwargs.get('params'), sort_keys=True)}"
        hit = self.cache_get(namespace, key, ttl_hours)
        if hit is not None:
            return hit
        text = self.get(url, **kwargs).text
        self.cache_set(namespace, key, text)
        return text

    def download_file(self, url: str, dest: Path, ttl_hours: float, **kwargs) -> Path:
        """Download a (large) file to `dest` unless a fresh copy exists."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and (self.offline or time.time() - dest.stat().st_mtime < ttl_hours * 3600):
            return dest
        if self.offline:
            raise ConnectionError(f"offline mode and no cached copy of {url}")
        self._throttle(url)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with self.session.get(url, stream=True, timeout=max(self.timeout, 120), **kwargs) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        tmp.replace(dest)
        return dest
