# Terraform configuration for AWS resources
# S3 bucket, KMS CMK, IAM, VPC endpoints

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "project" {
  description = "Project name"
  type        = string
  default     = "data-ingestion-pipeline"
}

# ============================================================================
# KMS — Customer Managed Key for envelope encryption
# ============================================================================

resource "aws_kms_key" "document_encryption" {
  description             = "CMK for document envelope encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountAccess"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid    = "AllowPipelineAccess"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.pipeline_role.arn
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey",
        ]
        Resource = "*"
      }
    ]
  })

  tags = {
    Project     = var.project
    Environment = var.environment
  }
}

resource "aws_kms_alias" "document_encryption" {
  name          = "alias/${var.project}-${var.environment}"
  target_key_id = aws_kms_key.document_encryption.key_id
}

# ============================================================================
# S3 — Document storage bucket (NEVER public)
# ============================================================================

resource "aws_s3_bucket" "documents" {
  bucket = "${var.project}-${var.environment}-documents"

  tags = {
    Project     = var.project
    Environment = var.environment
  }
}

# Block ALL public access
resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-KMS encryption at rest
resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.document_encryption.arn
    }
    bucket_key_enabled = true
  }
}

# Lifecycle rule: abort incomplete multipart uploads
resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
  }
}

# CORS for presigned multipart uploads from the browser
resource "aws_s3_bucket_cors_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "PUT", "HEAD"]
    allowed_origins = ["http://localhost:7800"] # Override per environment
    expose_headers  = ["ETag", "x-amz-request-id"]
    max_age_seconds = 3600
  }
}

# Versioning for audit trail
resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

# ============================================================================
# IAM — Pipeline role
# ============================================================================

resource "aws_iam_role" "pipeline_role" {
  name = "${var.project}-${var.environment}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "pipeline_s3" {
  name = "s3-access"
  role = aws_iam_role.pipeline_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts",
        "s3:CreateMultipartUpload",
        "s3:CompleteMultipartUpload",
      ]
      Resource = [
        aws_s3_bucket.documents.arn,
        "${aws_s3_bucket.documents.arn}/*",
      ]
    }]
  })
}

resource "aws_iam_role_policy" "pipeline_kms" {
  name = "kms-access"
  role = aws_iam_role.pipeline_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "kms:DescribeKey",
      ]
      Resource = [aws_kms_key.document_encryption.arn]
    }]
  })
}

# ============================================================================
# VPC Endpoints (for private S3 and KMS access)
# ============================================================================

data "aws_caller_identity" "current" {}
data "aws_vpc" "default" {
  default = true
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id       = data.aws_vpc.default.id
  service_name = "com.amazonaws.${var.aws_region}.s3"

  tags = {
    Project = var.project
  }
}

resource "aws_vpc_endpoint" "kms" {
  vpc_id            = data.aws_vpc.default.id
  service_name      = "com.amazonaws.${var.aws_region}.kms"
  vpc_endpoint_type = "Interface"

  tags = {
    Project = var.project
  }
}

# ============================================================================
# Outputs
# ============================================================================

output "bucket_name" {
  value = aws_s3_bucket.documents.id
}

output "kms_key_id" {
  value = aws_kms_key.document_encryption.key_id
}

output "kms_key_arn" {
  value = aws_kms_key.document_encryption.arn
}
