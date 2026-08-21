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


class AggregationError(HoopshypeImportError):
    """Base class for expected salary aggregation failures."""


class AggregationValidationError(AggregationError):
    """Raised when deduplicated rows or occurrence provenance are invalid."""


class AggregationDecisionError(AggregationError):
    """Raised when a commissioner decision is invalid or cannot be applied."""


class CsvFallbackError(HoopshypeImportError):
    """Base class for expected commissioner CSV fallback failures."""


class CsvFallbackValidationError(CsvFallbackError):
    """Raised with deterministic sanitized input-validation diagnostics."""

    def __init__(self, diagnostics: tuple[object, ...]) -> None:
        self.diagnostics = diagnostics
        rendered = "\n".join(f"- {diagnostic}" for diagnostic in diagnostics)
        super().__init__(
            f"CSV fallback validation failed with {len(diagnostics)} error(s):"
            + (f"\n{rendered}" if rendered else "")
        )


class CsvFallbackArtifactError(CsvFallbackError):
    """Raised when a derived fallback artifact is invalid or cannot publish."""


class CsvFallbackReconciliationError(CsvFallbackError):
    """Raised when canonical fallback/browser reconciliation is unsafe."""
