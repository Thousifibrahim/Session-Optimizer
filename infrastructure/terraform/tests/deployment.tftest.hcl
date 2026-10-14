mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

run "bootstrap" {
  command = plan

  assert {
    condition     = length(aws_lambda_function.app) == 0 && length(aws_apigatewayv2_api.app) == 0
    error_message = "Bootstrap must not create compute before an image is pushed."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.packages.block_public_acls && aws_s3_bucket_public_access_block.packages.block_public_policy && aws_s3_bucket_public_access_block.packages.ignore_public_acls && aws_s3_bucket_public_access_block.packages.restrict_public_buckets
    error_message = "Package storage must block all public access."
  }
  assert {
    condition     = aws_ecr_repository.app.image_tag_mutability == "IMMUTABLE"
    error_message = "Deployment images must be immutable."
  }
}

run "deploy" {
  command = plan

  variables {
    image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    qdrant_url   = "https://qdrant.example"
    secret_arn   = "arn:aws:secretsmanager:us-east-1:123456789012:secret:ctx-test"
  }

  assert {
    condition     = length(aws_lambda_function.app) == 1 && length(aws_apigatewayv2_api.app) == 1
    error_message = "A configured image must create the serverless endpoint."
  }
  assert {
    condition     = aws_lambda_function.app[0].timeout * 1000 < aws_apigatewayv2_integration.app[0].timeout_milliseconds
    error_message = "Lambda must finish before the synchronous HTTP integration times out."
  }
  assert {
    condition     = !contains(keys(aws_lambda_function.app[0].environment[0].variables), "CTX_API_TOKEN")
    error_message = "Terraform must never put bearer secret values into Lambda environment/state."
  }
}

run "reject_mutable_image" {
  command = plan
  variables {
    image_digest = "latest"
  }
  expect_failures = [var.image_digest]
}
