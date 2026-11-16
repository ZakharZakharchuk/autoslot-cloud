variable "region" {
  description = "Регіон AWS"
  type        = string
  default     = "eu-central-1"
}

variable "instance_type" {
  description = "Тип ВМ. t3.medium (2 vCPU, 4 ГБ) вистачає для 10 реплік + моніторингу"
  type        = string
  default     = "t3.medium"
}

variable "admin_cidr" {
  description = "Ваша IP-адреса у форматі 1.2.3.4/32: лише з неї доступні SSH і Grafana"
  type        = string
  validation {
    condition     = can(cidrnetmask(var.admin_cidr)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "Вкажіть конкретну адресу, наприклад 1.2.3.4/32 (не 0.0.0.0/0)."
  }
}

variable "ssh_public_key_path" {
  description = "Шлях до публічного SSH-ключа"
  type        = string
  default     = "~/.ssh/autoslot.pub"
}

variable "repo_url" {
  description = "Git-репозиторій, з якого ВМ забирає код"
  type        = string
  default     = "https://github.com/yaroslavkondrasiuk/autoslot-cloud.git"
}

variable "repo_ref" {
  description = "Гілка або тег для розгортання"
  type        = string
  default     = "main"
}

variable "replicas" {
  description = "Кількість реплік кожного сервісу"
  type        = number
  default     = 2
}

variable "auto_shutdown_hours" {
  description = "Через скільки годин ВМ знищить сама себе (0 — не знищувати)"
  type        = number
  default     = 5
}

variable "admin_emails" {
  description = "Адреси адміністраторів через кому"
  type        = string
}

variable "auth_dev_mode" {
  description = "true — код входу показується на сторінці (лише для демонстрації без SMTP)"
  type        = bool
  default     = false
}

# --- секрети: задаються в terraform.tfvars, який не комітиться ---
variable "postgres_password" {
  type      = string
  sensitive = true
}

variable "auth_secret" {
  type      = string
  sensitive = true
  validation {
    condition     = length(var.auth_secret) >= 16
    error_message = "auth_secret має бути щонайменше 16 символів."
  }
}

variable "grafana_password" {
  type      = string
  sensitive = true
}

variable "smtp_host" {
  type    = string
  default = ""
}

variable "smtp_user" {
  type    = string
  default = ""
}

variable "smtp_password" {
  type      = string
  default   = ""
  sensitive = true
}
