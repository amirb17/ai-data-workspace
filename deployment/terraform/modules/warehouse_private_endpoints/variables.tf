variable "vpc_id" {
  type        = string
  description = "Existing verified VPC"
  default     = "vpc-0235c4ff54488355c"
  validation {
    condition     = var.vpc_id == "vpc-0235c4ff54488355c"
    error_message = "Only the inspected DataRise VPC is permitted."
  }
}

variable "region" {
  type        = string
  default     = "ap-southeast-2"
  description = "Sydney region of the verified VPC"
  validation {
    condition     = var.region == "ap-southeast-2"
    error_message = "Only ap-southeast-2 is permitted."
  }
}

variable "private_subnet_ids" {
  type        = list(string)
  default     = ["subnet-07e43e5559f36b21c", "subnet-044eb7e6626f02bbf"]
  description = "Exact verified non-public two-AZ subnet pair"
  validation {
    condition = (
      length(var.private_subnet_ids) == 2 &&
      length(setsubtract(
        toset(var.private_subnet_ids),
        toset(["subnet-07e43e5559f36b21c", "subnet-044eb7e6626f02bbf"])
      )) == 0
    )
    error_message = "Only the inspected two private subnets are permitted."
  }
}

variable "worker_security_group_id" {
  type        = string
  description = "Private warehouse worker SG to admit, after review (not the public API SG)"
  validation {
    condition     = can(regex("^sg-[0-9a-f]{8,17}$", var.worker_security_group_id))
    error_message = "Supply a reviewed warehouse worker security group ID."
  }
}
