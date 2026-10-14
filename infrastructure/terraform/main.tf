data "aws_caller_identity" "current" {}

locals {
  deploy = var.image_digest != null
  bucket = "${var.name}-${data.aws_caller_identity.current.account_id}-${var.region}"
}

resource "aws_ecr_repository" "app" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_s3_bucket" "packages" {
  bucket        = local.bucket
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "packages" {
  bucket                  = aws_s3_bucket.packages.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "packages" {
  bucket = aws_s3_bucket.packages.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "packages" {
  bucket = aws_s3_bucket.packages.id

  rule {
    id     = "expire-session-packages"
    status = "Enabled"
    filter {
      prefix = "sessions/"
    }
    expiration {
      days = var.retention_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.packages.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.packages.arn, "${aws_s3_bucket.packages.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_cloudwatch_log_group" "app" {
  count             = local.deploy ? 1 : 0
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 14
}

resource "aws_iam_role" "app" {
  count = local.deploy ? 1 : 0
  name  = "${var.name}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "app" {
  count = local.deploy ? 1 : 0
  role  = aws_iam_role.app[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.app[0].arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.packages.arn}/sessions/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.secret_arn
      }
      ], var.secret_kms_key_arn == null ? [] : [{
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.secret_kms_key_arn
        Condition = {
          StringEquals = { "kms:ViaService" = "secretsmanager.${var.region}.amazonaws.com" }
        }
    }])
  })
}

resource "aws_lambda_function" "app" {
  count                          = local.deploy ? 1 : 0
  function_name                  = var.name
  role                           = aws_iam_role.app[0].arn
  package_type                   = "Image"
  image_uri                      = "${aws_ecr_repository.app.repository_url}@${var.image_digest}"
  architectures                  = ["x86_64"]
  memory_size                    = 2048
  timeout                        = 28
  reserved_concurrent_executions = 10

  environment {
    variables = {
      CTX_BUCKET     = aws_s3_bucket.packages.id
      CTX_REGION     = var.region
      CTX_QDRANT_URL = var.qdrant_url
      CTX_SECRET_ARN = var.secret_arn
    }
  }

  lifecycle {
    precondition {
      condition     = var.secret_arn != null && var.qdrant_url != null
      error_message = "Deployment needs an existing secret_arn and qdrant_url."
    }
  }

  depends_on = [
    aws_iam_role_policy.app,
    aws_s3_bucket_server_side_encryption_configuration.packages,
    aws_s3_bucket_public_access_block.packages,
  ]
}

resource "aws_apigatewayv2_api" "app" {
  count         = local.deploy ? 1 : 0
  name          = var.name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "app" {
  count                  = local.deploy ? 1 : 0
  api_id                 = aws_apigatewayv2_api.app[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.app[0].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "app" {
  count     = local.deploy ? 1 : 0
  api_id    = aws_apigatewayv2_api.app[0].id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.app[0].id}"
}

resource "aws_apigatewayv2_stage" "app" {
  count       = local.deploy ? 1 : 0
  api_id      = aws_apigatewayv2_api.app[0].id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
}

resource "aws_lambda_permission" "gateway" {
  count         = local.deploy ? 1 : 0
  statement_id  = "AllowGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app[0].execution_arn}/*/*"
}
