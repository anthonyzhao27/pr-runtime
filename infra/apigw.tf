# GitHub webhook -> API Gateway (HTTP API) -> Lambda (HMAC verify) -> SQS.
# Nothing inside the cluster is public. The signature is checked before anything touches the queue.
#
# Why Lambda and not API Gateway's direct SQS integration: that integration cannot substitute
# request headers inside the MessageAttributes JSON, so the HMAC signature could not be forwarded
# to the controller for verification. See docs/DECISIONS.md.

locals {
  dotenv = { for line in split("\n", file("${path.module}/../.env")) :
    trimspace(split("=", line)[0]) => trimspace(join("=", slice(split("=", line), 1, length(split("=", line)))))
    if length(trimspace(line)) > 0 && !startswith(trimspace(line), "#") && can(regex("=", line))
  }
}

data "archive_file" "ingress" {
  type        = "zip"
  source_file = "${path.module}/lambda/ingress.py"
  output_path = "${path.module}/.terraform/ingress.zip"
}

resource "aws_iam_role" "ingress" {
  name = "${var.name}-ingress-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ingress_logs" {
  role       = aws_iam_role.ingress.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "ingress_sqs" {
  role = aws_iam_role.ingress.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.tasks.arn
    }]
  })
}

resource "aws_lambda_function" "ingress" {
  function_name    = "${var.name}-webhook-ingress"
  role             = aws_iam_role.ingress.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "ingress.handler"
  filename         = data.archive_file.ingress.output_path
  source_code_hash = data.archive_file.ingress.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      QUEUE_URL      = aws_sqs_queue.tasks.url
      WEBHOOK_SECRET = local.dotenv["WEBHOOK_SECRET"]
    }
  }
}

resource "aws_apigatewayv2_api" "webhook" {
  name          = "${var.name}-webhook"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "ingress" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ingress.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingress.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}

resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.ingress.id}"
}

resource "aws_cloudwatch_log_group" "apigw" {
  name              = "/aws/apigw/${var.name}-webhook"
  retention_in_days = 7
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 200
    throttling_rate_limit  = 100
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.apigw.arn
    format = jsonencode({
      requestId         = "$context.requestId"
      status            = "$context.status"
      routeKey          = "$context.routeKey"
      integrationError  = "$context.integrationErrorMessage"
      integrationStatus = "$context.integration.status"
      error             = "$context.error.message"
    })
  }
}
