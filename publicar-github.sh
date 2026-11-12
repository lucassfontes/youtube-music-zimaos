#!/usr/bin/env bash
# Publica atualizações no repositório existente SEM forçar o histórico (sem force push).
set -euo pipefail
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO='lucassfontes/youtube-music-zimaos'
for cmd in git gh tar mktemp; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Comando ausente: $cmd. Instale-o antes de publicar." >&2
    exit 1
  fi
done
if ! gh auth status >/dev/null 2>&1; then
  gh auth login --hostname github.com --git-protocol https --web
fi
login="$(gh api user --jq .login)"
if [[ "$login" != lucassfontes ]]; then echo "Conta diferente da proprietária: $login" >&2; exit 1; fi
printf 'Destino: https://github.com/%s\nVersão: 1.5.7\n' "$REPO"
read -r -p 'Autoriza atualizar publicamente o repositório com esta versão? [s/N]: ' answer
[[ "$answer" =~ ^[sS]([iI][mM])?$ ]] || { echo 'Cancelado.'; exit 0; }
gh auth setup-git >/dev/null
if ! gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo create "$REPO" --public --description 'YouTube Music ZimaOS: player e downloads MP3/MP4'
fi
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
git clone "https://github.com/${REPO}.git" "$work/repo"
if ! git -C "$work/repo" show-ref --verify --quiet refs/heads/main; then
  git -C "$work/repo" switch -c main
fi
# Só copia código e configurações; conteúdo do usuário fica na biblioteca do ZimaOS.
(
  cd "$SOURCE_DIR"
  tar --exclude='./.git' --exclude='./.env' --exclude='./dados' --exclude='./musicas' \
      --exclude='./videos' --exclude='./Biblioteca' --exclude='./YouTube_Music' --exclude='./.venv' \
      --exclude='./__pycache__' --exclude='*/__pycache__' --exclude='./.pytest_cache' \
      --exclude='*.mp3' --exclude='*.mp4' --exclude='*.m4a' --exclude='*.webm' \
      --exclude='*.db' --exclude='*.sqlite*' --exclude='*.zip' -cf - .
) | (cd "$work/repo" && tar -xf -)
cd "$work/repo"
git config user.name "$login"
git config user.email "$(gh api user --jq .id)+${login}@users.noreply.github.com"
git add -A
if git diff --cached --name-only | grep -E '(^|/)(\.env$|dados/|musicas/|videos/|Biblioteca/|YouTube_Music/)|\.(db|sqlite|mp3|mp4|m4a|webm)$' >/dev/null; then
  echo 'Publicação interrompida: algum arquivo de mídia ou dado privado entrou no commit.' >&2
  exit 1
fi
if git diff --cached --quiet; then
  echo 'Não há novas alterações a publicar.'
  exit 0
fi
git commit -m 'Atualizar YouTube Music ZimaOS para v1.5.7'
git push origin main
printf '\nCódigo publicado. Confira a construção em: https://github.com/%s/actions\n' "$REPO"
echo 'Após sucesso da Action, a imagem 1.5.7 estará disponível no GHCR.'
