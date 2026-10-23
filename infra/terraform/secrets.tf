resource "random_password" "internal_token" {
  length  = 48
  special = false
}

resource "random_password" "keycloak_admin" {
  length  = 32
  special = false
}

# Consumed by ExternalSecret "omnimed-secrets" (infra/k8s/overlays/prod/external-secrets.yaml).
resource "aws_secretsmanager_secret" "app" {
  name       = "${var.name}/${var.environment}/app"
  kms_key_id = aws_kms_key.data.arn
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL     = "postgres://${aws_db_instance.postgres.username}:${random_password.db.result}@${aws_db_instance.postgres.address}:5432/omnimed?sslmode=require"
    REDIS_URL        = "rediss://:${random_password.redis.result}@${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0"
    INTERNAL_TOKEN   = random_password.internal_token.result
    MINIO_ACCESS_KEY = aws_iam_access_key.app_s3.id
    MINIO_SECRET_KEY = aws_iam_access_key.app_s3.secret
    VAULT_TOKEN      = "set-out-of-band"
    LLM_API_KEY      = ""
    AUDIT_BUCKET     = aws_s3_bucket.this["audit"].bucket
    REPORTS_BUCKET   = aws_s3_bucket.this["reports"].bucket
    PKI_BUCKET       = aws_s3_bucket.this["pki"].bucket
    VOLUME_BUCKET    = aws_s3_bucket.this["dicom"].bucket
  })

  # VAULT_TOKEN and LLM_API_KEY are rotated by operators directly in Secrets Manager.
  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "keycloak" {
  name       = "${var.name}/${var.environment}/keycloak-admin"
  kms_key_id = aws_kms_key.data.arn
}

resource "aws_secretsmanager_secret_version" "keycloak" {
  secret_id = aws_secretsmanager_secret.keycloak.id
  secret_string = jsonencode({
    username    = "admin"
    password    = random_password.keycloak_admin.result
    db_url      = "jdbc:postgresql://${aws_db_instance.postgres.address}:5432/keycloak?sslmode=require"
    db_username = aws_db_instance.postgres.username
    db_password = random_password.db.result
  })
}
