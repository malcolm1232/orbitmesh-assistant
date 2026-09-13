variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "asia-southeast1"
}
variable "service_name" {
  type    = string
  default = "orbitmesh-assistant"
}
variable "image" {
  description = "Fully qualified image, e.g. asia-southeast1-docker.pkg.dev/PROJECT/orbitmesh/assistant:TAG"
  type        = string
}
variable "llm_model" {
  type    = string
  default = "openai/gpt-4.1-mini"
}
variable "alert_email" {
  description = "Where Cloud Monitoring sends alerts (empty = no notification channel)"
  type        = string
  default     = ""
}
variable "allow_unauthenticated" {
  description = "Public demo endpoint. Set false and front with IAP / an API gateway for real customer traffic."
  type        = bool
  default     = true
}
variable "openrouter_api_key" {
  description = "OpenRouter key. Supplied via a gitignored tfvars / TF_VAR_openrouter_api_key; empty = manage the secret version out-of-band."
  type        = string
  default     = ""
  sensitive   = true
}
