# S9: application secrets in Secrets Manager, read by the controller at boot via Pod Identity.
# Values come from the local .env (gitignored). They end up in Terraform state, which is local-only here;
# in a team setup the state backend would be encrypted S3 and the values would be set out-of-band.

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/app"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    OPENAI_API_KEY    = local.dotenv["OPENAI_API_KEY"]
    GITHUB_BOT_TOKEN  = local.dotenv["GITHUB_BOT_TOKEN"]
    POSTGRES_PASSWORD = local.dotenv["POSTGRES_PASSWORD"]
    JUDGE_MODEL       = lookup(local.dotenv, "JUDGE_MODEL", "")
    # GitHub App (S4): the App signs JWTs with this key to mint short-lived installation tokens.
    GITHUB_APP_ID          = lookup(local.dotenv, "GITHUB_APP_ID", "")
    GITHUB_APP_PRIVATE_KEY = fileexists("${path.module}/../app-private-key.pem") ? file("${path.module}/../app-private-key.pem") : ""
  })
}

resource "aws_iam_role_policy" "controller_secrets" {
  role = aws_iam_role.controller.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.app.arn
    }]
  })
}

output "app_secret_id" {
  value = aws_secretsmanager_secret.app.name
}
