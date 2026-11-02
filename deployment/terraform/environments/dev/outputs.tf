output "private_subnet_ids" {
  description = "New subnet IDs when networking is enabled."
  value       = { for k, subnet in aws_subnet.private : k => subnet.id }
}

output "private_route_table_id" {
  value = try(aws_route_table.private[0].id, null)
}
