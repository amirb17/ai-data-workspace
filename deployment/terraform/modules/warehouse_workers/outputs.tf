output "worker_security_group_id" {
  description = "Pass to warehouse_private_endpoints.worker_security_group_id and allow inbound 443 on existing Secrets Manager SG"
  value       = aws_security_group.worker.id
}

output "private_worker_dns_names" {
  description = "Private Cloud Map DNS targets for mTLS RPC client configurations"
  value = {
    for duty, _ in local.duties :
    duty => "${local.service_names[duty]}.warehouse.internal"
  }
}
