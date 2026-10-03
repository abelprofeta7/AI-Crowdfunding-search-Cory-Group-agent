"""Shared polite HTTP session: retries, timeouts, a real User-Agent, robots.txt."""
from __future__ import annotations

import logging
import time
import urllib.robotparser
from functools import lru_cache
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; CoryGroupLeadBot/1.0; +https://corygroup.co)"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "POST"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return s


SESSION = make_session()


@lru_cache(maxsize=2048)
def _robots(netloc: str, scheme: str) -> urllib.robotparser.RobotFileParser | None:
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = SESSION.get(f"{scheme}://{netloc}/robots.txt", timeout=8)
        if resp.status_code >= 400:
            return None
        rp.parse(resp.text.splitlines())
        return rp
    except requests.RequestException:
        return None


def allowed(url: str) -> bool:
    p = urlparse(url)
    rp = _robots(p.netloc, p.scheme or "https")
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def get(url: str, *, timeout: float = 15, respect_robots: bool = True, **kw) -> requests.Response | None:
    """GET that never raises: returns None on network errors or robots disallow."""
    if respect_robots and not allowed(url):
        log.debug("robots.txt disallows %s", url)
        return None
    try:
        return SESSION.get(url, timeout=timeout, **kw)
    except requests.RequestException as exc:
        log.debug("GET %s failed: %s", url, exc)
        return None


def get_json(url: str, **kw):
    resp = get(url, respect_robots=False, **kw)
    if resp is None or resp.status_code != 200:
        if resp is not None:
            log.info("GET %s -> HTTP %s", url, resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def pause(seconds: float = 1.0) -> None:
    time.sleep(seconds)
