# Driver box: where image builds and eval runs happen, so the laptop does not have to.
# arm64 (native image builds), private subnet, no SSH key, access via SSM only.

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_iam_role" "driver" {
  name = "${var.name}-driver"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "driver_ssm" {
  role       = aws_iam_role.driver.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "driver_ecr" {
  role       = aws_iam_role.driver.name
  policy_arn = "arn:aws:iam::aws:policy/EC2InstanceProfileForImageBuilderECRContainerBuilds"
}

resource "aws_iam_role_policy" "driver" {
  role = aws_iam_role.driver.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["eks:DescribeCluster", "eks:ListClusters"], Resource = "*" },
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.app.arn },
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken", "ecr-public:GetAuthorizationToken", "sts:GetServiceBearerToken"], Resource = "*" },
      { Effect = "Allow", Action = ["sqs:GetQueueAttributes"], Resource = aws_sqs_queue.tasks.arn },
    ]
  })
}

resource "aws_iam_instance_profile" "driver" {
  name = "${var.name}-driver"
  role = aws_iam_role.driver.name
}

# kubectl from the driver: cluster-admin via EKS access entry (no aws-auth ConfigMap edits).
resource "aws_eks_access_entry" "driver" {
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.driver.arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "driver" {
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.driver.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope { type = "cluster" }
  depends_on = [aws_eks_access_entry.driver]
}

resource "aws_security_group" "driver" {
  name   = "${var.name}-driver"
  vpc_id = module.vpc.vpc_id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "driver" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = "t4g.large"
  subnet_id              = module.vpc.private_subnets[0]
  vpc_security_group_ids = [aws_security_group.driver.id]
  iam_instance_profile   = aws_iam_instance_profile.driver.name

  root_block_device {
    volume_size = 60
    volume_type = "gp3"
  }

  metadata_options {
    http_tokens = "required"
  }

  user_data = <<-EOT
    #!/bin/bash
    set -eux
    dnf install -y docker git python3.11 jq make
    systemctl enable --now docker
    usermod -aG docker ec2-user
    # kubectl
    curl -sLo /usr/local/bin/kubectl "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/arm64/kubectl" && chmod +x /usr/local/bin/kubectl
    # helm
    curl -s https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    # uv (for the controller venv / eval tooling)
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
    # gh
    dnf install -y 'dnf-command(config-manager)'
    dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
    dnf install -y gh
    # repo + kubeconfig for ec2-user
    sudo -u ec2-user bash -c 'cd ~ && git clone --quiet https://github.com/anthonyzhao27/pr-runtime.git || true'
    sudo -u ec2-user aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region}
    echo "driver ready" > /var/tmp/driver-ready
  EOT

  tags = { Name = "${var.name}-driver" }
}

output "driver_instance_id" {
  value = aws_instance.driver.id
}

# Inside the VPC the EKS endpoint resolves to its private address; admit the driver to the cluster security group.
resource "aws_vpc_security_group_ingress_rule" "driver_to_cluster" {
  security_group_id            = module.eks.cluster_security_group_id
  referenced_security_group_id = aws_security_group.driver.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
  description                  = "driver box to EKS API"
}
