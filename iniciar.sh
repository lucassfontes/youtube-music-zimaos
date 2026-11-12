#!/usr/bin/env bash
# YouTube Music ZimaOS v1.5.6 — inicializador GHCR interativo
# Inicie com: bash iniciar.sh (também funciona quando o bit de execução é perdido).
set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1

VERSION='1.5.6'
DOCKER=(docker)
SUDO_READY=0
STARTED=0

banner() {
  printf '\n\033[1;36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n'
  printf '\033[1;37m   YouTube Music ZimaOS — v%s\033[0m\n' "$VERSION"
  printf '\033[1;36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n'
}
info() { printf '\033[1;36m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[AVISO]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[ERRO]\033[0m %s\n' "$*" >&2; }

pause_at_end() {
  if [[ "${STARTED}" != 1 && -t 0 ]]; then
    printf '\n'
    read -r -p 'Inicialização interrompida. Pressione Enter para fechar... ' _ || true
  fi
}
trap pause_at_end EXIT

confirm() {
  local choice
  if [[ ! -t 0 ]]; then
    fail 'Esta operação precisa de confirmação em um terminal interativo.'
    return 1
  fi
  read -r -p "$1 [s/N]: " choice || return 1
  [[ "$choice" =~ ^[sS]([iI][mM])?$ ]]
}

need_sudo() {
  [[ "$(id -u)" == 0 ]] && return 0
  [[ "$SUDO_READY" == 1 ]] && return 0
  if ! command -v sudo >/dev/null 2>&1; then
    fail 'sudo não disponível. Entre com um administrador e execute: bash iniciar.sh'
    return 1
  fi
  info 'O sistema solicitará sua senha de administrador (sudo).'
  if sudo -v; then SUDO_READY=1; else fail 'Autorização de administrador recusada.'; return 1; fi
}

as_admin() {
  if [[ "$(id -u)" == 0 ]]; then "$@"; else sudo "$@"; fi
}

