#!/usr/bin/env bash
# Somente cria diretórios; NÃO altera nem apaga músicas antigas.
set -euo pipefail
BASE="${1:-/DATA/AppData/YouTube_Music}"
case "$BASE" in /DATA/AppData/*) ;; *) echo 'Use um caminho dentro de /DATA/AppData.' >&2; exit 1;; esac
for name in dados Biblioteca Biblioteca/Musicas Biblioteca/Videos; do
  if [ -L "$BASE/$name" ]; then echo "Não uso links simbólicos: $BASE/$name" >&2; exit 1; fi
  mkdir -p -- "$BASE/$name"
done
printf 'Raiz: %s\nMúsicas: %s/Biblioteca/Musicas\nVídeos: %s/Biblioteca/Videos\nDados: %s/dados\n' "$BASE" "$BASE" "$BASE" "$BASE"
