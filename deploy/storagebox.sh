#!/bin/bash
# 헤츠너 스토리지 박스를 SMB로 마운트해서 파일 공유 서버(Zipline) 업로드 폴더로 사용
# 사용법 (root):  bash storagebox.sh u123456      (스토리지 박스 사용자 이름)
# 미리: 스토리지 박스 설정에서 "SMB support" 켜기, 비밀번호 설정
set -euo pipefail

user=${1:?스토리지 박스 사용자 이름 (예: u123456)}
mnt=/mnt/storagebox
apt-get install -y cifs-utils

read -rsp "스토리지 박스 비밀번호: " pass; echo
umask 077
printf 'username=%s\npassword=%s\n' "$user" "$pass" > /root/.storagebox-cred

mkdir -p "$mnt"
# uid/gid 1000: Zipline 컨테이너가 쓰는 사용자. 파일 권한 문제를 피하려고 전부 그 사용자로 보이게 함
opts="credentials=/root/.storagebox-cred,uid=1000,gid=1000,file_mode=0660,dir_mode=0770,seal,nofail,_netdev,x-systemd.automount"
line="//$user.your-storagebox.de/backup $mnt cifs $opts 0 0"
grep -q " $mnt cifs " /etc/fstab && sed -i "\| $mnt cifs |d" /etc/fstab
echo "$line" >> /etc/fstab
systemctl daemon-reload
mount "$mnt" || mount -a
df -h "$mnt"

# Zipline 업로드 폴더를 스토리지 박스로
mkdir -p "$mnt/zipline-uploads"
if [ -d /opt/fileshare ]; then
    cd /opt/fileshare
    if [ -d uploads ] && [ ! -L uploads ]; then
        cp -a uploads/. "$mnt/zipline-uploads/" 2>/dev/null || true
        mv uploads uploads.local-backup
    fi
    ln -sfn "$mnt/zipline-uploads" uploads
    sed -i "s#- ./uploads:/zipline/uploads#- $mnt/zipline-uploads:/zipline/uploads#" compose.yaml
    if docker compose version >/dev/null 2>&1; then docker compose up -d; else docker-compose up -d; fi
    echo "Zipline 업로드가 이제 스토리지 박스에 저장됨 (기존 파일은 옮기고 uploads.local-backup에 남겨둠)"
fi
