variable "name" {
  type    = string
  default = "omnimed"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "region" {
  description = "Data residency: PHI must stay in India (DPDP Act)."
  type        = string
  default     = "ap-south-1"
}

variable "availability_zones" {
  type    = list(string)
  default = ["ap-south-1a", "ap-south-1b", "ap-south-1c"]
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "eks_version" {
  type    = string
  default = "1.30"
}

variable "eks_public_access_cidrs" {
  description = "CIDRs allowed to reach the EKS API endpoint. Empty disables public access."
  type        = list(string)
  default     = []
}

variable "node_instance_types" {
  type    = list(string)
  default = ["m6i.xlarge"]
}

variable "node_min" {
  type    = number
  default = 3
}

variable "node_max" {
  type    = number
  default = 9
}

variable "db_instance_class" {
  type    = string
  default = "db.r6g.large"
}

variable "db_allocated_storage" {
  type    = number
  default = 100
}

variable "redis_node_type" {
  type    = string
  default = "cache.r6g.large"
}

variable "audit_retention_days" {
  description = "Object Lock COMPLIANCE retention for the audit ledger mirror."
  type        = number
  default     = 2555
}

variable "k8s_namespace" {
  type    = string
  default = "omnimed"
}
