#!/bin/sh
set -eu
cd /home/ubuntu/rsms
umask 077
test -f .env || { cp .env.production.example .env; chmod 600 .env; echo '请先填写 /home/ubuntu/rsms/.env'; exit 1; }
RSMS_OFFICE_CONFIG_REV=$(sha256sum deploy/office-nginx.conf | cut -c1-12)
RSMS_PLATFORM_CONFIG_REV=$(sha256sum deploy/platform-nginx.conf | cut -c1-12)
export RSMS_OFFICE_CONFIG_REV RSMS_PLATFORM_CONFIG_REV
docker network inspect rsms-office >/dev/null 2>&1 || docker network create --driver overlay --attachable rsms-office >/dev/null
docker inspect standards-office-docs --format '{{json .NetworkSettings.Networks}}' | grep -q 'rsms-office' || docker network connect --alias documentserver rsms-office standards-office-docs
cd deploy
docker stack deploy --resolve-image never -c office.stack.yaml rsms
cd ..
if [ "${1:-}" = '--office-only' ]; then exit 0; fi
docker build --build-arg BASE_IMAGE="${RSMS_BASE_IMAGE:-mirror.ccs.tencentyun.com/library/node:22-bookworm-slim}" --build-arg APT_MIRROR="${RSMS_APT_MIRROR:-mirrors.tencentyun.com}" --build-arg PIP_INDEX_URL="${RSMS_PIP_INDEX_URL:-https://mirrors.cloud.tencent.com/pypi/simple}" -t rsms-platform:production .
RSMS_PLATFORM_REV=$(docker image inspect rsms-platform:production --format '{{.Id}}' | cut -d: -f2 | cut -c1-16)
RSMS_PLATFORM_IMAGE=rsms-platform:release-$RSMS_PLATFORM_REV
docker tag rsms-platform:production "$RSMS_PLATFORM_IMAGE"
export RSMS_PLATFORM_IMAGE
# Check all three cloud services before creating tables or replacing the running platform.
docker run --rm --network rsms-office --env-file .env rsms-platform:production python3 production_check.py
cd deploy
docker stack deploy --resolve-image never -c platform.stack.yaml rsms
echo '已提交部署；使用 docker service ls / docker service logs rsms_platform 查看状态。'
