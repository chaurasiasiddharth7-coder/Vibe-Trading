"""TRUSTED_ORIGINS lets a hosted front end make unsafe requests, but only when an API key is set."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api import security


def _req(origin: str | None, site: str | None = None) -> Request:
    headers = [(b"host", b"api.example.com")]
    if origin:
        headers.append((b"origin", origin.encode()))
    if site:
        headers.append((b"sec-fetch-site", site.encode()))
    return Request({"type": "http", "method": "POST", "headers": headers, "client": ("1.2.3.4", 1), "path": "/", "query_string": b""})


def test_listed_origin_allowed_only_with_api_key(monkeypatch):
    monkeypatch.setenv("TRUSTED_ORIGINS", "https://app.vercel.app/, https://other.example")
    monkeypatch.setattr(security, "_configured_api_key", lambda: "k")
    security._reject_cross_site_browser_request(_req("https://app.vercel.app", "cross-site"))
    security._reject_cross_site_browser_request(_req("HTTPS://APP.vercel.app/"))
    with pytest.raises(HTTPException):
        security._reject_cross_site_browser_request(_req("https://evil.example", "cross-site"))
    with pytest.raises(HTTPException):
        security._reject_cross_site_browser_request(_req("https://evil.example"))


def test_ignored_without_api_key(monkeypatch):
    monkeypatch.setenv("TRUSTED_ORIGINS", "https://app.vercel.app")
    monkeypatch.setattr(security, "_configured_api_key", lambda: "")
    with pytest.raises(HTTPException):
        security._reject_cross_site_browser_request(_req("https://app.vercel.app"))
