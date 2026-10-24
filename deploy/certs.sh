#!/bin/sh
# Сертификат Let's Encrypt для домена (когда он появится).
# Порт 80 на сервере занят чужим проектом, поэтому проверка — через DNS (TXT-запись _acme-challenge):
#   deploy/certs.sh trip.example.ru admin@example.ru
# certbot покажет TXT-запись → добавьте её у регистратора → Enter. Сертификат кладётся в volume certs,
# nginx перечитывает конфиг без простоя. Продление — раз в ~80 дней тем же скриптом.
set -eu
DOMAIN=${1:?usage: certs.sh <domain> <email>}
EMAIL=${2:?usage: certs.sh <domain> <email>}
cd "$(dirname "$0")/.."
VOL=$(docker volume ls -q | grep -E '(^|_)certs$' | grep odin | head -1)
docker run --rm -it -v odin-letsencrypt:/etc/letsencrypt certbot/certbot:v2.11.0 certonly \
  --manual --preferred-challenges dns -d "$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email
docker run --rm -v odin-letsencrypt:/le:ro -v "$VOL":/certs alpine:3.20 sh -c \
  "cp -L /le/live/$DOMAIN/fullchain.pem /certs/fullchain.pem && cp -L /le/live/$DOMAIN/privkey.pem /certs/privkey.pem"
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml exec nginx nginx -s reload
echo "Готово: https://$DOMAIN (не забудьте PUBLIC_HOST=$DOMAIN в .env)"
