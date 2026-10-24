#!/bin/sh
# Первичная установка на сервер (один раз). Ничего не трогает за пределами ~/odin, своих
# docker-ресурсов (проект odin-zapros) и юнитов odin-cd.*.
#   curl -fsSL https://raw.githubusercontent.com/ostrovok-hackathon-2026/5-ukazatelei/main/deploy/bootstrap.sh | sh
# или: git clone ... ~/odin && ~/odin/deploy/bootstrap.sh
set -eu
DIR=${ODIN_DIR:-$HOME/odin}
REPO=https://github.com/ostrovok-hackathon-2026/5-ukazatelei.git

[ -d "$DIR/.git" ] || git clone -q "$REPO" "$DIR"
cd "$DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  ip=$(curl -fsS -m 3 -H "Metadata-Flavor: Google" \
       http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || hostname -I | cut -d' ' -f1)
  {
    echo ""
    echo "# --- сервер ---"
    echo "PUBLIC_HOST=$ip"
    echo "HTTP_PORT=8080"
    echo "HTTPS_PORT=8443"
    echo "GRAFANA_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c1-20)"
  } >> .env
  echo "Создан .env: впишите LLM_* (иначе агент работает в офлайн-режиме). Пароль Grafana — в .env."
fi
chmod +x deploy/cd.sh

sudo cp deploy/systemd/odin-cd.service deploy/systemd/odin-cd.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now odin-cd.timer
deploy/cd.sh --force
