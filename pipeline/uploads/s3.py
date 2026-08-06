"""S3 client for presigned multipart uploads.

HARD RULES (from CLAUDE.md):
- File bytes NEVER pass through the API — presigned multipart, browser → S3 direct
- Never log a presigned URL (it IS a credential)
- Never make a bucket public
- Presigned URLs have short expiry, CORS scoped to app origin
"""

import hashlib
from typing import Any

import boto3
from botocore.config import Config

from pipeline.config import settings
from pipeline.observability.logging import get_logger

logger = get_logger(__name__)


def get_s3_client() -> Any:
    """Create an S3 client configured for MinIO or real AWS."""
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


class S3MultipartManager:
    """Manages presigned multipart uploads to S3.

    The browser uploads parts directly to S3 using presigned URLs.
    This manager tracks the upload state and provides resume capability.
    """

    def __init__(self) -> None:
        self._client = get_s3_client()
        self._bucket = settings.s3_bucket

    def compute_part_plan(self, file_size: int) -> list[dict[str, int]]:
        """Compute the part plan for a file.

        Part size 8-16 MB (S3 minimum is 5 MB except last part).
        Stay under the 10,000-part limit.
        """
        part_size = settings.multipart_part_size_bytes  # 10 MB default

        # Adjust part size to stay under 10,000 parts
        while file_size / part_size > 9999:
            part_size *= 2

        parts = []
        offset = 0
        part_number = 1
        while offset < file_size:
            size = min(part_size, file_size - offset)
            parts.append({
                "part_number": part_number,
                "offset": offset,
                "size": size,
            })
            offset += size
            part_number += 1

        return parts

    def initiate_multipart_upload(self, s3_key: str, content_type: str) -> str:
        """Initiate a multipart upload on S3. Returns the upload_id."""
        response = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=s3_key,
            ContentType=content_type,
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=settings.kms_key_id if settings.kms_key_id else None,
        ) if settings.kms_key_id else self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=s3_key,
            ContentType=content_type,
        )
        upload_id = response["UploadId"]
        logger.info(
            "multipart_upload_initiated",
            s3_key=s3_key,
            # Never log the upload_id as it could be used to construct URLs
        )
        return upload_id

    def generate_presigned_part_url(
        self, s3_key: str, upload_id: str, part_number: int
    ) -> str:
        """Generate a presigned URL for uploading a single part.

        Short expiry. The browser uses this to PUT bytes directly to S3.
        """
        url = self._client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self._bucket,
                "Key": s3_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=settings.presigned_url_expiry_seconds,
        )
        # NEVER log this URL — it IS a credential
        return url

    def generate_presigned_get_url(self, s3_key: str, expiry: int = 3600) -> str:
        """Generate a presigned GET URL for downloading/viewing a document."""
        url = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": s3_key},
            ExpiresIn=expiry,
        )
        return url

    def list_uploaded_parts(self, s3_key: str, upload_id: str) -> list[dict[str, Any]]:
        """List parts already uploaded for a multipart upload (for resume support)."""
        parts: list[dict[str, Any]] = []
        marker = 0

        while True:
            response = self._client.list_parts(
                Bucket=self._bucket,
                Key=s3_key,
                UploadId=upload_id,
                PartNumberMarker=marker,
            )
            for part in response.get("Parts", []):
                parts.append({
                    "part_number": part["PartNumber"],
                    "etag": part["ETag"],
                    "size": part["Size"],
                })

            if not response.get("IsTruncated", False):
                break
            marker = response["NextPartNumberMarker"]

        return parts

    def complete_multipart_upload(
        self, s3_key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> str:
        """Complete the multipart upload. Returns the final ETag."""
        multipart_upload = {
            "Parts": [
                {"PartNumber": p["part_number"], "ETag": p["etag"]}
                for p in sorted(parts, key=lambda x: x["part_number"])
            ]
        }
        response = self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=s3_key,
            UploadId=upload_id,
            MultipartUpload=multipart_upload,
        )
        logger.info("multipart_upload_completed", s3_key=s3_key)
        return response["ETag"]

    def abort_multipart_upload(self, s3_key: str, upload_id: str) -> None:
        """Abort an incomplete multipart upload. Cleans up orphaned parts."""
        self._client.abort_multipart_upload(
            Bucket=self._bucket,
            Key=s3_key,
            UploadId=upload_id,
        )
        logger.info("multipart_upload_aborted", s3_key=s3_key)

    def get_object_bytes(self, s3_key: str) -> bytes:
        """Download object bytes from S3 (for partitioning)."""
        response = self._client.get_object(Bucket=self._bucket, Key=s3_key)
        return response["Body"].read()

    @staticmethod
    def compute_content_hash(data: bytes) -> str:
        """Compute SHA-256 hash for content-based deduplication."""
        return hashlib.sha256(data).hexdigest()
