"""Application configuration loaded from environment."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from .env or environment variables."""

    # Postgres
    database_url: str = "postgresql+asyncpg://pipeline:pipeline@localhost:7802/pipeline"

    # S3 / MinIO
    s3_endpoint_url: str = "http://localhost:7804"
    s3_bucket: str = "documents"
    aws_access_key_id: str = "minioadmin"
    aws_secret_access_key: str = "minioadmin"
    aws_region: str = "us-east-1"

    # KMS
    kms_key_id: str = ""
    kms_endpoint_url: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # Rate limits
    claude_rpm_limit: int = 50
    claude_tpm_limit: int = 100000
    claude_concurrency_limit: int = 5
    claude_cost_ceiling_usd: float = 50.0

    # Kafka
    kafka_bootstrap_servers: str = "localhost:7808"

    # OpenTelemetry
    otel_exporter_otlp_endpoint: str = "http://localhost:7811"
    otel_service_name: str = "data-ingestion-pipeline"

    # App
    app_origin: str = "http://localhost:7800"
    log_level: str = "INFO"

    # Upload
    presigned_url_expiry_seconds: int = 3600
    max_file_size_bytes: int = 500_000_000  # 500 MB
    multipart_part_size_bytes: int = 10_000_000  # 10 MB

    # Extraction cache
    extraction_cache_dir: str = ".cache/extractions"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
