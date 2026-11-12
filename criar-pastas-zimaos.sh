#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-/DATA/AppData/YouTube_Music}"
case "$BASE" in /DATA/AppData/*) ;; *) echo 'Use um caminho dentro de /DATA/AppData.' >&2; exit 1;; esac
for name in dados Musica Videos; do
  if [ -L "$BASE/$name" ]; then echo "Não uso links simbólicos: $BASE/$name" >&2; exit 1; fi
  mkdir -p -- "$BASE/$name"
done
printf 'Biblioteca criada em: %s\n' "$BASE"
printf 'Músicas: %s/Musica\nVídeos: %s/Videos\nDados: %s/dados\n' "$BASE" "$BASE" "$BASE"
