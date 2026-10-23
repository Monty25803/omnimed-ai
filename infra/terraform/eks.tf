module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name    = local.prefix
  cluster_version = var.eks_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_private_access      = true
  cluster_endpoint_public_access       = length(var.eks_public_access_cidrs) > 0
  cluster_endpoint_public_access_cidrs = var.eks_public_access_cidrs

  create_kms_key = false
  cluster_encryption_config = {
    provider_key_arn = aws_kms_key.eks.arn
    resources        = ["secrets"]
  }

  cluster_enabled_log_types              = ["api", "audit", "authenticator"]
  cloudwatch_log_group_kms_key_id        = aws_kms_key.data.arn
  cloudwatch_log_group_retention_in_days = 365

  enable_irsa                              = true
  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true, configuration_values = jsonencode({ enableNetworkPolicy = "true" }) }
    aws-ebs-csi-driver     = { service_account_role_arn = aws_iam_role.ebs_csi.arn }
    eks-pod-identity-agent = {}
  }

  eks_managed_node_groups = {
    general = {
      instance_types = var.node_instance_types
      min_size       = var.node_min
      max_size       = var.node_max
      desired_size   = var.node_min
      ami_type       = "BOTTLEROCKET_x86_64"
      block_device_mappings = {
        root = {
          device_name = "/dev/xvdb"
          ebs = {
            volume_size = 100
            volume_type = "gp3"
            encrypted   = true
            kms_key_id  = aws_kms_key.data.arn
          }
        }
      }
    }
  }
}

locals {
  oidc_issuer = replace(module.eks.cluster_oidc_issuer_url, "https://", "")
}

data "aws_iam_policy_document" "irsa" {
  for_each = {
    external_secrets = "external-secrets"
    vault            = "vault"
    ebs_csi          = "kube-system:ebs-csi-controller-sa"
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer}:sub"
      values = [
        length(split(":", each.value)) == 2
        ? "system:serviceaccount:${each.value}"
        : "system:serviceaccount:${var.k8s_namespace}:${each.value}"
      ]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

# Matches the ServiceAccount annotation in infra/k8s/overlays/prod/external-secrets.yaml.
resource "aws_iam_role" "external_secrets" {
  name               = "${local.prefix}-external-secrets"
  assume_role_policy = data.aws_iam_policy_document.irsa["external_secrets"].json
}

resource "aws_iam_role_policy" "external_secrets" {
  role = aws_iam_role.external_secrets.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = [aws_secretsmanager_secret.app.arn, aws_secretsmanager_secret.keycloak.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.data.arn]
      }
    ]
  })
}

resource "aws_iam_role" "vault" {
  name               = "${local.prefix}-vault"
  assume_role_policy = data.aws_iam_policy_document.irsa["vault"].json
}

resource "aws_iam_role_policy" "vault" {
  role = aws_iam_role.vault.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["kms:Encrypt", "kms:Decrypt", "kms:DescribeKey"]
      Resource = [aws_kms_key.vault_unseal.arn]
    }]
  })
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${local.prefix}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.irsa["ebs_csi"].json
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_iam_role_policy" "ebs_csi_kms" {
  role = aws_iam_role.ebs_csi.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["kms:CreateGrant", "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey*", "kms:DescribeKey", "kms:ReEncrypt*"]
      Resource = [aws_kms_key.data.arn]
    }]
  })
}
