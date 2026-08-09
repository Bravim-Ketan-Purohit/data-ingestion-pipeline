"""CSV partitioning: encoding/delimiter sniffing, header repair, type inference.

Handles the "messy" in "messy CSVs":
- Encoding detection (UTF-8/UTF-16/Latin-1, BOM)
- Delimiter sniffing (, ; \t |)
- Quoting and embedded newlines
- Preamble junk rows before the real header
- Multi-row headers
- Merged/duplicate/blank column names
- Inconsistent row lengths
- Trailing summary rows
- Mixed date formats
- Thousands separators, currency symbols
- NULL/N/A/-/empty as distinct nulls
"""

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

import chardet

from pipeline.observability.logging import get_logger
from pipeline.observability.tracing import SPAN_PARTITION, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Null-like values to detect
NULL_VALUES = frozenset({
    "", "null", "NULL", "Null", "None", "none", "NONE",
    "N/A", "n/a", "NA", "na", "#N/A", "#NA",
    "-", "--", "---", ".", "..",
    "NaN", "nan", "NAN",
    "#REF!", "#VALUE!", "#NULL!",
})

# Date patterns for detection
DATE_PATTERNS = [
    re.compile(r"\d{4}-\d{2}-\d{2}"),  # ISO: 2024-01-15
    re.compile(r"\d{2}/\d{2}/\d{4}"),  # US: 01/30/2024
    re.compile(r"\d{2}\.\d{2}\.\d{4}"),  # EU: 15.02.2024
    re.compile(r"\d{2}-\w{3}-\d{4}"),  # 01-Mar-2024
    re.compile(r"\w+\s+\d{1,2},?\s+\d{4}"),  # January 1, 2024 or April 1, 2024
    re.compile(r"\d{1,2}\.\d{2}\.\d{4}"),  # Short EU: 1.02.2024
]


@dataclass
class CSVColumn:
    """Inferred column metadata."""

    name: str
    original_name: str
    index: int
    inferred_type: str  # string, integer, float, date, boolean, null
    null_count: int = 0
    sample_values: list[str] = field(default_factory=list)


@dataclass
class CSVPartitionResult:
    """Result of partitioning a CSV file."""

    columns: list[CSVColumn]
    rows: list[list[Any]]
    encoding: str
    delimiter: str
    has_header: bool
    preamble_rows: int  # Number of junk rows before the header
    header_rows: int  # Number of rows that form the header (for multi-row headers)
    trailing_rows: int  # Number of summary/trailing rows removed
    total_raw_rows: int
    issues: list[str] = field(default_factory=list)


