"""
Classification of changed files.

The important case is the third one. A file that looks retention related but
whose schema ClauseCI does not understand must never be treated as safe. It is
recorded so a later phase can raise review required rather than report a pass.
"""

from __future__ import annotations

from enum import Enum

from clauseci.settings import RETENTION_RELATED_HINTS, SUPPORTED_RETENTION_PATHS


class SurfaceClass(str, Enum):
    #: A retention configuration file whose schema ClauseCI understands.
    SUPPORTED_RETENTION = "supported_retention"
    #: Looks retention related, schema not understood. Needs a human.
    UNKNOWN_RETENTION_RELATED = "unknown_retention_related"
    #: Outside the supported surface. Recorded, not analyzed.
    UNSUPPORTED = "unsupported"


def classify_path(path: str) -> SurfaceClass:
    """Classify one repository path. Deterministic, no network, no model."""
    normalised = path.strip().lstrip("./").replace("\\", "/")

    if normalised in SUPPORTED_RETENTION_PATHS:
        return SurfaceClass.SUPPORTED_RETENTION

    lowered = normalised.lower()
    if any(hint in lowered for hint in RETENTION_RELATED_HINTS):
        return SurfaceClass.UNKNOWN_RETENTION_RELATED

    return SurfaceClass.UNSUPPORTED
