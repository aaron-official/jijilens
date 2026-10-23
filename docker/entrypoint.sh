#!/bin/sh
# Start Postgres inside the container, load the catalog snapshot into it, then
# start the app. The database lives on the container's own disk: it is rebuilt
# from the snapshot whenever the container starts fresh.
#
# The snapshot comes from either
#   SNAPSHOT_REPO  a (private) Hugging Face dataset repo, read with HF_TOKEN, or
#   SNAPSHOT_DIR   a mounted folder (default /snapshot).
set -eu

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  initdb --pgdata="$PGDATA" --username=jijilens --auth=trust --encoding=UTF8 >/dev/null
fi
pg_ctl --pgdata="$PGDATA" --log="$HOME/postgres.log" --wait \
  --options="-c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp" start
createdb --host=127.0.0.1 --username=jijilens jijilens 2>/dev/null || true

if [ -n "${SNAPSHOT_REPO:-}" ]; then
  export SNAPSHOT_DIR="$HOME/snapshot"
  echo "downloading snapshot from $SNAPSHOT_REPO"
  python -c "import os; from huggingface_hub import snapshot_download; \
snapshot_download(os.environ['SNAPSHOT_REPO'], repo_type='dataset', local_dir=os.environ['SNAPSHOT_DIR'], token=os.environ.get('HF_TOKEN'))"
fi
jijilens snapshot load "$SNAPSHOT_DIR" --if-empty

exec jijilens serve --host 0.0.0.0 --port "$PORT"
