# Розгортання без SSH: GitHub Actions входить в AWS через OIDC (без збережених ключів)
# і надсилає команду на ВМ через AWS Systems Manager. ВМ не відкриває для цього жодного порту:
# агент SSM сам забирає команди вихідним з'єднанням.

locals {
  # "owner/repo" з repo_url, напр. https://github.com/owner/repo.git -> owner/repo
  github_repo = regex("github\\.com[/:]([^/]+/[^/.]+)", var.repo_url)[0]
}

# --- Роль ВМ: дозволяє агенту SSM отримувати й виконувати команди ---
data "aws_iam_policy_document" "vm_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "vm" {
  name               = "autoslot-vm"
  assume_role_policy = data.aws_iam_policy_document.vm_assume.json
}

resource "aws_iam_role_policy_attachment" "vm_ssm" {
  role       = aws_iam_role.vm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "vm" {
  name = "autoslot-vm"
  role = aws_iam_role.vm.name
}

# --- Довіра до GitHub: AWS приймає короткоживучі токени GitHub Actions ---
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

# --- Роль розгортання: лише цей репозиторій, лише гілка main ---
data "aws_iam_policy_document" "deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Два формати subject: класичний (repo:owner/repo:...) і незмінний з ID,
    # який GitHub вмикає для нових репозиторіїв (repo:owner@123/repo@456:...)
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${local.github_repo}:ref:refs/heads/main",
        "repo:${split("/", local.github_repo)[0]}@*/${split("/", local.github_repo)[1]}@*:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "autoslot-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.deploy_assume.json
  max_session_duration = 3600
}

# Дозволено лише одне: виконати стандартний скрипт SSM на цій ВМ і прочитати результат
data "aws_iam_policy_document" "deploy" {
  statement {
    actions = ["ssm:SendCommand"]
    resources = [
      aws_instance.autoslot.arn,
      "arn:aws:ssm:${var.region}::document/AWS-RunShellScript",
    ]
  }
  statement {
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "autoslot-ssm-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
