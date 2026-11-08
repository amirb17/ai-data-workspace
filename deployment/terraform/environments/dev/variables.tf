variable "aws_region" {
  description = "AWS deployment region"
  type        = string
  default     = "ap-southeast-2"
}

variable "environment" {
  description = "Environment tag."
  type        = string
  default     = "dev"
}

variable "enable_network" {
  description = "Safety gate: false creates no networking resources."
  type        = bool
  default     = false
}

variable "vpc_id" {
  description = "VPC ID in the TARGET AWS account, not the old account."
  type        = string
  default     = ""
}

variable "private_subnets" {
  description = "Two non-overlapping free CIDRs, keyed by AZ suffix. Verify target VPC first."
  type = map(object({
    availability_zone = string
    cidr_block        = string
  }))
  default = {}
}
variable "enable_processing_queue" {
  description = "Create SQS processing queues and worker IAM role"
  type        = bool
  default     = false
}
variable "enable_lambda_worker" {
  description = "Provision the Lambda processing worker"
  type        = bool
  default     = false
}

variable "lambda_package_path" {
  description = "Path to the built Lambda deployment ZIP"
  type        = string
  default     = ""
}
variable "enable_lambda_trigger" {
  description = "Allow Lambda to consume the processing queue"
  type        = bool
  default     = false

  validation {
    condition     = !var.enable_lambda_trigger || var.enable_lambda_worker
    error_message = "The Lambda trigger requires enable_lambda_worker = true."
  }
}
variable "enable_ec2_worker" {
  description = "Enable EC2 worker infrastructure"
  type        = bool
  default     = false
}

variable "artifact_bucket_name" {
  description = "S3 bucket belonging to the deployment account"
  type        = string
  default     = ""
}

variable "worker_db_secret_arn" {
  description = "ARN of worker-only PostgreSQL secret"
  type        = string
  default     = ""
}

variable "verifier_lambda_arn" {
  description = "ARN of the separate trusted verifier Lambda"
  type        = string
  default     = ""
}
variable "worker_ami_id" {
  description = "Reviewed Linux AMI ID in the deployment region"
  type        = string
  default     = ""
}

variable "worker_subnet_id" {
  description = "Worker subnet ID in the deployment account"
  type        = string
  default     = ""
}

variable "worker_instance_type" {
  description = "Small development EC2 instance"
  type        = string
  default     = "t3.micro"
}

variable "worker_image_uri" {
  description = "Immutable ECR image reference for the worker"
  type        = string
  default     = ""
}

variable "worker_image_digest" {
  description = "Reviewed SHA-256 digest of the deployed worker image"
  type        = string
  default     = ""

  validation {
    condition = (
      var.worker_image_digest == "" ||
      can(regex("^sha256:[0-9a-f]{64}$", var.worker_image_digest))
    )
    error_message = "Worker image digest must be sha256 followed by 64 lowercase hex characters."
  }
}

variable "worker_aws_cli_region" {
  description = "Region used by the worker to access AWS services"
  type        = string
  default     = "ap-southeast-2"
}
variable "enable_verifier_lambda" {
  description = "Provision the isolated trusted verifier Lambda"
  type        = bool
  default     = false
}

variable "verifier_image_uri" {
  description = "Immutable ECR image URI for the verifier"
  type        = string
  default     = ""
}

variable "verifier_db_secret_arn" {
  description = "Verifier-only PostgreSQL secret ARN"
  type        = string
  default     = ""
}
variable "enable_artifact_bucket" {
  description = "Create a new versioned artifact bucket"
  type        = bool
  default     = false
}

variable "enable_verifier_ecr" {
  description = "Create the verifier container registry"
  type        = bool
  default     = false
}