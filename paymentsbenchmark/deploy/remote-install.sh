#!/usr/bin/env bash
# Runs on the VM. Installs one release of paymentsbenchmark.com without touching
# any other site: its own directory, its own Nginx file, `nginx -t` before a
# graceful reload, and an automatic rollback if the reload or check fails.
set -euo pipefail

TARBALL="${1:?usage: remote-install.sh <site.tar.gz> <nginx.conf>}"
CONF_SRC="${2:?usage: remote-install.sh <site.tar.gz> <nginx.conf>}"
BASE=/var/www/paymentsbenchmark
RELEASE="$BASE/releases/$(date -u +%Y%m%d-%H%M%S)"
CONF=/etc/nginx/sites-available/paymentsbenchmark.com
LINK=/etc/nginx/sites-enabled/paymentsbenchmark.com
KEEP=5

echo "== Release $RELEASE"
sudo mkdir -p "$RELEASE"
sudo tar -xzf "$TARBALL" -C "$RELEASE"
sudo test -f "$RELEASE/index.html" || { echo "index.html missing from archive"; exit 1; }
sudo chown -R www-data:www-data "$BASE"
sudo find "$RELEASE" -type d -exec chmod 755 {} +
sudo find "$RELEASE" -type f -exec chmod 644 {} +

PREVIOUS="$(readlink -f "$BASE/current" 2>/dev/null || true)"
sudo ln -sfn "$RELEASE" "$BASE/current.new"
sudo mv -T "$BASE/current.new" "$BASE/current"

NEW_CONF=0
if ! sudo test -f "$CONF"; then
  # First install only. Later runs leave certbot's SSL lines in place.
  echo "== Installing Nginx site $CONF"
  sudo install -m 644 "$CONF_SRC" "$CONF"
  sudo ln -sfn "$CONF" "$LINK"
  NEW_CONF=1
fi

rollback() {
  echo "!! Rolling back"
  if [ "$NEW_CONF" = 1 ]; then sudo rm -f "$LINK" "$CONF"; fi
  if [ -n "$PREVIOUS" ]; then sudo ln -sfn "$PREVIOUS" "$BASE/current"; fi
  sudo nginx -t && sudo systemctl reload nginx
}

if ! sudo nginx -t; then rollback; exit 1; fi
sudo systemctl reload nginx

# Check our site through the local Nginx, by Host header (no DNS needed).
code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: paymentsbenchmark.com' http://127.0.0.1/method)
if [ "$code" != 200 ] && [ "$code" != 301 ]; then
  echo "!! /method returned $code"; rollback; exit 1
fi
echo "== paymentsbenchmark.com /method -> $code"

# Keep the newest $KEEP releases.
ls -1dt "$BASE"/releases/* | tail -n +$((KEEP + 1)) | xargs -r sudo rm -rf
rm -f "$TARBALL" "$CONF_SRC"
echo "== Done"
