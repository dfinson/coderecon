resource "azurerm_cognitive_account" "ai" {
  name                = var.ai_services_name
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  kind                = "AIServices"
  sku_name            = "S0"

  custom_subdomain_name = var.ai_services_name

  tags = var.tags
}

resource "azurerm_cognitive_deployment" "gpt41_mini" {
  name                 = "gpt-4.1-mini"
  cognitive_account_id = azurerm_cognitive_account.ai.id

  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }

  sku {
    name     = "Standard"
    capacity = var.gpt41_mini_capacity
  }
}
