# EKS Pod Identity for the controller: the only pod that talks to AWS.
# The runner gets no role and no service-account token at all.

resource "aws_iam_role" "controller" {
  name = "${var.name}-controller"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy" "controller" {
  role = aws_iam_role.controller.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility",
        "sqs:GetQueueAttributes",
      ]
      Resource = aws_sqs_queue.tasks.arn
    }]
  })
}

resource "aws_eks_pod_identity_association" "controller" {
  cluster_name    = module.eks.cluster_name
  namespace       = "pr-runtime"
  service_account = "controller"
  role_arn        = aws_iam_role.controller.arn
}