class CSVPartitioner:
    """Partitions a CSV into structured data with type inference.

    Handles encoding, delimiter, header detection, and type inference
    for messy real-world CSVs.
    """

    def partition(self, csv_bytes: bytes, document_id: str | None = None) -> CSVPartitionResult:
        """Partition a CSV file into structured columns and rows."""
        with tracer.start_as_current_span(
            SPAN_PARTITION,
            attributes={"document_id": document_id or "unknown", "mime": "text/csv"},
        ):
            # Step 1: Detect encoding
            encoding = self._detect_encoding(csv_bytes)

            # Step 2: Decode
            text = self._decode(csv_bytes, encoding)

            # Step 3: Detect delimiter
            delimiter = self._detect_delimiter(text)

            # Step 4: Parse with detected settings
            lines = self._parse_lines(text, delimiter)

            # Step 5: Detect and remove preamble
            lines, preamble_rows = self._remove_preamble(lines)

            # Step 6: Detect header (single or multi-row)
            header, header_rows, lines = self._detect_header(lines)

            # Step 7: Repair header names
            header = self._repair_header(header)

            # Step 8: Normalize row lengths
            lines, issues = self._normalize_rows(lines, len(header))

            # Step 9: Detect and remove trailing summary rows
            lines, trailing_rows = self._remove_trailing_rows(lines, len(header))

            # Step 10: Type inference per column
            columns = self._infer_types(header, lines)

            logger.info(
                "csv_partitioned",
                document_id=document_id,
                encoding=encoding,
                delimiter=repr(delimiter),
                columns=len(columns),
                rows=len(lines),
                preamble_rows=preamble_rows,
                header_rows=header_rows,
            )

            return CSVPartitionResult(
                columns=columns,
                rows=lines,
                encoding=encoding,
                delimiter=delimiter,
                has_header=True,
                preamble_rows=preamble_rows,
                header_rows=header_rows,
                trailing_rows=trailing_rows,
                total_raw_rows=preamble_rows + header_rows + len(lines) + trailing_rows,
                issues=issues,
            )

    def _detect_encoding(self, data: bytes) -> str:
        """Detect file encoding, handling BOM markers."""
        # Check for BOM
        if data.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if data.startswith(b"\xff\xfe"):
            return "utf-16-le"
        if data.startswith(b"\xfe\xff"):
            return "utf-16-be"

        # Use chardet for detection
        result = chardet.detect(data[:10000])
        encoding = result.get("encoding", "utf-8") or "utf-8"

        # Normalize common names
        encoding = encoding.lower().replace("-", "_")
        encoding_map = {
            "ascii": "utf-8",
            "iso_8859_1": "latin-1",
            "windows_1252": "latin-1",
        }
        return encoding_map.get(encoding, encoding.replace("_", "-"))

    def _decode(self, data: bytes, encoding: str) -> str:
        """Decode bytes to string with fallback."""
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return data.decode("utf-8", errors="replace")

    def _detect_delimiter(self, text: str) -> str:
        """Detect the delimiter by frequency analysis."""
        # Take first few lines for analysis
        sample_lines = text.split("\n")[:20]
        sample = "\n".join(sample_lines)

        candidates = [",", ";", "\t", "|"]
        scores: dict[str, float] = {}

        for delim in candidates:
            try:
                reader = csv.reader(io.StringIO(sample), delimiter=delim)
                rows = list(reader)
                if not rows:
                    continue

                # Score: consistency of column count across rows
                col_counts = [len(r) for r in rows if r]
                if not col_counts:
                    continue

                # Prefer delimiters that give consistent column counts > 1
                most_common_count = max(set(col_counts), key=col_counts.count)
                consistency = col_counts.count(most_common_count) / len(col_counts)
                scores[delim] = consistency * most_common_count
            except csv.Error:
                continue

        if not scores:
            return ","

        return max(scores, key=scores.get)

    def _parse_lines(self, text: str, delimiter: str) -> list[list[str]]:
        """Parse CSV text into rows, handling quoting and embedded newlines."""
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = []
        for row in reader:
            rows.append(row)
        return rows

    def _remove_preamble(self, lines: list[list[str]]) -> tuple[list[list[str]], int]:
        """Remove preamble junk rows before the real header.

        Preamble rows are typically: empty rows, single-cell rows with metadata,
        rows with significantly fewer columns than the data.
        """
        if not lines:
            return lines, 0

        # Find the mode column count (likely the data column count)
        col_counts = [len(row) for row in lines]
        if not col_counts:
            return lines, 0

        data_col_count = max(set(col_counts[1:10] if len(col_counts) > 1 else col_counts), key=col_counts.count)

        preamble_rows = 0
        for i, row in enumerate(lines):
            # A row is preamble if it has significantly fewer columns
            # or is empty or has only one non-empty cell
            non_empty = [c for c in row if c.strip()]
            if len(row) < data_col_count * 0.5 or len(non_empty) <= 1:
                preamble_rows = i + 1
            else:
                break

        return lines[preamble_rows:], preamble_rows

    def _detect_header(self, lines: list[list[str]]) -> tuple[list[str], int, list[list[str]]]:
        """Detect the header row(s). Handles multi-row headers."""
        if not lines:
            return [], 0, []

        # Simple case: first row is header
        # Multi-row header: first N rows where cells merge to form full column names
        first_row = lines[0]

        # Check if second row might be part of a multi-row header
        if len(lines) > 1:
            second_row = lines[1]
            # If second row has many non-empty cells that look like header continuations
            # (not numeric, not date-like), it might be a merged header
            if self._looks_like_header_row(second_row) and not self._looks_like_data_row(second_row):
                # Merge first two rows
                merged = []
                for i in range(max(len(first_row), len(second_row))):
                    top = first_row[i].strip() if i < len(first_row) else ""
                    bottom = second_row[i].strip() if i < len(second_row) else ""
                    if top and bottom:
                        merged.append(f"{top} {bottom}")
                    else:
                        merged.append(top or bottom)
                return merged, 2, lines[2:]

        return first_row, 1, lines[1:]

    def _looks_like_header_row(self, row: list[str]) -> bool:
        """Check if a row looks like a header (non-numeric, short text values)."""
        non_empty = [c for c in row if c.strip()]
        if not non_empty:
            return False
        numeric_count = sum(1 for c in non_empty if self._is_numeric(c))
        # Also check that values are short (headers tend to be short labels)
        long_values = sum(1 for c in non_empty if len(c.strip()) > 50)
        if long_values > 0:
            return False
        # Check for embedded newlines (headers don't have those)
        has_newlines = any("\n" in c for c in non_empty)
        if has_newlines:
            return False
        return numeric_count / len(non_empty) < 0.3

    def _looks_like_data_row(self, row: list[str]) -> bool:
        """Check if a row looks like a data row (has numbers, dates, or long text)."""
        non_empty = [c for c in row if c.strip()]
        if not non_empty:
            return False
        numeric_or_date = sum(1 for c in non_empty if self._is_numeric(c) or self._is_date(c))
        # Rows with embedded newlines or long values are data, not headers
        has_newlines = any("\n" in c for c in non_empty)
        if has_newlines:
            return True
        return numeric_or_date / len(non_empty) > 0.3

    def _repair_header(self, header: list[str]) -> list[str]:
        """Repair merged/duplicate/blank column names."""
        repaired = []
        seen: dict[str, int] = {}

        for i, name in enumerate(header):
            name = name.strip()

            # Handle blank column names
            if not name:
                name = f"column_{i + 1}"

            # Handle duplicates by appending a suffix
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0

            repaired.append(name)

        return repaired

    def _normalize_rows(
        self, lines: list[list[str]], expected_cols: int
    ) -> tuple[list[list[str]], list[str]]:
        """Normalize rows to consistent length."""
        issues = []
        normalized = []

        for i, row in enumerate(lines):
            if len(row) < expected_cols:
                # Pad short rows
                row = row + [""] * (expected_cols - len(row))
                issues.append(f"Row {i + 1}: padded from {len(lines[i])} to {expected_cols} columns")
            elif len(row) > expected_cols:
                # Truncate long rows
                issues.append(f"Row {i + 1}: truncated from {len(row)} to {expected_cols} columns")
                row = row[:expected_cols]
            normalized.append(row)

        return normalized, issues

    def _remove_trailing_rows(
        self, lines: list[list[str]], col_count: int
    ) -> tuple[list[list[str]], int]:
        """Remove trailing summary/total rows."""
        if not lines:
            return lines, 0

        trailing = 0
        # Check last few rows for summary indicators
        summary_indicators = {"total", "sum", "average", "count", "grand total", "subtotal"}

        for row in reversed(lines):
            first_cell = row[0].strip().lower() if row else ""
            if first_cell in summary_indicators:
                trailing += 1
            else:
                break

        if trailing > 0:
            return lines[:-trailing], trailing
        return lines, 0

    def _infer_types(self, header: list[str], rows: list[list[str]]) -> list[CSVColumn]:
        """Infer column types from data."""
        columns = []

        for col_idx, col_name in enumerate(header):
            values = [row[col_idx] for row in rows if col_idx < len(row)]
            non_null = [v for v in values if v.strip() and v.strip().lower() not in NULL_VALUES]
            null_count = len(values) - len(non_null)

            inferred_type = self._infer_column_type(non_null)
            sample = non_null[:5] if non_null else []

            columns.append(CSVColumn(
                name=col_name,
                original_name=col_name,
                index=col_idx,
                inferred_type=inferred_type,
                null_count=null_count,
                sample_values=sample,
            ))

        return columns

    def _infer_column_type(self, values: list[str]) -> str:
        """Infer the type of a column from its non-null values."""
        if not values:
            return "null"

        # Sample for type detection
        sample = values[:100]

        bool_count = sum(1 for v in sample if v.strip().lower() in {"true", "false", "yes", "no", "1", "0"})
        int_count = sum(1 for v in sample if self._is_integer(v))
        float_count = sum(1 for v in sample if self._is_numeric(v))
        date_count = sum(1 for v in sample if self._is_date(v))

        n = len(sample)
        threshold = 0.8

        if bool_count / n >= threshold:
            return "boolean"
        if int_count / n >= threshold:
            return "integer"
        if float_count / n >= threshold:
            return "float"
        if date_count / n >= 0.5:  # Lower threshold for dates (mixed formats common)
            return "date"

        return "string"

    def _is_integer(self, value: str) -> bool:
        """Check if a value is an integer (handles thousands separators)."""
        cleaned = value.strip().replace(",", "").replace(" ", "")
        try:
            int(cleaned)
            return True
        except ValueError:
            return False

    def _is_numeric(self, value: str) -> bool:
        """Check if a value is numeric (handles currency, thousands separators)."""
        cleaned = value.strip()
        # Remove currency symbols
        cleaned = re.sub(r"[$€£¥₹]", "", cleaned)
        # Remove thousands separators
        cleaned = cleaned.replace(",", "").replace(" ", "")
        # Handle percentage
        cleaned = cleaned.rstrip("%")
        try:
            float(cleaned)
            return True
        except ValueError:
            return False

    def _is_date(self, value: str) -> bool:
        """Check if a value looks like a date."""
        return any(p.search(value.strip()) for p in DATE_PATTERNS)
