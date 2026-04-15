output "resource_group_name" {
  value = azurerm_resource_group.lab.name
}

output "ai_services_endpoint" {
  description = "Azure AI Services endpoint URL. Set as AZURE_OPENAI_ENDPOINT."
  value       = azurerm_cognitive_account.ai.endpoint
}

output "ai_services_name" {
  value = azurerm_cognitive_account.ai.name
}

output "gpt41_mini_deployment" {
  description = "Deployment name for gpt-4.1-mini. Use as the model/deployment parameter."
  value       = azurerm_cognitive_deployment.gpt41_mini.name
}

# --- AML outputs ---

output "aml_workspace_name" {
  value = azurerm_machine_learning_workspace.lab.name
}

output "aml_workspace_id" {
  value = azurerm_machine_learning_workspace.lab.id
}

output "aml_storage_account" {
  value = azurerm_storage_account.aml.name
}
