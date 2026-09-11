#!/usr/bin/env bash
# Nightly ETL wrapper for cron: single instance (flock), dated logs, 30-day
# log rotation, non-zero exit on failure (cron MAILTO gets the message).
#
#   30 2 * * * /opt/microsip-etl/deploy/run_nightly.sh >> /opt/microsip-etl/logs/cron.log 2>&1
#   30 3 2 * * ETL_TARGET=reporte-mensual /opt/microsip-etl/deploy/run_nightly.sh >> ... 2>&1
#
# Env overrides: ETL_DIR (repo root), ETL_TARGET (default nightly).
# Extra arguments are forwarded to the container, e.g.
#   ETL_TARGET=reporte-mensual deploy/run_nightly.sh --mes 2026-08 --corte zona
set -u -o pipefail

ETL_DIR="${ETL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ETL_TARGET="${ETL_TARGET:-nightly}"
LOG_DIR="$ETL_DIR/logs"
LOCK_FILE="$ETL_DIR/.etl.lock"
LOG_FILE="$LOG_DIR/etl-$(date +%Y%m%d).log"

mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name 'etl-*.log' -mtime +30 -delete 2>/dev/null || true

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date '+%F %T') ETL already running, skipping" | tee -a "$LOG_FILE"
  exit 0
fi

echo "$(date '+%F %T') ETL start target=$ETL_TARGET args=${*:-}" | tee -a "$LOG_FILE"
docker compose -f "$ETL_DIR/deploy/docker-compose.yml" run --rm etl "$ETL_TARGET" ${1+"$@"} 2>&1 | tee -a "$LOG_FILE"
rc=${PIPESTATUS[0]}
echo "$(date '+%F %T') ETL end rc=$rc" | tee -a "$LOG_FILE"

if [ "$rc" -ne 0 ]; then
  echo "ETL FAILED (rc=$rc). See $LOG_FILE" >&2
fi
exit "$rc"
