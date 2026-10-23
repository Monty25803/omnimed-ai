locals {
  buckets = {
    audit   = "${local.prefix}-audit-worm-${local.account_id}"
    reports = "${local.prefix}-reports-${local.account_id}"
    pki     = "${local.prefix}-pki-${local.account_id}"
    dicom   = "${local.prefix}-dicom-${local.account_id}"
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket              = each.value
  object_lock_enabled = each.key == "audit"
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# COMPLIANCE mode: not even the root account can shorten retention or delete a locked version.
resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.this["audit"].id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.audit_retention_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.this]
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [each.value.arn, "${each.value.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "audit" ? [1] : []
    content {
      sid     = "DenyVersionDeletion"
      effect  = "Deny"
      actions = ["s3:DeleteObjectVersion", "s3:PutBucketObjectLockConfiguration", "s3:BypassGovernanceRetention"]
      principals {
        type        = "*"
        identifiers = ["*"]
      }
      resources = [each.value.arn, "${each.value.arn}/*"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each = aws_s3_bucket.this

  bucket     = each.value.id
  policy     = data.aws_iam_policy_document.tls_only[each.key].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}

resource "aws_s3_bucket_lifecycle_configuration" "dicom" {
  bucket = aws_s3_bucket.this["dicom"].id
  rule {
    id     = "tier-cold-studies"
    status = "Enabled"
    filter {}
    transition {
      days          = 90
      storage_class = "INTELLIGENT_TIERING"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# The services use static S3 credentials (MINIO_ACCESS_KEY/SECRET_KEY) so the same code
# runs against MinIO locally; scope them tightly to the four buckets.
resource "aws_iam_user" "app_s3" {
  name = "${local.prefix}-app-s3"
}

resource "aws_iam_user_policy" "app_s3" {
  user = aws_iam_user.app_s3.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [for b in aws_s3_bucket.this : b.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:GetObjectRetention", "s3:PutObjectRetention"]
        Resource = [for b in aws_s3_bucket.this : "${b.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*"]
        Resource = [aws_kms_key.data.arn]
      }
    ]
  })
}

resource "aws_iam_access_key" "app_s3" {
  user = aws_iam_user.app_s3.name
}
