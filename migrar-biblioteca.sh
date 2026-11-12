#!/usr/bin/env bash
# Copia arquivos antigos para a nova pasta, sem apagar ou sobrescrever os originais.
set -euo pipefail
source_dir="${1:-/DATA/AppData/YouTubeMusicZimaOS}"
dest_dir="${2:-/DATA/AppData/YouTube_Music}"
case "$source_dir" in /DATA/*) ;; *) echo 'A origem deve estar dentro de /DATA.' >&2; exit 1 ;; esac
case "$dest_dir" in /DATA/AppData/*) ;; *) echo 'O destino deve ficar dentro de /DATA/AppData.' >&2; exit 1 ;; esac
if [ ! -d "$source_dir" ]; then echo "Pasta antiga não encontrada: $source_dir" >&2; exit 1; fi
if [ "$(cd "$source_dir" && pwd -P)" = "$(mkdir -p "$dest_dir" && cd "$dest_dir" && pwd -P)" ]; then
  echo 'Origem e destino são a mesma pasta; nenhuma alteração.' >&2
  exit 1
fi
printf 'Origem: %s\nDestino: %s\n\nPare o aplicativo antigo ANTES de copiar o banco de dados.\n' "$source_dir" "$dest_dir"
if [ ! -t 0 ]; then echo 'Execute este script interativamente para confirmar a cópia.' >&2; exit 1; fi
read -r -p 'Confirma a cópia SEM sobrescrever arquivos existentes? [s/N]: ' answer
case "$answer" in s|S|sim|SIM|Sim) ;; *) echo 'Cancelado sem copiar.'; exit 0;; esac
for mapping in 'dados:dados' 'musicas:Musica' 'videos:Videos'; do
  old="${mapping%%:*}"; new="${mapping#*:}"
  if [ -d "$source_dir/$old" ] && [ ! -L "$source_dir/$old" ]; then
    mkdir -p "$dest_dir/$new"
    cp -a -n -- "$source_dir/$old/." "$dest_dir/$new/"
    echo "Copiado (sem sobrescrever): $old -> $new"
  else
    echo "Origem ausente, ignorando: $source_dir/$old"
  fi
done
printf '\nCópia concluída. Pasta antiga mantida intacta. Verifique os arquivos antes de ligar a nova versão.\n'
