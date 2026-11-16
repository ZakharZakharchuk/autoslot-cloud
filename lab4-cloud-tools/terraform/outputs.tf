output "public_ip" {
  description = "Публічна IP-адреса ВМ"
  value       = aws_instance.autoslot.public_ip
}

output "app_url" {
  description = "Сайт AutoSlot (стартує через 3-5 хв після створення ВМ)"
  value       = "http://${aws_instance.autoslot.public_ip}:8080"
}

output "grafana_url" {
  description = "Grafana (доступна лише з admin_cidr)"
  value       = "http://${aws_instance.autoslot.public_ip}:3000"
}

output "ssh_command" {
  value = "ssh -i ${trimsuffix(pathexpand(var.ssh_public_key_path), ".pub")} ubuntu@${aws_instance.autoslot.public_ip}"
}

output "instance_id" {
  description = "ID ВМ — секрет DEPLOY_INSTANCE_ID у GitHub"
  value       = aws_instance.autoslot.id
}

output "deploy_role_arn" {
  description = "Роль для GitHub Actions — секрет DEPLOY_ROLE_ARN у GitHub"
  value       = aws_iam_role.deploy.arn
}
