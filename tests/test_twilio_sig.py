"""Twilio signature validation, cross-checked against the official SDK when installed."""

from __future__ import annotations

import pytest

URL = "https://hooks.nabu.casa/gAAAAABexample?kind=sms"
PARAMS = {"From": "+17075550100", "Body": "EVACUATION ORDER LAK-E123", "AccountSid": "ACxxxx"}
TOKEN = "test_auth_token"


def test_roundtrip(twilio_sig):
    sig = twilio_sig.compute_signature(TOKEN, URL, PARAMS)
    assert twilio_sig.is_valid(TOKEN, URL, PARAMS, sig)


def test_rejects_tampering(twilio_sig):
    sig = twilio_sig.compute_signature(TOKEN, URL, PARAMS)
    assert not twilio_sig.is_valid(TOKEN, URL, {**PARAMS, "Body": "all clear"}, sig)
    assert not twilio_sig.is_valid(TOKEN, URL + "x", PARAMS, sig)
    assert not twilio_sig.is_valid("other", URL, PARAMS, sig)
    assert not twilio_sig.is_valid(TOKEN, URL, PARAMS, None)
    assert not twilio_sig.is_valid("", URL, PARAMS, sig)


def test_matches_official_sdk(twilio_sig):
    validator_mod = pytest.importorskip("twilio.request_validator")
    expected = validator_mod.RequestValidator(TOKEN).compute_signature(URL, PARAMS)
    assert twilio_sig.compute_signature(TOKEN, URL, PARAMS) == expected


@pytest.mark.parametrize(
    ("signed_url", "configured_url"),
    [
        ("https://x.duckdns.org:443/api/webhook/abc?kind=sms", "https://x.duckdns.org/api/webhook/abc?kind=sms"),
        ("https://x.duckdns.org/api/webhook/abc?kind=sms", "https://x.duckdns.org:443/api/webhook/abc?kind=sms"),
    ],
)
def test_default_port_variants(twilio_sig, signed_url, configured_url):
    sig = twilio_sig.compute_signature(TOKEN, signed_url, PARAMS)
    assert twilio_sig.is_valid(TOKEN, configured_url, PARAMS, sig)


def test_custom_port_must_match(twilio_sig):
    sig = twilio_sig.compute_signature(TOKEN, "https://x.example:8123/hook", PARAMS)
    assert twilio_sig.is_valid(TOKEN, "https://x.example:8123/hook", PARAMS, sig)
    assert not twilio_sig.is_valid(TOKEN, "https://x.example:8124/hook", PARAMS, sig)


def test_join_query(twilio_sig):
    assert twilio_sig.join_query("https://a/b", "kind=sms") == "https://a/b?kind=sms"
    assert twilio_sig.join_query("https://a/b?t=1", "kind=sms") == "https://a/b?t=1&kind=sms"
    assert twilio_sig.join_query("https://a/b", "") == "https://a/b"


@pytest.mark.parametrize(
    "url",
    ["https://hooks.nabu.casa/abc?kind=sms", "https://x.example:443/abc?kind=voice", "http://x.example/abc"],
)
def test_agrees_with_sdk_validate(twilio_sig, url):
    validator_mod = pytest.importorskip("twilio.request_validator")
    validator = validator_mod.RequestValidator(TOKEN)
    for signed in twilio_sig.url_variants(url):
        sig = twilio_sig.compute_signature(TOKEN, signed, PARAMS)
        assert validator.validate(url, PARAMS, sig) == twilio_sig.is_valid(TOKEN, url, PARAMS, sig)


def test_signed_url_candidates(twilio_sig):
    assert twilio_sig.signed_url_candidates("https://a/b", "kind=sms") == ["https://a/b?kind=sms"]
    assert twilio_sig.signed_url_candidates("https://a/b?token=x", "token=x&kind=sms") == [
        "https://a/b?token=x&token=x&kind=sms",
        "https://a/b?token=x&kind=sms",
    ]
