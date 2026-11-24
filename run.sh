#!/usr/bin/env bash
# Keeps server.py running without root (Linux): cron calls this every minute; flock makes sure
# only one copy runs, so a crashed or rebooted server comes back within a minute.
cd "$(dirname "$0")" || exit 1
mkdir -p data
# Trim the log when it passes ~5 MB.
if [ -f data/server.log ] && [ "$(stat -c %s data/server.log 2>/dev/null || echo 0)" -gt 5000000 ]; then
  tail -n 2000 data/server.log > data/server.log.tmp && mv data/server.log.tmp data/server.log
fi
exec flock -n data/server.lock python3 server.py >> data/server.log 2>&1
