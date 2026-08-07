"""KMS envelope encryption.

Client-side envelope encryption happens BEFORE S3 upload, so bytes are unreadable
even with bucket access. Plus SSE-KMS on the bucket for defense in depth.

Flow:
1. Generate a data key from KMS (plaintext + encrypted copy)
2. Encrypt the document with the plaintext data key (AES-256-GCM)
3. Discard the plaintext data key
4. Store the encrypted data key alongside the ciphertext
5. Upload the ciphertext to S3 (with SSE-KMS enabled on bucket)

Decryption:
1. Retrieve encrypted data key and ciphertext
2. Call KMS Decrypt to get plaintext data key
3. Decrypt ciphertext with the plaintext data key
"""

import os
from dataclasses import dataclass

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pipeline.config import settings
from pipeline.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class EncryptedPayload:
    """The result of envelope encryption."""

    ciphertext: bytes
    encrypted_data_key: bytes
    nonce: bytes  # 12 bytes for AES-GCM
    key_id: str


class KMSEnvelopeEncryption:
    """Client-side envelope encryption using AWS KMS."""

    def __init__(self) -> None:
        kms_kwargs = {
            "region_name": settings.aws_region,
            "aws_access_key_id": settings.aws_access_key_id,
            "aws_secret_access_key": settings.aws_secret_access_key,
        }
        if settings.kms_endpoint_url:
            kms_kwargs["endpoint_url"] = settings.kms_endpoint_url

        self._kms = boto3.client("kms", **kms_kwargs)
        self._key_id = settings.kms_key_id

    @property
    def enabled(self) -> bool:
        """Whether KMS encryption is configured."""
        return bool(self._key_id)

    def encrypt(self, plaintext: bytes) -> EncryptedPayload:
        """Encrypt data using envelope encryption.

        This MUST be called before uploading to S3.
        """
        if not self._key_id:
            raise RuntimeError("KMS key ID not configured")

        # Generate data key
        response = self._kms.generate_data_key(
            KeyId=self._key_id,
            KeySpec="AES_256",
        )

        plaintext_key = response["Plaintext"]
        encrypted_key = response["CiphertextBlob"]

        # Encrypt with AES-256-GCM
        nonce = os.urandom(12)
        aesgcm = AESGCM(plaintext_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        # Zero out plaintext key from memory (best effort in Python)
        plaintext_key = b"\x00" * len(plaintext_key)

        logger.info("envelope_encryption_complete", key_id=self._key_id)

        return EncryptedPayload(
            ciphertext=ciphertext,
            encrypted_data_key=encrypted_key,
            nonce=nonce,
            key_id=self._key_id,
        )

    def decrypt(self, payload: EncryptedPayload) -> bytes:
        """Decrypt envelope-encrypted data."""
        # Decrypt the data key via KMS
        response = self._kms.decrypt(
            CiphertextBlob=payload.encrypted_data_key,
            KeyId=payload.key_id,
        )
        plaintext_key = response["Plaintext"]

        # Decrypt the ciphertext
        aesgcm = AESGCM(plaintext_key)
        plaintext = aesgcm.decrypt(payload.nonce, payload.ciphertext, None)

        # Zero out plaintext key
        plaintext_key = b"\x00" * len(plaintext_key)

        return plaintext
