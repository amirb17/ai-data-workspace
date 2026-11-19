variable "aws_region" {
  type        = string
  description = "Reviewed region of the target ECS cluster"
}

variable "name_prefix" {
  type        = string
  description = "Short IAM/ECS name prefix (e.g. datarise-wh-dev)"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,25}$", var.name_prefix))
    error_message = "Use a stable lowercase hyphenated service prefix."
  }
}

variable "vpc_id" {
  type = string
}

variable "cluster_arn" {
  type = string
}

variable "api_security_group_id" {
  type        = string
  description = "Security group attached to the existing API ECS task"
}

variable "database_security_group_id" {
  type        = string
  description = "Security group accepting traffic on PostgreSQL port 5432"
}

variable "efs_security_group_id" {
  type        = string
  description = "Security group of the existing TLS-material EFS mount targets"
}

variable "vpc_endpoint_security_group_id" {
  type        = string
  description = "Security group on private ECR API/DKR, Secrets Manager and CloudWatch Logs endpoints"
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "At least two reviewed private subnets, with required VPC endpoints"
  validation {
    condition     = length(distinct(var.private_subnet_ids)) >= 2
    error_message = "At least two distinct private subnets are required."
  }
}

variable "image_digest_uri" {
  type        = string
  description = "Immutable worker ECR image URI; must contain Python and this exact reviewed code"
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.image_digest_uri))
    error_message = "Pin image by @sha256 digest, never a mutable tag."
  }
}

variable "image_repository_arn" {
  type        = string
  description = "Single ECR repository ARN hosting the reviewed immutable image"
}

variable "duties" {
  description = "Unique per-duty identity secrets and readonly TLS access point; no generic shared worker credentials"
  type = map(object({
    db_dsn_secret_arn   = string
    rpc_secret_arn     = string
    efs_file_system_id = string
    efs_file_system_arn = string
    efs_access_point_id = string
    efs_access_point_arn = string
  }))

  validation {
    condition = (
      length(var.duties) == 4 &&
      length(setsubtract(
        toset(keys(var.duties)),
        toset(["provider_submission", "recovery_attestation", "accounting", "result_authorization"])
      )) == 0
    )
    error_message = "Exactly the four independent worker duties must be supplied."
  }

  validation {
    condition = (
      length(distinct([for _, d in var.duties : d.db_dsn_secret_arn])) == 4 &&
      length(distinct([for _, d in var.duties : d.rpc_secret_arn])) == 4 &&
      length(distinct([for _, d in var.duties : d.efs_access_point_arn])) == 4
    )
    error_message = "Each duty must have its own database, RPC and TLS access-point identities."
  }

  validation {
    condition = alltrue([
      for _, d in var.duties :
      can(regex("^arn:aws:secretsmanager:", d.db_dsn_secret_arn)) &&
      can(regex("^arn:aws:secretsmanager:", d.rpc_secret_arn)) &&
      can(regex("^arn:aws:elasticfilesystem:", d.efs_access_point_arn)) &&
      can(regex("^arn:aws:elasticfilesystem:", d.efs_file_system_arn))
    ])
    error_message = "Secrets and EFS resources must be passed by reviewed ARNs."
  }
}
