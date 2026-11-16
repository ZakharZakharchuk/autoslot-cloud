# Лабораторна 4 — інструменти керування хмарними ресурсами

| Що | Де |
| --- | --- |
| Terraform (ВМ в AWS, мережеві правила, запуск застосунку) | `terraform/` |
| CI: тести, система в Docker + smoke-тест, перевірка Terraform | `../.github/workflows/ci.yml` |
| Розгортання на ВМ після успішного CI | `../.github/workflows/deploy.yml` |
| Метрики в сервісах | `../lab2-containers/services/common/metrics.py` |
| Prometheus, правила сповіщень, Grafana | `../lab2-containers/monitoring/` |
| Генератор навантаження | `scripts/load.py` |
| Результати | `results/` |
| Звіт | `report.md` |

## Швидкий старт

```bash
# 1. Інфраструктура
cd terraform && cp terraform.tfvars.example terraform.tfvars   # заповнити
terraform init && terraform apply
terraform output                                                # адреси

# 2. Автоматичне розгортання: у GitHub -> Settings -> Secrets -> Actions додати
#    DEPLOY_HOST (terraform output public_ip) і DEPLOY_SSH_KEY (приватний ключ)

# 3. Моніторинг локально
cd ../../lab2-containers && docker compose --profile monitoring up -d --build
# Grafana: http://localhost:3000 (admin / GRAFANA_PASSWORD), дашборд AutoSlot
python ../lab4-cloud-tools/scripts/load.py http://localhost:8080 --minutes 5

# 4. Прибрати інфраструктуру
cd ../lab4-cloud-tools/terraform && terraform destroy
```
