#!/bin/bash
# Hetzner CX23/CAX11 (Ubuntu 24.04 또는 Debian 13) 초기 세팅: 스왑, Docker, 방화벽, GitHub Actions 러너, 디코 봇
# 사용법 (root로):
#   bash setup.sh                                   # 기본 세팅만
#   bash setup.sh runner yoro1836/tawc <등록토큰>    # 러너 추가 (저장소마다 한 번씩)
#   bash setup.sh bot                               # 디코 봇 설치
# 등록토큰: 저장소 Settings > Actions > Runners > New self-hosted runner 화면의 --token 값 (1시간 유효)
set -euo pipefail

base() {
    # 스왑 4GB (램 4GB라 빌드 중 OOM 방지)
    if ! swapon --show | grep -q /swapfile; then
        fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
        echo 'vm.swappiness=10' > /etc/sysctl.d/99-swap.conf && sysctl -p /etc/sysctl.d/99-swap.conf
    fi
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
    DEBIAN_FRONTEND=noninteractive apt-get -y install docker.io git curl jq ufw sudo \
        unattended-upgrades build-essential unzip
    # compose v2 패키지 이름이 우분투(docker-compose-v2)와 데비안(docker-compose)에서 다름
    DEBIAN_FRONTEND=noninteractive apt-get -y install docker-compose-v2 2>/dev/null \
        || DEBIAN_FRONTEND=noninteractive apt-get -y install docker-compose
    systemctl enable --now docker
    # 들어오는 건 SSH만
    ufw default deny incoming && ufw default allow outgoing && ufw allow OpenSSH && ufw --force enable
    id runner >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker runner
    echo "기본 세팅 끝"
}

runner() {
    local repo=$1 token=$2 name dir ver arch
    case $(uname -m) in aarch64) arch=arm64 ;; *) arch=x64 ;; esac
    name=$(hostname)-${repo#*/}
    dir=/home/runner/${repo#*/}
    ver=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | sed 's/^v//')
    mkdir -p "$dir" && cd "$dir"
    if [ ! -f config.sh ]; then
        curl -fsSL -o r.tgz "https://github.com/actions/runner/releases/download/v${ver}/actions-runner-linux-${arch}-${ver}.tar.gz"
        tar xzf r.tgz && rm r.tgz && chown -R runner:runner "$dir"
        ./bin/installdependencies.sh
    fi
    sudo -u runner ./config.sh --unattended --url "https://github.com/$repo" --token "$token" \
        --name "$name" --labels "self-hosted,linux,${arch},hetzner" --replace
    ./svc.sh install runner && ./svc.sh start
    echo "러너 등록 끝: $repo ($name)"
}

bot() {
    cd /opt
    [ -d Discord-bot ] || git clone https://github.com/yoro1836/Discord-bot
    cd Discord-bot
    [ -f .env ] || { cp .env.example .env; echo ".env를 채운 뒤 다시 실행하세요: nano /opt/Discord-bot/.env"; exit 0; }
    docker compose up -d --build
    echo "봇 실행됨. 로그: cd /opt/Discord-bot && docker compose logs -f"
}

case "${1:-base}" in
    base) base ;;
    runner) runner "$2" "$3" ;;
    bot) bot ;;
    *) echo "usage: $0 [base|runner owner/repo token|bot]"; exit 1 ;;
esac
