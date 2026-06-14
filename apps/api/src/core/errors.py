"""Application-wide exception types.

Each exception carries a stable error ``code`` (design.md §9) that the global
exception handler converts into the standard ``{"error": {...}}`` envelope.
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected, user-visible API errors.

    Subclasses set :attr:`code` (the machine-readable string the client sees)
    and :attr:`http_status`. ``message`` and ``details`` default to safe values
    that don't leak internals.
    """

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.code
        self.details = details
        super().__init__(self.message)


class CaptureInvalid(AppError):
    code = "CAPTURE_INVALID"
    http_status = 422


class ReconstructFailed(AppError):
    code = "RECONSTRUCT_FAILED"
    http_status = 500


class JobNotFound(AppError):
    code = "JOB_NOT_FOUND"
    http_status = 404


class AssetNotFound(AppError):
    code = "ASSET_NOT_FOUND"
    http_status = 404


class CaptureNotFound(AppError):
    code = "CAPTURE_NOT_FOUND"
    http_status = 404


class PartNotFound(AppError):
    code = "PART_NOT_FOUND"
    http_status = 404


__all__ = [
    "AppError",
    "AssetNotFound",
    "CaptureInvalid",
    "CaptureNotFound",
    "JobNotFound",
    "PartNotFound",
    "ReconstructFailed",
]
