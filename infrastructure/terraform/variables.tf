variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "session-optimizer"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,39}$", var.name))
    error_message = "Use 3-40 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "image_digest" {
  type        = string
  default     = null
  description = "Immutable ECR image digest. Leave null to bootstrap the repository and bucket."

  validation {
    condition     = var.image_digest == null ? true : can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
    error_message = "Use an immutable sha256 ECR digest."
  }
}

variable "qdrant_url" {
  type        = string
  default     = null
  description = "Existing Qdrant HTTPS endpoint; Qdrant Cloud is managed separately."

  validation {
    condition     = var.qdrant_url == null ? true : can(regex("^https://[^/]+/?$", var.qdrant_url))
    error_message = "Use an HTTPS Qdrant endpoint without a path."
  }
}

variable "secret_arn" {
  type        = string
  default     = null
  description = "Existing Secrets Manager secret containing api_token and qdrant_api_key."
}

variable "secret_kms_key_arn" {
  type        = string
  default     = null
  description = "Only required when the existing secret uses a customer-managed KMS key."
}

variable "retention_days" {
  type        = number
  default     = 90
  description = "S3 package expiry. Delete through the API to remove vectors too."

  validation {
    condition     = var.retention_days >= 1 && floor(var.retention_days) == var.retention_days
    error_message = "Retention must be a positive integer."
  }
}
