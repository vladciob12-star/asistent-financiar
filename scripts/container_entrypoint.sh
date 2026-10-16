#!/bin/sh
set -eu

mkdir -p "${DATA_DIR:-/var/data}"
chown finance:finance "${DATA_DIR:-/var/data}"
if [ -f "${DATABASE_PATH:-/var/data/finante.sqlite3}" ]; then
  chown finance:finance "${DATABASE_PATH:-/var/data/finante.sqlite3}"
fi
for sidecar in "${DATABASE_PATH:-/var/data/finante.sqlite3}-wal" "${DATABASE_PATH:-/var/data/finante.sqlite3}-shm"; do
  if [ -e "$sidecar" ]; then chown finance:finance "$sidecar"; fi
done
exec gosu finance "$@"