read_env() {
  local key="$1" fallback="$2" line value
  line="$(grep -E "^${key}=" .env | tail -n 1 || true)"
  value="${line#*=}"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
  if [[ "$value" == \'*\' && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "${value:-$fallback}"
}

check_path() {
  local path="$1" label="$2"
  local normalized="${path%/}"
  if [[ "$path" != /* || "$path" == *$'\n'* || "$path" == *:* || "$path" == */../* || "$path" == */.. || "$path" == *'/./'* ]]; then
    fail "$label inválido: $path"
    return 1
  fi
  case "$normalized" in /|/DATA|/DATA/AppData|/DATA/Media|/media|/mnt|/home|/root|/var|/etc|/usr|/opt)
      fail "$label aponta para uma pasta grande demais: $path"
      return 1 ;;
  esac
}

# Verifica permissões POSIX básicas de escrita + acesso pelo UID/GID 1000 do container.
# ACLs e sistemas de arquivos NTFS podem diferir: o app ainda é a verificação final.
uid1000_writable() {
  local path="$1" owner group mode digits a b c
  owner="$(stat -c %u -- "$path" 2>/dev/null)" || return 1
  group="$(stat -c %g -- "$path" 2>/dev/null)" || return 1
  mode="$(stat -c %a -- "$path" 2>/dev/null)" || return 1
  digits="${mode: -3}"
  [[ "$digits" =~ ^[0-7]{3}$ ]] || return 1
  a="${digits:0:1}"; b="${digits:1:1}"; c="${digits:2:1}"
  if [[ "$owner" == 1000 ]] && (( (8#$a & 3) == 3 )); then return 0; fi
  if [[ "$group" == 1000 ]] && (( (8#$b & 3) == 3 )); then return 0; fi
  (( (8#$c & 3) == 3 ))
}

ensure_path() {
  local path="$1" label="$2" kind="$3" was_created=0
  if [[ -L "$path" ]]; then fail "$label é um link simbólico; para manter os dados dentro do projeto, use uma pasta real: $path"; return 1; fi
  if [[ -e "$path" && ! -d "$path" ]]; then fail "$label não é uma pasta: $path"; return 1; fi
  if [[ ! -d "$path" ]]; then
    info "Criando pasta de $label: $path"
    if mkdir -p -- "$path" 2>/dev/null; then
      was_created=1
    else
      warn "É necessária permissão de administrador para criar: $path"
      confirm "Autoriza criar a pasta de $label com sudo?" || return 1
      need_sudo || return 1
      as_admin mkdir -p -- "$path" || return 1
      was_created=1
    fi
  fi
  if uid1000_writable "$path"; then return 0; fi

  if [[ "$kind" == config ]]; then
    warn "O UID 1000 do aplicativo não tem escrita na pasta de configuração: $path"
    info 'A correção será restrita à pasta de configuração deste aplicativo.'
    confirm 'Autoriza corrigir proprietário dessa pasta e seus arquivos para 1000:1000?' || return 1
    need_sudo || return 1
    as_admin chown -R 1000:1000 -- "$path" || return 1
    as_admin chmod u+rwx -- "$path" || return 1
  else
    warn "O UID 1000 do aplicativo não tem escrita na pasta de $label: $path"
    info 'A correção NÃO será recursiva: não alteraremos arquivos ou subpastas existentes.'
    confirm "Autoriza dar propriedade do diretório de $label ao UID 1000?" || return 1
    need_sudo || return 1
    as_admin chown 1000:1000 -- "$path" || return 1
    as_admin chmod u+rwx -- "$path" || return 1
  fi
  if ! uid1000_writable "$path"; then
    warn 'Não foi possível confirmar escrita do UID 1000; verifique opções de montagem de NTFS/exFAT e ACLs.'
    # Uma pasta recém-criada pode usar um sistema de arquivos especial.
    return 1
  fi
  info "Permissões de $label verificadas."
}

prepare_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    fail 'Docker não encontrado no servidor. Instale/ative Docker no ZimaOS.'
    return 1
  fi
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
  else
    warn 'Docker não pode ser acessado pelo usuário atual (permissão ou serviço indisponível).'
    confirm 'Autoriza tentar acessar o Docker com sudo?' || return 1
    need_sudo || return 1
    if ! as_admin docker info >/dev/null 2>&1; then
      fail 'Docker indisponível mesmo com sudo. Verifique se o serviço Docker está iniciado.'
      return 1
    fi
    if [[ "$(id -u)" == 0 ]]; then DOCKER=(docker); else DOCKER=(sudo docker); fi
  fi
  if ! "${DOCKER[@]}" compose version >/dev/null 2>&1; then
    fail 'Docker Compose V2 não encontrado. Verifique a instalação do Compose no ZimaOS.'
    return 1
  fi
}

show_urls() {
  local addr iface found=0
  printf '\n\033[1;32mAPLICATIVO INICIADO!\033[0m\n'
  printf 'Painel neste servidor: http://127.0.0.1:%s\n' "$APP_PORT"
  if command -v ip >/dev/null 2>&1; then
    while read -r iface addr; do
      [[ -n "$addr" ]] || continue
      case "$iface" in lo|docker*|br-*|veth*|virbr*|tailscale*|tun*|zt*) continue ;; esac
      printf 'Acesso pela rede (%s): http://%s:%s\n' "$iface" "$addr" "$APP_PORT"
      found=1
    done < <(ip -o -4 addr show up scope global 2>/dev/null | awk '{split($4,a,"/");print $2,a[1]}' | sort -u)
  fi
  if [[ "$found" == 0 ]]; then
    printf 'Acesso pela rede: http://IP-DO-ZIMAOS:%s\n' "$APP_PORT"
  fi
  printf '\nDocker continuará funcionando ao fechar o terminal.\n'
  printf 'Não exponha esta porta diretamente à internet.\n'
}

primary_ip() {
  local iface addr
  if command -v ip >/dev/null 2>&1; then
    while read -r iface addr; do
      [[ -n "$addr" ]] || continue
      case "$iface" in lo|docker*|br-*|veth*|virbr*|tailscale*|tun*|zt*) continue ;; esac
      printf '%s' "$addr"; return 0
    done < <(ip -o -4 addr show up scope global 2>/dev/null | awk '{split($4,a,"/");print $2,a[1]}' | sort -u)
  fi
  if command -v hostname >/dev/null 2>&1; then
    addr="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [[ "$addr" =~ ^[0-9.]+$ ]]; then printf '%s' "$addr"; return 0; fi
  fi
  return 1
}

export_zimaos_compose() {
  local ip icon_url
  ip="$(primary_ip)" || {
    warn 'Não foi possível detectar IP LAN. Configure manualmente o endereço do ícone no ZimaOS.'
    return 1
  }
  icon_url="http://${ip}:${APP_PORT}/icon.png"
  # `docker compose config` converte os volumes e o build para caminhos ABSOLUTOS.
  # Portanto, o ZimaOS pode importar este arquivo de outra pasta sem mover as músicas.
  if [[ "${DOCKER[0]}" == sudo ]]; then
    if ! sudo env "APP_ICON_URL=$icon_url" docker compose -f compose.yaml config > zimaos-compose.yaml; then
      rm -f zimaos-compose.yaml
      return 1
    fi
  else
    if ! APP_ICON_URL="$icon_url" "${DOCKER[@]}" compose -f compose.yaml config > zimaos-compose.yaml; then
      rm -f zimaos-compose.yaml
      return 1
    fi
  fi
  info "Compose para importar no ZimaOS: $(pwd -P)/zimaos-compose.yaml"
  info "Ícone do app: $icon_url"
  info 'Importe o arquivo no ZimaOS como Aplicativo personalizado para criar o atalho com ícone.'
}

menu() {
  local opt
  while true; do
    printf '\n\033[1mCONTROLES:\033[0m [1] Logs ao vivo  [2] Status  [3] Reiniciar  [4] Parar aplicativo  [5] Compose ZimaOS + ícone  [0] Sair do inicializador\n'
    if ! read -r -p 'Escolha: ' opt; then break; fi
    case "$opt" in
      1) info 'Exibindo logs. Pressione Ctrl+C para voltar ao menu.'
         "${DOCKER[@]}" compose logs -f --tail=80 youtube-music || true ;;
      2) "${DOCKER[@]}" compose ps ;;
      3) info 'Reiniciando aplicativo...'
         "${DOCKER[@]}" compose restart youtube-music && show_urls || fail 'Erro ao reiniciar.' ;;
      4) if confirm 'Deseja parar o aplicativo (seus dados serão mantidos)?'; then
           "${DOCKER[@]}" compose down && info 'Aplicativo parado. Músicas e configurações preservadas.'
           break
         fi ;;
      5) export_zimaos_compose || warn 'Não foi possível gerar o Compose ZimaOS.' ;;
      0|q|Q) info 'Inicializador fechado. O container continuará ativo.'; break ;;
      *) warn 'Opção inválida.' ;;
    esac
  done
}

