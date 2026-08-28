#!/usr/bin/env bash
# Restricted Docker read-only snapshot helper. It intentionally accepts no args.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "snapshot helper accepts no arguments" >&2
  exit 64
fi

export PATH=/usr/bin:/bin
DOCKER_BIN=/usr/bin/docker

if [ ! -x "$DOCKER_BIN" ]; then
  echo "docker binary not found at $DOCKER_BIN" >&2
  exit 127
fi

mapfile -t container_ids < <("$DOCKER_BIN" ps --all --quiet)

printf '{"inspect":'
if [ "${#container_ids[@]}" -eq 0 ]; then
  printf '[]'
else
  "$DOCKER_BIN" inspect "${container_ids[@]}"
fi

printf ',"stats":['
first=true
while IFS= read -r stat; do
  [ -z "$stat" ] && continue
  if [ "$first" = true ]; then
    first=false
  else
    printf ','
  fi
  # Docker itself emits each --format '{{json .}}' value as JSON.
  printf '%s' "$stat"
done < <("$DOCKER_BIN" stats --all --no-stream --format '{{json .}}')
printf ']}\n'
