#!/usr/bin/env bash
# Migra copiando, SEM apagar ou sobrescrever o original.
# Padrão: v1.5.6 -> v1.5.7 no próprio /DATA/AppData/YouTube_Music.
# Opcional: bash migrar-biblioteca.sh /DATA/AppData/YouTubeMusicZimaOS
set -euo pipefail
source_dir="${1:-/DATA/AppData/YouTube_Music}"
dest_dir="${2:-/DATA/AppData/YouTube_Music}"
case "$source_dir" in /DATA/*) ;; *) echo 'Origem deve ficar dentro de /DATA.' >&2; exit 1;; esac
case "$dest_dir" in /DATA/AppData/*) ;; *) echo 'Destino deve ficar dentro de /DATA/AppData.' >&2; exit 1;; esac
if [ ! -d "$source_dir" ] || [ -L "$source_dir" ]; then
  echo "Origem ausente ou link simbólico: $source_dir" >&2; exit 1
fi
for root in "$dest_dir" "$dest_dir/Biblioteca"; do
  if [ -L "$root" ]; then echo "Destino com link simbólico: $root" >&2; exit 1; fi
done
for dest in "$dest_dir/Biblioteca/Musicas" "$dest_dir/Biblioteca/Videos" "$dest_dir/dados"; do
  if [ -L "$dest" ]; then echo "Destino com link simbólico: $dest" >&2; exit 1; fi
done
mkdir -p -- "$dest_dir/Biblioteca/Musicas" "$dest_dir/Biblioteca/Videos" "$dest_dir/dados"
# Impede copiar diretórios para dentro de si (e criar recursão).
copy_without_overwrite() {
  local origin="$1" target="$2"
  if [ ! -d "$origin" ] || [ -L "$origin" ]; then return 1; fi
  local from to
  from="$(cd "$origin" && pwd -P)"
  to="$(cd "$target" && pwd -P)"
  case "$to" in "$from"|"$from"/*)
    echo "Ignorando origem igual/ancestral ao destino: $origin" >&2; return 1;;
  esac
  cp -a -n -- "$origin/." "$target/"
  printf 'Copiado sem sobrescrever: %s -> %s\n' "$origin" "$target"
}
printf 'Origem: %s\nDestino: %s\n\n' "$source_dir" "$dest_dir"
printf 'Pare o container e faça BACKUP antes da migração. Nenhum arquivo original será excluído.\n'
if [ ! -t 0 ]; then
  echo 'Execute este script em um terminal interativo para confirmar a cópia.' >&2; exit 1
fi
read -r -p 'Confirmar cópia SEM substituir arquivos existentes? [s/N]: ' answer
case "$answer" in s|S|sim|SIM|Sim) ;; *) echo 'Cancelado.'; exit 0;; esac
# Aceita todas as variantes de maiúsculas das versões anteriores.
for old in Musica Musicas musicas; do
  copy_without_overwrite "$source_dir/$old" "$dest_dir/Biblioteca/Musicas" || true
done
for old in Videos videos; do
  copy_without_overwrite "$source_dir/$old" "$dest_dir/Biblioteca/Videos" || true
done
# Se origem for uma versão já organizada, copie as pastas internas também.
copy_without_overwrite "$source_dir/Biblioteca/Musicas" "$dest_dir/Biblioteca/Musicas" || true
copy_without_overwrite "$source_dir/Biblioteca/Videos" "$dest_dir/Biblioteca/Videos" || true
if [ "$(cd "$source_dir" && pwd -P)" != "$(cd "$dest_dir" && pwd -P)" ]; then
  copy_without_overwrite "$source_dir/dados" "$dest_dir/dados" || true
fi
printf '\nConcluído. Origem preservada. Confira os arquivos antes de subir a nova versão.\n'
