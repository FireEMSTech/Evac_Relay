"""Twilio request signature validation.

Implements Twilio's documented scheme without the twilio SDK: HMAC-SHA1 over
the full request URL followed by each POST parameter name and value, sorted
by name, keyed with the account auth token, base64 encoded. Like the official
SDK, validation accepts the URL with or without an explicit default port,
because Twilio is not consistent about including it.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
import hashlib
import hmac
from urllib.parse import urlsplit, urlunsplit

_DEFAULT_PORTS = {"https": 443, "http": 80}


def compute_signature(auth_token: str, url: str, params: Mapping[str, str | Iterable[str]]) -> str:
    """Return the expected X-Twilio-Signature for a request."""
    payload = url
    for key in sorted(params):
        value = params[key]
        values = [value] if isinstance(value, str) else sorted(value)
        for item in values:
            payload += key + item
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def url_variants(url: str) -> list[str]:
    """The URL as given, plus the same URL with the default port added or removed."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port is None:
        port = _DEFAULT_PORTS.get(parts.scheme)
        other = urlunsplit(parts._replace(netloc=f"{host}:{port}")) if port else url
    else:
        other = urlunsplit(parts._replace(netloc=host))
    return list(dict.fromkeys([url, other]))


def is_valid(
    auth_token: str,
    url: str,
    params: Mapping[str, str | Iterable[str]],
    signature: str | None,
) -> bool:
    """Constant-time check of a Twilio signature against the URL and its port variant."""
    if not auth_token or not signature:
        return False
    return any(
        hmac.compare_digest(compute_signature(auth_token, candidate, params), signature)
        for candidate in url_variants(url)
    )


def join_query(base: str, raw_query: str) -> str:
    """Append a raw query string to a base URL that may already have one."""
    if not raw_query:
        return base
    return f"{base}{'&' if '?' in base else '?'}{raw_query}"


def signed_url_candidates(base: str, raw_query: str) -> list[str]:
    """URLs Twilio may have signed.

    The configured base plus the request's query, and, when the base already
    carries its own query that a proxy passed through, the base's scheme, host
    and path with the request's query alone.
    """
    joined = join_query(base, raw_query)
    parts = urlsplit(base)
    replaced = urlunsplit(parts._replace(query=raw_query)) if parts.query else joined
    return list(dict.fromkeys([joined, replaced]))
