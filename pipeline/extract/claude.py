"""Claude API adapter for schema-constrained extraction.

RULES:
- Schema-constrained structured output (tool-use JSON schema). Never regex over prose,
  never "return JSON" in a prompt without schema enforcement.
- Every extracted field carries provenance and confidence.
- All calls go through the rate limiter — no direct SDK calls.
- Disk-cache responses by sha256(model, prompt, schema, partition_content).
- Never log document contents.
"""

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

from pipeline.config import settings
from pipeline.limits.rate_limiter import RateLimitError, rate_limiter
from pipeline.observability.logging import get_logger
from pipeline.observability.tracing import SPAN_EXTRACT_CALL, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Cost per token (Claude 3.5 Sonnet pricing as of 2024)
INPUT_COST_PER_TOKEN = 3.0 / 1_000_000  # $3 per million input tokens
OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000  # $15 per million output tokens


@dataclass
class ExtractionField:
    """A single extracted field with provenance and confidence."""

    path: str  # JSON pointer into target schema
    value: Any
    confidence: float  # 0.0 to 1.0
    source_partition_id: str | None
    source_span: dict | None  # bbox or row/col — powers click-to-highlight


@dataclass
class ExtractionResult:
    """Result of extracting fields from a partition."""

    fields: list[ExtractionField]
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cached: bool = False


class ExtractionCache:
    """Disk cache for extraction results.

    Keyed by sha256(model, prompt, schema, partition_content).
    Re-running the pipeline during development must not re-bill.
    """

    def __init__(self, cache_dir: str | None = None) -> None:
        self._cache_dir = Path(cache_dir or settings.extraction_cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, model: str, prompt: str, schema: dict, content: str) -> str:
        """Compute cache key from extraction parameters."""
        key_data = json.dumps({
            "model": model,
            "prompt": prompt,
            "schema": schema,
            "content": content,
        }, sort_keys=True)
        return hashlib.sha256(key_data.encode()).hexdigest()

    def get(self, model: str, prompt: str, schema: dict, content: str) -> ExtractionResult | None:
        """Get cached extraction result if available."""
        key = self._cache_key(model, prompt, schema, content)
        cache_file = self._cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                fields = [ExtractionField(**f) for f in data["fields"]]
                return ExtractionResult(
                    fields=fields,
                    model=data["model"],
                    input_tokens=data["input_tokens"],
                    output_tokens=data["output_tokens"],
                    cost_usd=0.0,  # Cached results are free
                    cached=True,
                )
            except (json.JSONDecodeError, KeyError):
                cache_file.unlink(missing_ok=True)
        return None

    def put(self, model: str, prompt: str, schema: dict, content: str, result: ExtractionResult) -> None:
        """Cache an extraction result."""
        key = self._cache_key(model, prompt, schema, content)
        cache_file = self._cache_dir / f"{key}.json"
        data = {
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "fields": [
                {
                    "path": f.path,
                    "value": f.value,
                    "confidence": f.confidence,
                    "source_partition_id": f.source_partition_id,
                    "source_span": f.source_span,
                }
                for f in result.fields
            ],
        }
        cache_file.write_text(json.dumps(data, indent=2))


