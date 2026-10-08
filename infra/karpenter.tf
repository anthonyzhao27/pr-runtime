# S3: Karpenter for burst capacity. Fixed node group stays as the always-on floor; Karpenter adds
# Graviton spot/on-demand nodes when runner pods are unschedulable and consolidates them away afterwards.

module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "~> 21.0"

  cluster_name = module.eks.cluster_name

  namespace       = "kube-system"
  service_account = "karpenter"

  create_pod_identity_association = true
  enable_spot_termination         = true # SQS queue + EventBridge rules for spot interruption / rebalance / health events

  node_iam_role_use_name_prefix = false
  node_iam_role_name            = "${var.name}-karpenter-node"
  node_iam_role_additional_policies = {
    AmazonSSMManagedInstanceCore = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  }
}

# Discovery tags so EC2NodeClass can find the subnets and the node security group.
resource "aws_ec2_tag" "karpenter_subnets" {
  for_each    = toset(module.vpc.private_subnets)
  resource_id = each.value
  key         = "karpenter.sh/discovery"
  value       = module.eks.cluster_name
}

resource "aws_ec2_tag" "karpenter_node_sg" {
  resource_id = module.eks.node_security_group_id
  key         = "karpenter.sh/discovery"
  value       = module.eks.cluster_name
}

output "karpenter_queue_name" {
  value = module.karpenter.queue_name
}

output "karpenter_node_role" {
  value = module.karpenter.node_iam_role_name
}
