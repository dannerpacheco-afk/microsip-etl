#!/usr/bin/env bash
# ETL wrapper for macOS (launchd or cron) using the local venv, no Docker.
# Single instance via mkdir lock (macOS has no flock), dated logs, 30-day
# rotation, non-zero exit on failure.
#
#   ETL_TARGET=nightly         deploy/run_nightly_mac.sh
#   ETL_TARGET=reporte-mensual deploy/run_nightly_mac.sh
#   deploy/run_nightly_mac.sh backfill --tables fact_ventas_articulo
#
# Env: ETL_DIR (repo root, default: parent of this script), ETL_TARGET
# (default nightly), PYTHON (default $ETL_DIR/.venv/bin/python).
set -u -o pipefail

ETL_DIR="${ETL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ETL_TARGET="${ETL_TARGET:-nightly}"
PYTHON="${PYTHON:-$ETL_DIR/.venv/bin/python}"
LOG_DIR="$ETL_DIR/logs"
LOCK_DIR="$ETL_DIR/.etl.lock.d"
LOG_FILE="$LOG_DIR/etl-$(date +%Y%m%d).log"

mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name 'etl-*.log' -mtime +30 -delete 2>/dev/null || true

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date '+%F %T') ETL already running (lock $LOCK_DIR), skipping" | tee -a "$LOG_FILE"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

cd "$ETL_DIR" || exit 2
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-$ETL_DIR/credentials.json}"

echo "$(date '+%F %T') ETL start target=$ETL_TARGET args=$*" | tee -a "$LOG_FILE"
"$PYTHON" -W ignore main.py "$ETL_TARGET" "$@" 2>&1 | tee -a "$LOG_FILE"
rc=${PIPESTATUS[0]}
echo "$(date '+%F %T') ETL end rc=$rc" | tee -a "$LOG_FILE"

if [ "$rc" -ne 0 ]; then
  echo "ETL FAILED (rc=$rc). See $LOG_FILE" >&2
fi
exit "$rc"
