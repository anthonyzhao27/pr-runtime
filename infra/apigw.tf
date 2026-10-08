# GitHub webhook -> API Gateway (HTTP API) -> SQS. Nothing inside the cluster is public.
# The HMAC signature header is forwarded as a message attribute and verified by the controller.

resource "aws_iam_role" "apigw_sqs" {
  name = "${var.name}-apigw-sqs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "apigw_sqs" {
  role = aws_iam_role.apigw_sqs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.tasks.arn
    }]
  })
}

resource "aws_apigatewayv2_api" "webhook" {
  name          = "${var.name}-webhook"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "sqs" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_subtype    = "SQS-SendMessage"
  credentials_arn        = aws_iam_role.apigw_sqs.arn
  payload_format_version = "1.0"

  request_parameters = {
    QueueUrl    = aws_sqs_queue.tasks.url
    MessageBody = "$request.body"
    MessageAttributes = jsonencode({
      signature = { DataType = "String", StringValue = "$request.header.X-Hub-Signature-256" }
      event     = { DataType = "String", StringValue = "$request.header.X-GitHub-Event" }
      delivery  = { DataType = "String", StringValue = "$request.header.X-GitHub-Delivery" }
    })
  }
}

resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.sqs.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 200
    throttling_rate_limit  = 100
  }
}
