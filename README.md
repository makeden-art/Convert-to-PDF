# Convert-to-PDF

Сервис конвертации документов в PDF — модуль платформы Road.

- Репозиторий: [github.com/makeden-art/Convert-to-PDF](https://github.com/makeden-art/Convert-to-PDF)
- Docker Hub: `makeden/convert-to-pdf:latest`
- Маршрут (в платформе): `/convert` через прокси портала `:8080`
- Прямой порт: `8084`

## Поддерживаемые форматы (v0.2)

- DOC, DOCX, XLS, XLSX, ODT, ODS, RTF → PDF (LibreOffice)
- PDF — пропускается (уже PDF)

## Пакетная конвертация папки

Укажите путь на сервере — все файлы внутри конвертируются, **PDF кладётся рядом** с оригиналом:

```
/data/проект/ведомость.docx  →  /data/проект/ведомость.pdf
```

Веб-интерфейс: блок «Папка целиком» на `/convert`.

API:

```bash
curl -X POST http://localhost:8084/api/convert-folder \
  -H 'Content-Type: application/json' \
  -d '{"path": "/data/моя_папка", "recursive": true}'
```

Разрешённые каталоги задаются переменной `CONVERT_ALLOWED_ROOTS` (по умолчанию `/data`, `/workspace`, `/opt/road-pdf-platform`).

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

## План разработки

Дорожная карта модуля (только «Перевод в PDF»): [PLAN.md](PLAN.md)
