"""Static guard for default-off Ask AI Terraform bindings.

Run: python tests/test_ask_ai_terraform_contract.py
"""
from pathlib import Path
source = Path("deployment/terraform/environments/dev/api_ecs_task.tf").read_text()
assert 'variable "api_enable_ask_ai" {' in source
assert 'default     = false' in source.split('variable "api_enable_ask_ai" {',1)[1].split('variable "api_gemini_secret_arn"',1)[0]
assert 'var.api_enable_processing_jobs' in source
assert 'var.api_enable_model_read' in source
assert 'var.api_enable_analytics_read' in source
assert 'DATARISE_PUBLIC_ASK_AI' in source
assert 'GEMINI_API_KEY' in source
assert 'valueFrom = var.api_gemini_secret_arn' in source
assert 'var.api_enable_ask_ai ? [var.api_gemini_secret_arn] : []' in source
assert 'api_execution_secrets' in source
assert 'secret:datarise/dev/gemini-api-key-' in source
print("Ask AI Terraform contract passed")
