# Pod Identity roles for cluster addons and the KEDA baseline.

locals {
  pod_identity_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

# EBS CSI driver: Postgres PVC and anything else that needs a disk.
resource "aws_iam_role" "ebs_csi" {
  name               = "${var.name}-ebs-csi"
  assume_role_policy = local.pod_identity_trust
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

# KEDA operator: reads SQS queue depth to scale the baseline ScaledJob.
resource "aws_iam_role" "keda" {
  name               = "${var.name}-keda-operator"
  assume_role_policy = local.pod_identity_trust
}

resource "aws_iam_role_policy" "keda" {
  role = aws_iam_role.keda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:GetQueueAttributes"]
      Resource = aws_sqs_queue.tasks.arn
    }]
  })
}

resource "aws_eks_pod_identity_association" "keda" {
  cluster_name    = module.eks.cluster_name
  namespace       = "keda"
  service_account = "keda-operator"
  role_arn        = aws_iam_role.keda.arn
}

# Baseline only: the KEDA-spawned Job has to pull its own message from SQS, which means the
# untrusted pod holds AWS credentials. That is exactly the reason the controller exists.
resource "aws_iam_role" "baseline_job" {
  name               = "${var.name}-baseline-job"
  assume_role_policy = local.pod_identity_trust
}

resource "aws_iam_role_policy" "baseline_job" {
  role = aws_iam_role.baseline_job.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility"]
      Resource = aws_sqs_queue.tasks.arn
    }]
  })
}

resource "aws_eks_pod_identity_association" "baseline_job" {
  cluster_name    = module.eks.cluster_name
  namespace       = "pr-runtime"
  service_account = "baseline-job"
  role_arn        = aws_iam_role.baseline_job.arn
}
