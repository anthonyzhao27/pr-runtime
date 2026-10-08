output "cluster_name" {
  value = module.eks.cluster_name
}

output "update_kubeconfig" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region} --profile ${var.aws_profile}"
}

output "webhook_url" {
  value = "${aws_apigatewayv2_stage.default.invoke_url}/webhook"
}

output "queue_url" {
  value = aws_sqs_queue.tasks.url
}

output "ecr_controller" {
  value = aws_ecr_repository.controller.repository_url
}

output "ecr_runner" {
  value = aws_ecr_repository.runner.repository_url
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

data "aws_caller_identity" "current" {}
