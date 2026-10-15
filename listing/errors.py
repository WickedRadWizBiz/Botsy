"""Errors raised by the listing stage (kept separate so every module can import them)."""
from __future__ import annotations


class ListingError(RuntimeError):
    """The item cannot be listed (not through QA, missing price, bad paths, ...)."""


class ListingComplianceError(ListingError):
    """A blocked trademark term (or a URL) was found. listing.json is not written."""

    def __init__(self, message: str, terms: list[str] | None = None):
        super().__init__(message)
        self.terms = list(terms or [])


class ListingValidationError(ListingError):
    """The copy still breaks an Etsy rule after retries and repair (should not happen)."""
