#!/usr/bin/env bash
# Enable https://scythe.spectrcyde.com for the SCYTHE orchestrator.
# Run as root once the DNS A record scythe.spectrcyde.com -> 192.255.214.141 exists:
#   sudo bash deploy/nginx/install-scythe-tls.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOMAIN=scythe.spectrcyde.com
CONF=/etc/nginx/conf.d/$DOMAIN.conf

if [[ "$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1)" != "192.255.214.141" ]]; then
  echo "error: $DOMAIN does not resolve to 192.255.214.141 yet; add the A record first" >&2
  exit 1
fi

# Phase 1: a port-80 block for the domain so certbot's nginx authenticator can
# answer the HTTP-01 challenge. The final block cannot be loaded yet because it
# references a certificate that does not exist.
mkdir -p /var/www/html
cat > "$CONF" <<BOOT
server {
    listen      80;
    listen [::]:80;
    server_name $DOMAIN;
    location / { proxy_pass http://scythe_orchestrator; proxy_set_header Host \$host; }
}
BOOT
nginx -t && systemctl reload nginx

# Phase 2: obtain the cert (certonly: we install the config ourselves).
certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos \
  --keep-until-expiring --deploy-hook "systemctl reload nginx"

# Phase 3: the real config.
install -m 0644 "$HERE/$DOMAIN.conf" "$CONF"
nginx -t && systemctl reload nginx

curl -sSI "https://$DOMAIN/scythe-web/regional-rf-demo.html" | head -1
echo "done: https://$DOMAIN/scythe-web/regional-rf-demo.html"
