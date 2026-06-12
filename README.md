# Convert-to-PDF

Сервис конвертации документов в PDF — модуль платформы Road.

- Репозиторий: [github.com/makeden-art/Convert-to-PDF](https://github.com/makeden-art/Convert-to-PDF)
- Docker Hub: `makeden/convert-to-pdf:latest`
- Маршрут (в платформе): `/convert` через прокси портала `:8080`
- Прямой порт: `8084`

## Поддерживаемые форматы (v0.1)

- DOC, DOCX, XLS, XLSX, ODT, ODS, RTF → PDF (LibreOffice)
- PDF → passthrough

## Локальный запуск

```bash
docker compose up -d --build
# http://localhost:8084/convert
```

## CI/CD

При push в `main` GitHub Actions собирает образ и пушит на Docker Hub.

Секреты в репозитории GitHub:

- `DOCKER_USERNAME`
- `DOCKER_PASSWORD`

## Платформа

Подключается в `road-pdf-platform/docker-compose.platform.yml` как сервис `convert-to-pdf`.
