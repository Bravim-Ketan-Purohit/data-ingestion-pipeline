"""FastAPI application — main entry point.

Ports: 7801 (this project owns 7800–7899)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pipeline.config import settings
from pipeline.observability.logging import configure_logging
from pipeline.observability.tracing import init_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: setup and teardown."""
    configure_logging()
    init_tracing()
    yield


app = FastAPI(
    title="Data Ingestion Pipeline",
    description="Drag-and-drop normalization of messy PDFs and CSVs into strict JSON schemas",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — scoped to app origin only. Never allow *.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# Import and include routers
from pipeline.api.routes import documents, exports, events, schemas, timing, uploads  # noqa: E402

app.include_router(uploads.router, prefix="/api/uploads", tags=["uploads"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(schemas.router, prefix="/api/schemas", tags=["schemas"])
app.include_router(exports.router, prefix="/api", tags=["exports"])
app.include_router(timing.router, prefix="/api/timing", tags=["timing"])
app.include_router(events.router, prefix="/api/events", tags=["events"])


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/api/runs")
async def get_runs():
    """Get throughput, throttling, cost stats."""
    from pipeline.limits.rate_limiter import rate_limiter
    return rate_limiter.stats