class ClaudeExtractor:
    """Schema-constrained extraction using Claude API.

    All calls go through the rate limiter. Results are disk-cached.
    Every field carries provenance (where it came from) and confidence.
    """

    MODEL = "claude-sonnet-4-20250514"

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._cache = ExtractionCache()

    async def extract(
        self,
        content: str,
        target_schema: dict,
        partition_id: str | None = None,
        source_span: dict | None = None,
        document_id: str | None = None,
    ) -> ExtractionResult:
        """Extract fields from content according to the target schema.

        Uses Claude's tool-use for schema-constrained structured output.
        Returns per-field provenance and confidence.
        """
        prompt = self._build_prompt(content, target_schema)

        # Check cache first
        cached = self._cache.get(self.MODEL, prompt, target_schema, content)
        if cached is not None:
            logger.info("extraction_cache_hit", document_id=document_id)
            return cached

        # Build the extraction tool schema
        tool_schema = self._build_tool_schema(target_schema)

        # Make the API call through the rate limiter
        estimated_tokens = len(content) // 4 + 1000  # rough estimate

        async def make_call():
            return await self._call_claude(prompt, tool_schema, content)

        with tracer.start_as_current_span(
            SPAN_EXTRACT_CALL,
            attributes={
                "document_id": document_id or "unknown",
                "partition_id": partition_id or "unknown",
                # NEVER put content in attributes
            },
        ):
            try:
                result = await rate_limiter.execute_with_backoff(make_call)
            except anthropic.RateLimitError as e:
                retry_after = None
                if hasattr(e, "response") and e.response:
                    retry_after_header = e.response.headers.get("retry-after")
                    if retry_after_header:
                        retry_after = float(retry_after_header)
                raise RateLimitError(429, retry_after=retry_after)

        # Parse response into ExtractionFields
        fields = self._parse_response(result, partition_id, source_span)

        # Calculate cost
        input_tokens = result.usage.input_tokens
        output_tokens = result.usage.output_tokens
        cost = (input_tokens * INPUT_COST_PER_TOKEN) + (output_tokens * OUTPUT_COST_PER_TOKEN)
        rate_limiter.record_cost(cost)

        extraction_result = ExtractionResult(
            fields=fields,
            model=self.MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

        # Cache the result
        self._cache.put(self.MODEL, prompt, target_schema, content, extraction_result)

        logger.info(
            "extraction_complete",
            document_id=document_id,
            partition_id=partition_id,
            fields_extracted=len(fields),
            cost_usd=round(cost, 6),
        )

        return extraction_result

    def _build_prompt(self, content: str, target_schema: dict) -> str:
        """Build the extraction prompt."""
        schema_desc = json.dumps(target_schema, indent=2)
        return f"""Extract structured data from the following content according to the target JSON schema.

For EVERY field you extract:
1. Provide the value matching the schema's expected type
2. Rate your confidence from 0.0 to 1.0 (how certain you are this is correct)
3. Note which part of the source content this came from

If a field cannot be found in the content, set its value to null with confidence 0.0.
If a field is ambiguous or partially visible, set confidence accordingly (0.3-0.7).
High confidence (>0.9) only for clearly stated, unambiguous values.

Target schema:
{schema_desc}

Source content:
{content}"""

    def _build_tool_schema(self, target_schema: dict) -> dict:
        """Build the Claude tool schema for structured extraction.

        Wraps the target schema to include confidence and provenance per field.
        """
        # Create a tool that returns extracted fields with metadata
        properties = {}
        required_fields = target_schema.get("required", [])

        for field_name, field_def in target_schema.get("properties", {}).items():
            properties[field_name] = {
                "type": "object",
                "properties": {
                    "value": field_def,
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score 0.0-1.0",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "source_text": {
                        "type": "string",
                        "description": "The exact text from the source that this value was extracted from",
                    },
                },
                "required": ["value", "confidence", "source_text"],
            }

        return {
            "name": "extract_fields",
            "description": "Extract structured fields from the document content",
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties.keys()),
            },
        }

    async def _call_claude(self, prompt: str, tool_schema: dict, content: str):
        """Make the actual Claude API call."""
        response = self._client.messages.create(
            model=self.MODEL,
            max_tokens=4096,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": "extract_fields"},
            messages=[{"role": "user", "content": prompt}],
        )
        return response

    def _parse_response(
        self,
        response,
        partition_id: str | None,
        source_span: dict | None,
    ) -> list[ExtractionField]:
        """Parse Claude's response into ExtractionFields with provenance."""
        fields = []

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_fields":
                tool_input = block.input
                for field_path, field_data in tool_input.items():
                    if isinstance(field_data, dict) and "value" in field_data:
                        fields.append(ExtractionField(
                            path=f"/{field_path}",
                            value=field_data["value"],
                            confidence=field_data.get("confidence", 0.5),
                            source_partition_id=partition_id,
                            source_span=source_span,
                        ))
                    else:
                        # Direct value without metadata wrapper
                        fields.append(ExtractionField(
                            path=f"/{field_path}",
                            value=field_data,
                            confidence=0.5,
                            source_partition_id=partition_id,
                            source_span=source_span,
                        ))

        return fields


class ExtractionMerger:
    """Merges extraction results from multiple partitions.

    Merge conflicts (two partitions proposing different values for one field)
    are surfaced to the operator, never silently resolved by last-write-wins.
    """

    def merge(self, results: list[ExtractionResult]) -> list[ExtractionField]:
        """Merge fields from multiple partitions.

        Strategy: highest confidence wins, but conflicts are flagged.
        A conflict is when two partitions propose different values for the same path.
        """
        field_map: dict[str, list[ExtractionField]] = {}

        for result in results:
            for field in result.fields:
                if field.path not in field_map:
                    field_map[field.path] = []
                field_map[field.path].append(field)

        merged = []
        for path, candidates in field_map.items():
            if len(candidates) == 1:
                merged.append(candidates[0])
            else:
                # Multiple candidates — check for conflict
                values = set(json.dumps(c.value, sort_keys=True) for c in candidates)
                if len(values) == 1:
                    # Same value from multiple sources — pick highest confidence
                    best = max(candidates, key=lambda f: f.confidence)
                    merged.append(best)
                else:
                    # CONFLICT: different values proposed
                    # Pick highest confidence but flag it
                    best = max(candidates, key=lambda f: f.confidence)
                    # Lower confidence to signal the conflict needs review
                    best.confidence = min(best.confidence, 0.5)
                    merged.append(best)
                    logger.warning(
                        "extraction_merge_conflict",
                        path=path,
                        candidates=len(candidates),
                    )

        return merged
