#!/bin/bash
# Скрипт для автоматического исправления docker-compose.platform.yml на сервере клиента

COMPOSE_FILE="/opt/road-pdf-platform/docker-compose.platform.yml"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "Ошибка: Файл $COMPOSE_FILE не найден."
    exit 1
fi

echo "Применяем исправления к $COMPOSE_FILE..."

# 1. Исправляем volume для портала (добавляем :shared)
sed -i 's|- /opt/road-pdf-platform:/opt/road-pdf-platform$|- /opt/road-pdf-platform:/opt/road-pdf-platform:shared|g' "$COMPOSE_FILE"

# 2. Удаляем ошибочный глобальный volume (если кто-то случайно добавил его в конец файла)
# Мы удаляем строку '- /opt/road-pdf-platform:/opt/road-pdf-platform' только если она находится в блоке глобальных volumes
sed -i '/volumes:/,$ { /^[[:space:]]*- \/opt\/road-pdf-platform:\/opt\/road-pdf-platform$/d }' "$COMPOSE_FILE"

# 3. Добавляем необходимые volumes для convert-to-pdf, если их там нет
# Ищем блок convert-to-pdf: и проверяем наличие правильных volumes
if ! grep -q "source: /opt/road-pdf-platform/mnt/smb" "$COMPOSE_FILE"; then
    echo "Добавляем проброс SMB-шары в convert-to-pdf..."
    # Вставляем нужные volumes после '- convert-data:/data'
    sed -i '/- convert-data:\/data/a \      - type: bind\n        source: \/opt\/road-pdf-platform\/mnt\/smb\n        target: \/data\/smb\n        bind:\n          propagation: rslave\n      - \/opt\/road-pdf-platform:\/opt\/road-pdf-platform' "$COMPOSE_FILE"
fi

echo "Файл успешно обновлен. Перезапускаем контейнеры..."
cd /opt/road-pdf-platform && docker compose -f docker-compose.platform.yml up -d portal convert-to-pdf

echo "Готово! Теперь зайдите в портал и переподключите SMB-шару."
