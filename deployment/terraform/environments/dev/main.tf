locals {
  tags = {
    Project     = "ai-data-workspace"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# Networking is OFF by default. It can be enabled only after target account,
# VPC CIDRs, AZ availability, permissions and costs have been reviewed.
resource "aws_route_table" "private" {
  count  = var.enable_network ? 1 : 0
  vpc_id = var.vpc_id
  tags   = merge(local.tags, { Name = "ai-workspace-private-rt" })
  # A newly created table has only its local VPC route.
  # Deliberately no NAT or internet gateway routes.
}

resource "aws_subnet" "private" {
  for_each                = var.enable_network ? var.private_subnets : {}
  vpc_id                  = var.vpc_id
  availability_zone       = each.value.availability_zone
  cidr_block              = each.value.cidr_block
  map_public_ip_on_launch = false
  tags                    = merge(local.tags, { Name = "ai-workspace-private-${each.key}" })
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private[0].id
}
