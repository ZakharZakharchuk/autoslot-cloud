# Уся інфраструктура AutoSlot в AWS: SSH-ключ, мережеві правила й ВМ.
# terraform apply   — створити або оновити;  terraform destroy — прибрати все.

# Найсвіжіший офіційний образ Ubuntu 24.04 від Canonical (власник 099720109477)
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "aws_key_pair" "autoslot" {
  key_name   = "autoslot-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_security_group" "autoslot" {
  name        = "autoslot-sg"
  description = "AutoSlot: gateway for everyone, SSH and Grafana for admin only"

  ingress {
    description = "gateway (site and API)"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "SSH from admin IP"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
  }
  ingress {
    description = "Grafana from admin IP"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
  }
  egress {
    description = "outbound (packages, git, email)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "autoslot" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.autoslot.key_name
  vpc_security_group_ids = [aws_security_group.autoslot.id]

  # Якщо ВМ вимкнеться зсередини (shutdown у cloud-init) — AWS її видалить і не братиме плату
  instance_initiated_shutdown_behavior = var.auto_shutdown_hours > 0 ? "terminate" : "stop"

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  # IMDSv2: метадані ВМ доступні лише з токеном (захист від SSRF-атак)
  metadata_options {
    http_tokens = "required"
  }

  # Скрипт першого запуску: Docker, код, .env, docker compose up
  user_data = templatefile("${path.module}/cloud-init.sh.tftpl", {
    repo_url            = var.repo_url
    repo_ref            = var.repo_ref
    replicas            = var.replicas
    auto_shutdown_hours = var.auto_shutdown_hours
    admin_emails        = var.admin_emails
    auth_dev_mode       = var.auth_dev_mode
    postgres_password   = var.postgres_password
    auth_secret         = var.auth_secret
    grafana_password    = var.grafana_password
    smtp_host           = var.smtp_host
    smtp_user           = var.smtp_user
    smtp_password       = var.smtp_password
  })
  user_data_replace_on_change = true

  tags = { Name = "autoslot" }
}
