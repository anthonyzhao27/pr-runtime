resource "aws_ecr_repository" "controller" {
  name                 = "${var.name}/controller"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = false }
}

resource "aws_ecr_repository" "runner" {
  name                 = "${var.name}/runner"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = false }
}

resource "aws_ecr_repository" "tools" {
  name                 = "${var.name}/tools"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = false }
}
