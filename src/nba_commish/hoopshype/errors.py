"""Sanitized errors exposed by the Hoopshype importer."""


class HoopshypeImportError(Exception):
    """Base class for expected, safe-to-display importer failures."""


class SeasonValidationError(HoopshypeImportError):
    """Raised before browser launch when a season value is invalid."""


class SourceStructureError(HoopshypeImportError):
    """Raised when the rendered public table violates its source contract."""


class BrowserCollectionError(HoopshypeImportError):
    """Raised when Chromium cannot complete a public-source collection."""


class ArtifactWriteError(HoopshypeImportError):
    """Raised when the final artifact cannot be published safely."""


class SalaryParseError(HoopshypeImportError):
    """Raised for expected salary grammar or row-context validation failures."""


class FingerprintError(HoopshypeImportError):
    """Base class for expected fingerprint and deduplication failures."""


class FingerprintValidationError(FingerprintError):
    """Raised when normalized row evidence cannot be fingerprinted safely."""


class FingerprintCollisionError(FingerprintError):
    """Raised when equal digests correspond to different canonical payloads."""
