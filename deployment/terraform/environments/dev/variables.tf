variable "aws_region" {
  description = "AWS region for development."
  type        = string
  default     = "ap-south-1"
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