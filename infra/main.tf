terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "job-agent"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name = "job-agent"
}

# ---------------------------------------------------------------------------
# DynamoDB — replaces SQLite. PAY_PER_REQUEST means no provisioned capacity
# to leave running and forget about: you pay per read/write, and this
# workload sits inside the perpetual free allowance.
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "jobs" {
  name         = "${local.name}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "status"
    type = "S"
  }
  attribute {
    name = "score"
    type = "N"
  }

  # Lets the agent fetch the shortlist ordered by score without scanning
  # the whole table.
  global_secondary_index {
    name            = "status-score-index"
    hash_key        = "status"
    range_key       = "score"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = false # not worth it for reproducible data
  }
}

# ---------------------------------------------------------------------------
# Secrets — SSM Parameter Store standard tier is free, unlike Secrets
# Manager which bills per secret per month. Values are set outside
# Terraform so they never enter the state file.
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "anthropic_key" {
  name  = "/${local.name}/ANTHROPIC_API_KEY"
  type  = "SecureString"
  value = "PLACEHOLDER_SET_VIA_CLI"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "adzuna_id" {
  name  = "/${local.name}/ADZUNA_APP_ID"
  type  = "SecureString"
  value = "PLACEHOLDER_SET_VIA_CLI"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "adzuna_key" {
  name  = "/${local.name}/ADZUNA_APP_KEY"
  type  = "SecureString"
  value = "PLACEHOLDER_SET_VIA_CLI"

  lifecycle {
    ignore_changes = [value]
  }
}

# ---------------------------------------------------------------------------
# IAM — least privilege. The Lambda can touch exactly one table, the
# parameters under its own prefix, and send mail as one verified address.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  name = "${local.name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda" {
  name = "${local.name}-lambda-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:BatchGetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:Scan",
        ]
        Resource = [
          aws_dynamodb_table.jobs.arn,
          "${aws_dynamodb_table.jobs.arn}/index/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${local.name}", "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${local.name}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com" }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource = "*"
        Condition = {
          StringEquals = { "ses:FromAddress" = var.email }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "agent" {
  function_name = local.name
  role          = aws_iam_role.lambda.arn
  handler       = "lambda_handler.handler"
  runtime       = "python3.13"

  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)

  # The agent makes many sequential model calls; it needs room to finish.
  timeout     = 600
  memory_size = 512

  environment {
    variables = {
      JOBS_TABLE    = aws_dynamodb_table.jobs.name
      PARAM_PREFIX  = "/${local.name}"
      SES_SENDER    = var.email
      SES_RECIPIENT = var.email
      AGENT_MODEL   = var.agent_model
      LOG_LEVEL     = "INFO"
    }
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.agent.function_name}"
  retention_in_days = 14 # logs are not free forever; 14 days is plenty
}

# ---------------------------------------------------------------------------
# Schedule — weekdays at 07:00 Europe/Paris. EventBridge Scheduler handles
# the timezone and daylight saving itself, which a plain cron rule does not.
# ---------------------------------------------------------------------------

resource "aws_scheduler_schedule" "daily" {
  name                = "${local.name}-daily"
  schedule_expression = "cron(0 7 ? * MON-FRI *)"
  schedule_expression_timezone = "Europe/Paris"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.agent.arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name = "${local.name}-scheduler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${local.name}-scheduler-policy"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.agent.arn
    }]
  })
}

# ---------------------------------------------------------------------------
# SES — verifying the address puts it on the allowed sender AND recipient
# list. The account starts in the SES sandbox, which only permits sending
# to verified addresses. That is fine here: the agent emails only you.
# ---------------------------------------------------------------------------

resource "aws_ses_email_identity" "me" {
  email = var.email
}
