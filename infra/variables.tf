variable "region" {
  type    = string
  default = "us-east-1"
}

variable "aws_profile" {
  type    = string
  default = "personal"
}

variable "name" {
  type    = string
  default = "pr-runtime"
}

variable "kubernetes_version" {
  type    = string
  default = "1.34"
}

variable "node_instance_type" {
  description = "Graviton; images are built natively on an arm64 laptop."
  type        = string
  default     = "m7g.large"
}

variable "node_count" {
  type    = number
  default = 3
}

variable "node_max" {
  type    = number
  default = 6
}
