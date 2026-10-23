output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "postgres_address" {
  value = aws_db_instance.postgres.address
}

output "redis_primary_endpoint" {
  value = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "buckets" {
  value = { for k, b in aws_s3_bucket.this : k => b.bucket }
}

output "external_secrets_role_arn" {
  description = "Set as eks.amazonaws.com/role-arn on the external-secrets ServiceAccount."
  value       = aws_iam_role.external_secrets.arn
}

output "vault_role_arn" {
  value = aws_iam_role.vault.arn
}

output "vault_unseal_key_id" {
  description = "seal \"awskms\" { kms_key_id = ... } in the Vault server config."
  value       = aws_kms_key.vault_unseal.key_id
}

output "secrets" {
  value = {
    app      = aws_secretsmanager_secret.app.name
    keycloak = aws_secretsmanager_secret.keycloak.name
  }
}
