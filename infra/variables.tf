variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
  default     = "eastus2"
}

variable "resource_group_name" {
  description = "Name of the resource group to create."
  type        = string
  default     = "rg-coderecon-lab"
}

variable "ai_services_name" {
  description = "Name of the Azure AI Services (Foundry) account."
  type        = string
  default     = "coderecon-lab-ai"
}

variable "gpt41_mini_capacity" {
  description = "Token-per-minute capacity (in thousands) for gpt-4.1-mini."
  type        = number
  default     = 50
}

variable "aml_workspace_name" {
  description = "Name of the Azure ML workspace."
  type        = string
  default     = "mlw-coderecon-lab"
}

variable "aml_train_vm_size" {
  description = "VM size for training compute cluster."
  type        = string
  default     = "Standard_D4s_v3" # 4 vCPU, 16 GB — enough for LightGBM
}

variable "aml_eval_vm_size" {
  description = "VM size for eval compute cluster (needs more RAM for indexes)."
  type        = string
  default     = "Standard_E4s_v3" # 4 vCPU, 32 GB — memory-optimized
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default = {
    project     = "coderecon"
    environment = "lab"
    purpose     = "swebench-query-generation"
  }
}
