variable "region" {
  description = "AWS region. Paris keeps the data close and the latency low."
  type        = string
  default     = "eu-west-3"
}

variable "email" {
  description = "Verified SES address. Sends and receives the daily digest."
  type        = string
}

variable "package_path" {
  description = "Path to the built Lambda zip (see build_package.py)."
  type        = string
  default     = "../build/lambda.zip"
}

variable "agent_model" {
  description = "Claude model the agent runs on."
  type        = string
  default     = "claude-haiku-4-5"
}
