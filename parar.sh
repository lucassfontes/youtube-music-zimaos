#!/usr/bin/env bash
# YouTube Music ZimaOS — parar sem excluir dados
set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1
DOCKER=(docker)

fim() {
  if [[ -t 0 ]]; then
    read -r -p 'Pressione Enter para fechar o terminal... ' _ || true
  fi
}
trap fim EXIT
printf '\nYouTube Music ZimaOS — Parar aplicativo\n\n'
if ! command -v docker >/dev/null 2>&1; then
  echo 'ERRO: Docker não encontrado.' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  if [[ ! -t 0 ]]; then echo 'Necessário terminal para autorizar sudo.' >&2; exit 1; fi
  read -r -p 'O Docker precisa de permissão de administrador. Tentar com sudo? [s/N]: ' choice
  if [[ ! "$choice" =~ ^[sS]([iI][mM])?$ ]]; then exit 1; fi
  if [[ "$(id -u)" != 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1 || ! sudo -v; then
      echo 'ERRO: sudo indisponível ou autorização recusada.' >&2
      exit 1
    fi
    DOCKER=(sudo docker)
  fi
fi
if "${DOCKER[@]}" compose down; then
  echo 'Aplicativo parado. Músicas, usuários e histórico permanecem salvos.'
else
  echo 'Não foi possível parar o aplicativo.' >&2
  exit 1
fi
