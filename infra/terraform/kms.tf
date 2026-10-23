data "aws_iam_policy_document" "kms_root" {
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }
}

data "aws_iam_policy_document" "data_key" {
  source_policy_documents = [data.aws_iam_policy_document.kms_root.json]

  statement {
    sid     = "CloudWatchLogs"
    actions = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    resources = ["*"]
  }
}

# RDS, ElastiCache, S3, Secrets Manager and CloudWatch Logs.
resource "aws_kms_key" "data" {
  description             = "${local.prefix} data-at-rest"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.data_key.json
}

resource "aws_kms_alias" "data" {
  name          = "alias/${local.prefix}-data"
  target_key_id = aws_kms_key.data.key_id
}

# Vault auto-unseal (awskms seal stanza). Only the Vault IRSA role may use it.
resource "aws_kms_key" "vault_unseal" {
  description             = "${local.prefix} vault auto-unseal"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms_root.json
}

resource "aws_kms_alias" "vault_unseal" {
  name          = "alias/${local.prefix}-vault-unseal"
  target_key_id = aws_kms_key.vault_unseal.key_id
}

# Envelope encryption of Kubernetes Secrets in etcd.
resource "aws_kms_key" "eks" {
  description             = "${local.prefix} eks secrets"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms_root.json
}

resource "aws_kms_alias" "eks" {
  name          = "alias/${local.prefix}-eks"
  target_key_id = aws_kms_key.eks.key_id
}
