"""PDF partitioning: layout detection, bounding boxes, table extraction.

Produces typed elements with bounding boxes — the mapping UI's click-to-highlight
is impossible without them.

Element types: page, heading, paragraph, table, kv_pair, list_item, footer
Each element has: kind, page, bbox {x0, y0, x1, y1}, text content
"""

import re
import uuid
from dataclasses import dataclass, field

import fitz  # pymupdf

from pipeline.observability.logging import get_logger
from pipeline.observability.tracing import SPAN_PARTITION, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@dataclass
class BBox:
    """Bounding box coordinates (PDF coordinate system)."""

    x0: float
    y0: float
    x1: float
    y1: float

    def to_dict(self) -> dict:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass
class PDFElement:
    """A typed element extracted from a PDF with its bounding box."""

    kind: str  # heading, paragraph, table, kv_pair, list_item, footer
    page: int
    bbox: BBox
    content: str
    ordinal: int = 0
    table_data: list[list[str]] | None = None  # For table elements


@dataclass
class PDFPartitionResult:
    """Result of partitioning a PDF document."""

    elements: list[PDFElement] = field(default_factory=list)
    page_count: int = 0
    has_text_layer: bool = True
    needs_ocr: bool = False


class PDFPartitioner:
    """Partitions a PDF into typed elements with bounding boxes.

    Uses PyMuPDF (fitz) for layout analysis. Tables are detected by
    looking for grid-like structures and preserving cell relationships.
    """

    # Heuristics for element classification
    HEADING_MIN_FONT_SIZE = 14.0
    HEADING_MAX_LINES = 3
    FOOTER_ZONE_RATIO = 0.9  # Bottom 10% of page
    KV_PAIR_PATTERN = re.compile(r"^([^:]+):\s*(.+)$")

    def partition(self, pdf_bytes: bytes, document_id: str | None = None) -> PDFPartitionResult:
        """Partition a PDF into typed elements with bounding boxes.

        Args:
            pdf_bytes: Raw PDF file content
            document_id: Optional document ID for logging (never log content)

        Returns:
            PDFPartitionResult with typed elements
        """
        with tracer.start_as_current_span(
            SPAN_PARTITION,
            attributes={"document_id": document_id or "unknown", "mime": "application/pdf"},
        ):
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            result = PDFPartitionResult(page_count=len(doc))

            # Check if document has a text layer
            first_page_text = doc[0].get_text() if len(doc) > 0 else ""
            if not first_page_text.strip():
                result.has_text_layer = False
                result.needs_ocr = True
                logger.info(
                    "pdf_needs_ocr",
                    document_id=document_id,
                    page_count=len(doc),
                )
                # Still try to extract what we can
                # In production, this would route to an OCR pipeline

            ordinal = 0
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_height = page.rect.height

                # Extract tables first (they need special handling)
                tables = self._extract_tables(page, page_num)
                table_rects = [
                    fitz.Rect(t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1) for t in tables
                ]

                # Get text blocks
                blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

                for block in blocks:
                    if block["type"] != 0:  # Skip image blocks
                        continue

                    bbox = BBox(
                        x0=block["bbox"][0],
                        y0=block["bbox"][1],
                        x1=block["bbox"][2],
                        y1=block["bbox"][3],
                    )

                    # Skip blocks that overlap with detected tables
                    block_rect = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
                    if any(block_rect.intersects(tr) for tr in table_rects):
                        continue

                    # Extract text and font info from lines
                    lines_text = []
                    max_font_size = 0.0
                    is_bold = False

                    for line in block.get("lines", []):
                        line_text = ""
                        for span in line.get("spans", []):
                            line_text += span.get("text", "")
                            font_size = span.get("size", 12.0)
                            max_font_size = max(max_font_size, font_size)
                            if "bold" in span.get("font", "").lower():
                                is_bold = True
                        lines_text.append(line_text)

                    text = "\n".join(lines_text).strip()
                    if not text:
                        continue

                    # Classify element
                    kind = self._classify_element(
                        text, bbox, max_font_size, is_bold, page_height, len(lines_text)
                    )

                    element = PDFElement(
                        kind=kind,
                        page=page_num + 1,  # 1-indexed
                        bbox=bbox,
                        content=text,
                        ordinal=ordinal,
                    )
                    result.elements.append(element)
                    ordinal += 1

                # Add table elements
                for table in tables:
                    table.ordinal = ordinal
                    result.elements.append(table)
                    ordinal += 1

            doc.close()

            logger.info(
                "pdf_partitioned",
                document_id=document_id,
                elements=len(result.elements),
                pages=result.page_count,
                needs_ocr=result.needs_ocr,
            )

            return result

    def _classify_element(
        self,
        text: str,
        bbox: BBox,
        font_size: float,
        is_bold: bool,
        page_height: float,
        line_count: int,
    ) -> str:
        """Classify a text block into an element type."""
        # Footer detection: bottom 10% of page
        if bbox.y0 > page_height * self.FOOTER_ZONE_RATIO:
            return "footer"

        # Heading detection: large font or bold with few lines
        if (font_size >= self.HEADING_MIN_FONT_SIZE or is_bold) and line_count <= self.HEADING_MAX_LINES:
            return "heading"

        # Key-value pair detection
        if line_count == 1 and self.KV_PAIR_PATTERN.match(text):
            return "kv_pair"

        # List item detection
        if text.lstrip().startswith(("•", "-", "●", "○", "▪")) or re.match(r"^\d+[\.\)]\s", text):
            return "list_item"

        # Default: paragraph
        return "paragraph"

    def _extract_tables(self, page, page_num: int) -> list[PDFElement]:
        """Extract tables from a page, preserving cell structure.

        Tables are the hard case and the most valuable: detect them,
        keep cell structure, and don't let a table get flattened into prose.
        """
        tables = []

        # Use PyMuPDF's table finder
        try:
            found_tables = page.find_tables()
        except Exception:
            return tables

        for table_idx, table in enumerate(found_tables):
            if table is None:
                continue

            # Get table bbox
            table_bbox = table.bbox
            bbox = BBox(
                x0=table_bbox[0],
                y0=table_bbox[1],
                x1=table_bbox[2],
                y1=table_bbox[3],
            )

            # Extract cell data preserving structure
            try:
                data = table.extract()
            except Exception:
                continue

            if not data:
                continue

            # Format as structured content (not flattened prose)
            # Headers are the first row, data follows
            headers = data[0] if data else []
            rows = data[1:] if len(data) > 1 else []

            # Build a text representation that preserves structure
            content_lines = []
            if headers:
                content_lines.append(" | ".join(str(h or "") for h in headers))
                content_lines.append("-" * 40)
            for row in rows:
                content_lines.append(" | ".join(str(cell or "") for cell in row))

            content = "\n".join(content_lines)

            element = PDFElement(
                kind="table",
                page=page_num + 1,
                bbox=bbox,
                content=content,
                table_data=data,
            )
            tables.append(element)

        return tables
