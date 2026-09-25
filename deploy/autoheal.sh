#!/bin/sh
# Перезапуск контейнера, если healthcheck Docker признал его нездоровым.
# restart: unless-stopped поднимает упавший процесс, но не зависший; этот скрипт закрывает второй случай.
# Установка на сервере: cp deploy/autoheal.sh /usr/local/bin/ && chmod +x /usr/local/bin/autoheal.sh
#   echo '*/2 * * * * root /usr/local/bin/autoheal.sh >> /var/log/autoheal.log 2>&1' > /etc/cron.d/autoheal

CONTAINER=max_hackaton-app-1
status=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null)
if [ "$status" = "unhealthy" ]; then
    echo "$(date -Is) $CONTAINER unhealthy, restarting"
    docker restart "$CONTAINER"
fi
