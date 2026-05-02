# ---------------------------------------------------------------------------
# Azure Machine Learning workspace + serverless compute for training & eval
# ---------------------------------------------------------------------------

# AML requires a storage account, key vault, and app insights.

resource "azurerm_storage_account" "aml" {
  name                     = "stcodereconlab"
  location                 = azurerm_resource_group.lab.location
  resource_group_name      = azurerm_resource_group.lab.name
  account_tier             = "Standard"
  account_replication_type = "LRS"

  tags = var.tags
}

resource "azurerm_key_vault" "aml" {
  name                = "kv-coderecon-lab"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  purge_protection_enabled   = false
  soft_delete_retention_days = 7

  tags = var.tags
}

resource "azurerm_application_insights" "aml" {
  name                = "ai-coderecon-lab"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  application_type    = "web"

  tags = var.tags
}

# --- AML workspace ---

resource "azurerm_machine_learning_workspace" "lab" {
  name                    = var.aml_workspace_name
  location                = azurerm_resource_group.lab.location
  resource_group_name     = azurerm_resource_group.lab.name
  storage_account_id      = azurerm_storage_account.aml.id
  key_vault_id            = azurerm_key_vault.aml.id
  application_insights_id = azurerm_application_insights.aml.id

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

# --- Compute cluster for training (auto-scale 0→1, cost = $0 at idle) ---

resource "azurerm_machine_learning_compute_cluster" "train" {
  name                          = "train-cpu"
  machine_learning_workspace_id = azurerm_machine_learning_workspace.lab.id
  location                      = azurerm_resource_group.lab.location
  vm_priority                   = "LowPriority"
  vm_size                       = var.aml_train_vm_size

  scale_settings {
    min_node_count                       = 0
    max_node_count                       = 1
    scale_down_nodes_after_idle_duration  = "PT5M"
  }

  identity {
    type = "SystemAssigned"
  }
}

# --- Compute cluster for eval (heavier — needs coderecon + indexes) ---

resource "azurerm_machine_learning_compute_cluster" "eval" {
  name                          = "eval-cpu"
  machine_learning_workspace_id = azurerm_machine_learning_workspace.lab.id
  location                      = azurerm_resource_group.lab.location
  vm_priority                   = "LowPriority"
  vm_size                       = var.aml_eval_vm_size

  scale_settings {
    min_node_count                       = 0
    max_node_count                       = 1
    scale_down_nodes_after_idle_duration  = "PT5M"
  }

  identity {
    type = "SystemAssigned"
  }
}

# --- GPU compute cluster for indexing (SPLADE vectorization via ONNX) ---

resource "azurerm_machine_learning_compute_cluster" "index_gpu" {
  name                          = "index-gpu"
  machine_learning_workspace_id = azurerm_machine_learning_workspace.lab.id
  location                      = azurerm_resource_group.lab.location
  vm_priority                   = "LowPriority"
  vm_size                       = var.aml_index_gpu_vm_size

  scale_settings {
    min_node_count                       = 0
    max_node_count                       = var.aml_index_gpu_max_nodes
    scale_down_nodes_after_idle_duration  = "PT5M"
  }

  identity {
    type = "SystemAssigned"
  }
}

# --- Data asset: reference the merged signal data in blob storage ---

data "azurerm_client_config" "current" {}
