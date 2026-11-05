#!/bin/bash
# 파일 공유 서버 (Zipline + Caddy 자동 HTTPS). 디스코드에 링크를 붙이면 이미지/동영상이 임베드로 뜸.
# 사용법 (root, 도커 설치 후):  bash fileshare.sh [도메인]
#   도메인을 안 주면 <서버IP>.sslip.io 를 씀 (무료, 따로 설정 필요 없음)
# 헤츠너 방화벽에서 TCP 80, 443 인바운드를 열어야 함 (80은 인증서 발급용)
set -euo pipefail

# 데비안 13은 docker CLI가 docker-cli 패키지로 따로 나옴
command -v docker >/dev/null || apt-get install -y docker-cli
# compose 플러그인이 없으면 docker-compose 명령으로 대신
if docker compose version >/dev/null 2>&1; then compose() { docker compose "$@"; }; else compose() { docker-compose "$@"; }; fi

ip=$(curl -fsS4 https://ifconfig.me)
domain=${1:-${ip//./-}.sslip.io}
dir=/opt/fileshare
mkdir -p "$dir" && cd "$dir"

if [ ! -f .env ]; then
    rnd() { openssl rand -base64 48 | tr -dc A-Za-z0-9 | cut -c -32; }
    cat > .env <<ENV
POSTGRESQL_PASSWORD=$(rnd)
CORE_SECRET=$(rnd)
DOMAIN=$domain
ENV
    chmod 600 .env
fi

cat > Caddyfile <<'CADDY'
{$DOMAIN} {
    encode zstd gzip
    reverse_proxy zipline:3000
}
CADDY

cat > compose.yaml <<'YAML'
services:
  postgresql:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: zipline
      POSTGRES_PASSWORD: ${POSTGRESQL_PASSWORD}
      POSTGRES_DB: zipline
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ['CMD', 'pg_isready', '-U', 'zipline']
      interval: 10s
      timeout: 5s
      retries: 5
    mem_limit: 512m

  zipline:
    image: ghcr.io/diced/zipline:latest
    restart: unless-stopped
    environment:
      DATABASE_URL: postgres://zipline:${POSTGRESQL_PASSWORD}@postgresql:5432/zipline
      CORE_SECRET: ${CORE_SECRET}
      CORE_RETURN_HTTPS_URLS: "true"
    depends_on:
      postgresql:
        condition: service_healthy
    volumes:
      - ./uploads:/zipline/uploads
      - ./public:/zipline/public
    mem_limit: 1g

  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    environment:
      DOMAIN: ${DOMAIN}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - zipline

volumes:
  pgdata:
  caddy_data:
  caddy_config:
YAML

compose up -d
echo
echo "접속: https://$domain  (처음 접속해서 관리자 계정 만들기)"
echo "인증서 발급에 1분쯤 걸릴 수 있음. 로그: cd $dir && docker compose logs -f"
