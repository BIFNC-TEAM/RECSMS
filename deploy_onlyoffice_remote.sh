#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"
umask 077
if [[ ! -f office.env ]]; then
  python3 -c 'import secrets; from pathlib import Path; Path("office.env").write_text("JWT_SECRET="+secrets.token_urlsafe(48)+"\nJWT_ENABLED=true\nJWT_HEADER=Authorization\nALLOW_PRIVATE_IP_ADDRESS=true\n")'
fi
chmod 600 office.env
gateway="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')"
if [[ ! "$gateway" =~ ^[0-9.]+$ ]]; then
  echo 'Invalid Docker bridge gateway' >&2
  exit 1
fi
mkdir -p data logs state
cat > standards-office-callback.service <<EOF
[Unit]
Description=Standards ONLYOFFICE callback bridge (SSH upstream)
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$root
ExecStart=/usr/bin/python3 $root/office_callback_proxy.py --host $gateway --port 18766 --upstream-port 18765
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
sudo install -m 644 standards-office-callback.service /etc/systemd/system/standards-office-callback.service
sudo systemctl daemon-reload
sudo systemctl enable --now standards-office-callback.service
if docker container inspect standards-office-docs >/dev/null 2>&1; then
  label="$(docker inspect --format '{{index .Config.Labels "app"}}' standards-office-docs)"
  if [[ "$label" != 'standards-platform-onlyoffice' ]]; then
    echo 'Container name already belongs to another application; refusing to replace it.' >&2
    exit 1
  fi
  docker start standards-office-docs >/dev/null
else
  docker run -d --name standards-office-docs --label app=standards-platform-onlyoffice \
    --restart unless-stopped --cpus 2 --memory 3g --memory-swap 4g --shm-size 512m \
    --log-opt max-size=10m --log-opt max-file=3 \
    -p 127.0.0.1:18080:80 --add-host host.docker.internal:host-gateway \
    --env-file "$root/office.env" \
    -v "$root/data:/var/www/onlyoffice/Data" \
    -v "$root/logs:/var/log/onlyoffice" \
    -v "$root/state:/var/lib/onlyoffice" \
    onlyoffice/documentserver:10.0
fi
echo 'Container started. Check: curl --fail http://127.0.0.1:18080/healthcheck'
