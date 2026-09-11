#!/bin/sh
# rabbitmq-init-user.sh — обёртка для официального образа rabbitmq (см. compose).
#
# Официальный образ НЕ поддерживает env-создание пользователя (в отличие от
# bitnami/rabbitmq), поэтому пользователь из .env создаётся скриптом. Скрипт:
#   1. запускает сервер в фоне (через официальный entrypoint, который сам
#      делает drop прав до пользователя rabbitmq);
#   2. ждёт полной готовности приложения (rabbitmqctl await_startup);
#   3. создаёт пользователя swot/swot (совпадает с BROKER__USER/BROKER__PASSWORD
#      в .env) — без этого скрипта пользователя из .env в брокере просто нет;
#      штатный guest в docker-образе тоже работает (loopback_users=[]) но
#      выделенный пользователь чище (свои права, не "известный" гостевой);
#   4. живёт, пока жив сервер.
set -u

# 1. Сервер в фоне (docker-entrypoint.sh сам сделает gosu rabbitmq)
docker-entrypoint.sh rabbitmq-server &
server_pid=$!

# 2. Ждём полной готовности приложения (до ~5 минут: сначала нода должна
#    зарегистрироваться, затем await_startup блокируется, пока app не поднимется)
i=0
until docker-entrypoint.sh rabbitmqctl await_startup >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "rabbitmq-init-user: node did not come up in time" >&2
    break
  fi
  sleep 5
done

# 3. Пользователь из .env (идемпотентно: || true — при рестарте уже существует)
docker-entrypoint.sh rabbitmqctl add_user swot swot || true
docker-entrypoint.sh rabbitmqctl set_user_tags swot administrator || true
docker-entrypoint.sh rabbitmqctl set_permissions -p / swot ".*" ".*" ".*" || true

# 4. Время жизни контейнера = время жизни сервера
wait "$server_pid"