main() {
  banner
  if [[ ! -f .env ]]; then
    if [[ ! -f .env.example ]]; then fail 'Arquivo .env.example não encontrado.'; return 1; fi
    cp -- .env.example .env || { fail 'Não foi possível criar .env'; return 1; }
    info 'Criado .env. Biblioteca em /DATA/AppData/YouTube_Music/{Musica,Videos,dados}.'
  fi
  # Caminhos persistentes centralizados no ZimaOS; não dependem da pasta do Compose.
  LIBRARY_ROOT="$(read_env LIBRARY_ROOT /DATA/AppData/YouTube_Music)"
  LIBRARY_ROOT="${LIBRARY_ROOT%/}"
  CONFIG_PATH="${LIBRARY_ROOT}/dados"
  MUSIC_PATH="${LIBRARY_ROOT}/Musica"
  VIDEO_PATH="${LIBRARY_ROOT}/Videos"
  APP_PORT="$(read_env APP_PORT 8787)"
  check_path "$CONFIG_PATH" CONFIG_PATH || return 1
  check_path "$MUSIC_PATH" MUSIC_PATH || return 1
  check_path "$VIDEO_PATH" VIDEO_PATH || return 1
  if [[ "$CONFIG_PATH" == "$MUSIC_PATH" || "$CONFIG_PATH" == "$MUSIC_PATH/"* || "$MUSIC_PATH" == "$CONFIG_PATH/"* ]]; then
    fail 'Configuração e músicas precisam de pastas diferentes e não podem ser subpastas uma da outra.'
    return 1
  fi
  if [[ ! "$APP_PORT" =~ ^[0-9]{1,5}$ ]] || (( 10#$APP_PORT < 1 || 10#$APP_PORT > 65535 )); then
    fail "Porta inválida: $APP_PORT"; return 1
  fi
  info "Músicas salvas em: $MUSIC_PATH"
  info "Vídeos salvos em: $VIDEO_PATH"
  info 'Verificando pastas e permissões (a senha sudo só será solicitada quando necessário).'
  ensure_path "$CONFIG_PATH" 'configuração' config || return 1
  ensure_path "$MUSIC_PATH" 'músicas' music || return 1
  ensure_path "$VIDEO_PATH" 'vídeos' videos || return 1
  info 'Verificando Docker...'
  prepare_docker || return 1
  info 'Verificando a imagem Docker pronta no GHCR (sem compilação local)...'
  # compose pull obtém atualizações da imagem com a mesma tag quando disponíveis.
  if ! "${DOCKER[@]}" compose pull youtube-music; then
    if "${DOCKER[@]}" image inspect 'ghcr.io/lucassfontes/youtube-music-zimaos:1.5.6' >/dev/null 2>&1; then
      warn 'Não foi possível atualizar pelo GHCR. Usando a imagem já salva no servidor.'
    else
      fail 'Imagem indisponível no GHCR. Confira se a Action terminou e se o pacote está PÚBLICO no GitHub.'
      info 'Opcional para construir localmente: docker compose -f compose.yaml -f compose.local.yaml up -d --build'
      return 1
    fi
  fi
  if ! "${DOCKER[@]}" compose up -d --no-build youtube-music; then
    fail 'Falha ao iniciar a imagem pronta. Confira o Docker e a porta 8787.'
    return 1
  fi

  if [[ -z "$("${DOCKER[@]}" compose ps --status running -q youtube-music 2>/dev/null)" ]]; then
    fail 'Container não ficou em execução. Últimos logs:'
    "${DOCKER[@]}" compose logs --tail=60 youtube-music || true
    return 1
  fi
  show_urls
  export_zimaos_compose || warn 'O aplicativo funciona normalmente, mas o Compose de importação não pôde ser gerado.'
  STARTED=1
  menu
}

main "$@"
