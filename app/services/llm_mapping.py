"""
LLM-assisted column mapping for invoice history imports.

Sends the uploaded file's headers plus a handful of sample rows to an LLM,
which proposes how each canonical field maps to a source column with a
confidence score. The proposal is strict JSON validated against a schema and
is only ever a *suggestion* — the user reviews and confirms or corrects it
before anything is imported, and import itself applies the confirmed mapping
deterministically without touching the LLM again.

Behind an interface (ColumnMappingProvider) so the test suite can use
FakeColumnMapper and never make a network call.
"""
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

CANONICAL_FIELDS = [
    "invoice_id",
    "customer_id",
    "customer_name",
    "invoice_date",
    "due_date",
    "paid_date",
    "amount",
    "currency",
    "payment_terms",
]

REQUIRED_CANONICAL_FIELDS = [
    "invoice_id",
    "customer_name",
    "invoice_date",
    "due_date",
    "amount",
    "currency",
]

MAX_SAMPLE_ROWS = 20


class MappingProviderError(Exception):
    """Raised when the mapping provider can't produce a usable proposal:
    a timeout, a network failure, a refusal, or output that doesn't parse
    as the expected schema."""


@dataclass
class FieldMapping:
    canonical_field: str
    source_column: Optional[str]
    confidence: float


@dataclass
class MappingProposal:
    mappings: list[FieldMapping] = field(default_factory=list)

    def as_dict(self) -> dict:
        """{canonical_field: {source_column, confidence}} — the shape stored
        on PendingImport.proposed_mapping and read back by the review page."""
        return {
            m.canonical_field: {"source_column": m.source_column, "confidence": m.confidence}
            for m in self.mappings
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MappingProposal":
        return cls(mappings=[
            FieldMapping(canonical_field=field_name, source_column=v.get("source_column"), confidence=v.get("confidence", 0.0))
            for field_name, v in data.items()
        ])


class ColumnMappingProvider(ABC):
    @abstractmethod
    def propose_mapping(self, headers: list[str], sample_rows: list[dict]) -> MappingProposal:
        """`sample_rows` should be at most MAX_SAMPLE_ROWS dicts keyed by header."""
        raise NotImplementedError


def _mapping_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "canonical_field": {"type": "string", "enum": CANONICAL_FIELDS},
                        "source_column": {"type": ["string", "null"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["canonical_field", "source_column", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["mappings"],
        "additionalProperties": False,
    }


class AnthropicColumnMapper(ColumnMappingProvider):
    """Real implementation. Never used in the test suite — see FakeColumnMapper."""

    def __init__(self, api_key: Optional[str] = None, timeout: float = 20.0):
        import anthropic  # imported lazily so the suite never needs the package importable at collection time

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise MappingProviderError("ANTHROPIC_API_KEY is not set")
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=key, timeout=timeout)

    def propose_mapping(self, headers: list[str], sample_rows: list[dict]) -> MappingProposal:
        anthropic = self._anthropic
        prompt = (
            "Map these spreadsheet columns to the canonical invoice fields.\n\n"
            f"Canonical fields: {', '.join(CANONICAL_FIELDS)}\n\n"
            f"Source columns: {json.dumps(headers)}\n\n"
            f"Sample rows (up to {MAX_SAMPLE_ROWS}):\n{json.dumps(sample_rows[:MAX_SAMPLE_ROWS], default=str)}\n\n"
            "For every canonical field, propose the single best-matching source "
            "column, or null if nothing in the source columns matches. Give a "
            "confidence between 0 and 1 reflecting how sure you are."
        )
        try:
            response = self._client.messages.create(
                model="claude-opus-4-8",
                max_tokens=2048,
                output_config={"format": {"type": "json_schema", "schema": _mapping_schema()}},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APITimeoutError as e:
            raise MappingProviderError(f"LLM request timed out: {e}") from e
        except anthropic.APIError as e:
            raise MappingProviderError(f"LLM request failed: {e}") from e

        if response.stop_reason == "refusal":
            raise MappingProviderError("LLM declined to propose a mapping for this file")

        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise MappingProviderError("LLM returned no text content")

        try:
            data = json.loads(text)
            mappings = [
                FieldMapping(
                    canonical_field=m["canonical_field"],
                    source_column=m["source_column"],
                    confidence=float(m["confidence"]),
                )
                for m in data["mappings"]
            ]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise MappingProviderError(f"LLM returned a malformed mapping: {e}") from e

        return MappingProposal(mappings=mappings)


class FakeColumnMapper(ColumnMappingProvider):
    """Test double. Either returns a fixed MappingProposal, raises a fixed
    error (to simulate a timeout or malformed output), or — if neither is
    given — makes a naive case-insensitive/substring guess so tests that
    don't care about the exact mapping still get a plausible one."""

    def __init__(self, response: Optional[MappingProposal] = None, error: Optional[Exception] = None):
        self._response = response
        self._error = error

    def propose_mapping(self, headers: list[str], sample_rows: list[dict]) -> MappingProposal:
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response

        lowered = {h.lower(): h for h in headers}
        guesses = {
            "invoice_id": ["invoice_id", "invoice id", "invoice number", "invoice_number", "invoice#"],
            "customer_id": ["customer_id", "customer id"],
            "customer_name": ["customer_name", "customer name", "customer", "client"],
            "invoice_date": ["invoice_date", "invoice date", "date"],
            "due_date": ["due_date", "due date"],
            "paid_date": ["paid_date", "paid date", "payment date"],
            "amount": ["amount", "total", "invoice amount"],
            "currency": ["currency"],
            "payment_terms": ["payment_terms", "payment terms", "terms"],
        }
        mappings = []
        for canonical, candidates in guesses.items():
            match = next((lowered[c] for c in candidates if c in lowered), None)
            mappings.append(FieldMapping(
                canonical_field=canonical,
                source_column=match,
                confidence=0.9 if match else 0.0,
            ))
        return MappingProposal(mappings=mappings)
