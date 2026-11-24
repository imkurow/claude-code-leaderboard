#!/usr/bin/env bash
# Runs a Cloudflare Tunnel without root: cron calls this every minute, flock keeps one copy.
# Put the token of a remotely-managed tunnel in data/tunnel.token (chmod 600). See docs/cloudflare.md.
cd "$(dirname "$0")" || exit 1
if [ -f data/tunnel.log ] && [ "$(stat -c %s data/tunnel.log 2>/dev/null || echo 0)" -gt 5000000 ]; then
  tail -n 2000 data/tunnel.log > data/tunnel.log.tmp && mv data/tunnel.log.tmp data/tunnel.log
fi
TUNNEL_TOKEN="$(cat data/tunnel.token)" exec flock -n data/tunnel.lock \
  "${CLOUDFLARED:-$(command -v cloudflared || echo "$HOME/bin/cloudflared")}" tunnel --no-autoupdate --loglevel warn run >> data/tunnel.log 2>&1
