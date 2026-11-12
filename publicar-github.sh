#!/usr/bin/env bash
# Publicação inicial e atualizações do projeto no GitHub de lucassfontes.
# Precisa de git + GitHub CLI (gh) e login no navegador.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
REPO='lucassfontes/youtube-music-zimaos'

echo
printf '   YouTube Music ZimaOS v1.5.5 — publicar no GitHub\n\n'
for bin in git gh; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    printf 'Falta %s. No Ubuntu/Zorin: sudo apt update && sudo apt install git gh\n' "$bin" >&2
    exit 1
  fi
done
if ! gh auth status >/dev/null 2>&1; then
  echo 'Conecte sua conta GitHub. O gh mostrará instruções para autenticação.'
  gh auth login --hostname github.com --git-protocol https --web
fi
USER_LOGIN="$(gh api user --jq .login)"
if [[ "$USER_LOGIN" != 'lucassfontes' ]]; then
  printf 'Conta conectada: %s. Este projeto está configurado para lucassfontes.\n' "$USER_LOGIN" >&2
  exit 1
fi
printf 'Repositório: https://github.com/%s\n' "$REPO"
echo 'ATENÇÃO: será criado um repositório PÚBLICO, como seus demais projetos.'
echo 'As pastas dados/, musicas/, videos/ e o arquivo .env serão ignorados pelo Git.'
read -r -p 'Confirmar publicação pública? [s/N]: ' ok
[[ "$ok" =~ ^[sS]([iI][mM])?$ ]] || exit 0

if [[ ! -d .git ]]; then
  git init -b main 2>/dev/null || { git init; git branch -M main; }
fi
# Configure identidade local sem incluir credenciais no código enviado.
if ! git config user.name >/dev/null; then git config user.name "$USER_LOGIN"; fi
if ! git config user.email >/dev/null; then
  USER_ID="$(gh api user --jq .id)"
  git config user.email "${USER_ID}+${USER_LOGIN}@users.noreply.github.com"
fi
# Comprovar que dados não serão publicados mesmo que já existam localmente.
for item in .env dados/segredo-teste.sqlite musicas/faixa.mp3 videos/arquivo.mp4; do
  if ! git check-ignore -q "$item" 2>/dev/null; then
    printf 'AVISO: %s não parece ignorado! Confira .gitignore antes de continuar.\n' "$item" >&2
    exit 1
  fi
done

git add -A
if ! git diff --cached --quiet; then
  git commit -m 'Publicar YouTube Music ZimaOS v1.5.5 e pipeline GHCR'
fi
if git remote get-url origin >/dev/null 2>&1; then
  remote="$(git remote get-url origin)"
  case "$remote" in
    *github.com/lucassfontes/youtube-music-zimaos|*github.com/lucassfontes/youtube-music-zimaos.git) ;;
    *) echo "Remote origin inesperado: $remote" >&2; exit 1;;
  esac
else
  git remote add origin "https://github.com/$REPO.git"
fi

if gh repo view "$REPO" >/dev/null 2>&1; then
  echo 'Repositório existente: enviando alterações para main...'
else
  echo 'Criando o repositório público no GitHub...'
  gh repo create "$REPO" --public --description 'Busca, player e downloads MP3/MP4 no ZimaOS com Docker/GHCR'
fi
git branch -M main
gh auth setup-git >/dev/null
git push -u origin main

echo
echo 'Arquivos publicados.'
echo "Repositório: https://github.com/$REPO"
echo "Acompanhe a imagem: https://github.com/$REPO/actions"
echo 'Quando a Action terminar, marque a imagem GHCR como Public (Packages > Package settings > Change visibility).'
echo 'Depois, no ZimaOS, use o compose.yaml e o iniciar.sh deste repositório.'
