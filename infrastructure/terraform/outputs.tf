output "repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "bucket_name" {
  value = aws_s3_bucket.packages.id
}

output "api_url" {
  value = local.deploy ? aws_apigatewayv2_api.app[0].api_endpoint : null
}
