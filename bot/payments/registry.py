"""Choosing the provider for the configured payment mode. Construction makes
no network calls."""

from __future__ import annotations

from .base import PaymentProvider
from .mock import MockProvider
from .stars import StarsProvider


def build_provider(mode: str) -> PaymentProvider:
    if mode == "stars":
        return StarsProvider()
    if mode == "mock":
        return MockProvider()
    raise ValueError(f"Unknown payment mode {mode!r}")
