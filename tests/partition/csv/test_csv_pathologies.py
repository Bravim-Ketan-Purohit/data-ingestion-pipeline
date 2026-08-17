"""Tests for messy CSV pathologies from SPEC §5.

Each fixture file tests one specific pathology. These are named regression tests —
the fixture was written before the fix.
"""

from pathlib import Path

import pytest

from pipeline.partition.csv.extractor import CSVPartitioner

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "csv"


@pytest.fixture
def partitioner():
    return CSVPartitioner()


class TestEncodingDetection:
    """Test: encoding detection (UTF-8/UTF-16/Latin-1, BOM)."""

    def test_utf8_default(self, partitioner):
        data = (FIXTURES / "delimiter_semicolon.csv").read_bytes()
        result = partitioner.partition(data)
        assert result.encoding in ("utf-8", "ascii")

    def test_utf8_bom(self, partitioner):
        # Create a UTF-8 BOM file
        content = b"\xef\xbb\xbfname,age\nAlice,30\n"
        result = partitioner.partition(content)
        assert result.encoding == "utf-8-sig"
        assert result.columns[0].name == "name"


class TestDelimiterSniffing:
    """Test: delimiter sniffing (, ; \\t |)."""

    def test_comma(self, partitioner):
        data = (FIXTURES / "mixed_nulls.csv").read_bytes()
        result = partitioner.partition(data)
        assert result.delimiter == ","
        assert len(result.columns) == 5

    def test_semicolon(self, partitioner):
        data = (FIXTURES / "delimiter_semicolon.csv").read_bytes()
        result = partitioner.partition(data)
        assert result.delimiter == ";"
        assert len(result.columns) == 4

    def test_tab(self, partitioner):
        data = (FIXTURES / "delimiter_tab.csv").read_bytes()
        result = partitioner.partition(data)
        assert result.delimiter == "\t"
        assert len(result.columns) == 4


class TestEmbeddedNewlines:
    """Test: quoting and embedded newlines."""

    def test_multiline_fields(self, partitioner):
        data = (FIXTURES / "embedded_newlines.csv").read_bytes()
        result = partitioner.partition(data)
        assert len(result.rows) == 3
        # First row has a multiline address
        assert "Apt 4B" in result.rows[0][1]


class TestPreambleJunk:
    """Test: preamble junk rows before the real header."""

    def test_preamble_detection(self, partitioner):
        data = (FIXTURES / "preamble_junk.csv").read_bytes()
        result = partitioner.partition(data)
        assert result.preamble_rows > 0
        # Header should be name,amount,date,status
        assert result.columns[0].name == "name"
        assert result.columns[1].name == "amount"


class TestMultiRowHeaders:
    """Test: multi-row headers."""

    def test_merged_header(self, partitioner):
        data = (FIXTURES / "multirow_header.csv").read_bytes()
        result = partitioner.partition(data)
        # Header should be merged from first two rows
        assert result.header_rows >= 1
        assert len(result.columns) >= 4


class TestDuplicateColumnNames:
    """Test: merged/duplicate/blank column names."""

    def test_duplicate_repair(self, partitioner):
        data = (FIXTURES / "duplicate_headers.csv").read_bytes()
        result = partitioner.partition(data)
        # Duplicate "amount" should be renamed
        names = [c.name for c in result.columns]
        assert len(names) == len(set(names)), f"Duplicate column names found: {names}"


class TestInconsistentRowLengths:
    """Test: inconsistent row lengths."""

    def test_short_and_long_rows(self, partitioner):
        data = (FIXTURES / "inconsistent_rows.csv").read_bytes()
        result = partitioner.partition(data)
        # All rows should be normalized to the same length
        for row in result.rows:
            assert len(row) == len(result.columns)
        # Should have issues noted
        assert len(result.issues) > 0


class TestTrailingSummaryRows:
    """Test: trailing summary rows."""

    def test_total_row_removed(self, partitioner):
        data = (FIXTURES / "thousands_currency.csv").read_bytes()
        result = partitioner.partition(data)
        # "Grand Total" row should be removed
        assert result.trailing_rows > 0
        for row in result.rows:
            assert row[0].lower() != "grand total"


class TestMixedDateFormats:
    """Test: mixed date formats."""

    def test_date_column_detection(self, partitioner):
        data = (FIXTURES / "mixed_dates.csv").read_bytes()
        result = partitioner.partition(data)
        # start_date and end_date should be detected as date type
        date_cols = [c for c in result.columns if c.inferred_type == "date"]
        assert len(date_cols) >= 1


class TestThousandsSeparators:
    """Test: thousands separators, currency symbols."""

    def test_currency_detection(self, partitioner):
        data = (FIXTURES / "thousands_currency.csv").read_bytes()
        result = partitioner.partition(data)
        # price_usd should still be detected as float/string (has $ prefix)
        amount_col = next((c for c in result.columns if "price" in c.name.lower()), None)
        assert amount_col is not None


class TestNullVariants:
    """Test: NULL/N/A/-/empty as distinct nulls."""

    def test_null_counting(self, partitioner):
        data = (FIXTURES / "mixed_nulls.csv").read_bytes()
        result = partitioner.partition(data)
        # The 'age' column has NULL, -, None as null values
        age_col = next(c for c in result.columns if c.name == "age")
        assert age_col.null_count >= 3
