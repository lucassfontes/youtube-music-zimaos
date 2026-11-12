'use strict';
const $ = (id) => document.getElementById(id);
let firstSetup = false;
let selectedUrl = null;
let busy = false;
const downloadedArtwork = new Map(); // video id -> downloaded MP3 job record

async function api(path, method='GET', body=null) {
  const response = await fetch(path, {
    method, credentials:'same-origin',
    headers: {'Content-Type':'application/json', 'X-App-Request':'zimaos-music'},
    ...(body !== null ? {body:JSON.stringify(body)} : {})
  });
  let data;
  try { data = await response.json(); } catch (_) { data = {}; }
  if (!response.ok) {
    const detail = data.detail;
    const error = new Error(typeof detail === 'string' ? detail : (detail?.message || `Erro HTTP ${response.status}`));
    error.status = response.status;
    error.code = (detail && typeof detail === 'object') ? detail.code : null;
    error.details = detail;
    throw error;
  }
  return data;
}
// A decisão é sempre do usuário. Jamais substituímos ou duplicamos silenciosamente.
function askExistingFile(details) {
  return new Promise(resolve => {
    const dialog = $('duplicate-dialog');
    const suffix = details.media_type === 'video' ? 'MP4' : 'MP3';
    $('duplicate-description').textContent = `Este ${suffix} já foi baixado e o arquivo realmente existe no servidor.`;
    $('duplicate-path').textContent = details.path || '';
    let settled = false;
    function finish(value) {
      if (settled) return;
      settled = true;
      dialog.removeEventListener('close', onClose);
      if (dialog.open) dialog.close();
      resolve(value);
    }
    function onClose() { finish(null); }
    dialog.addEventListener('close', onClose);
    $('duplicate-replace').onclick = () => finish('replace');
    $('duplicate-keep').onclick = () => finish('keep_both');
    $('duplicate-cancel').onclick = () => finish(null);
    dialog.showModal();
  });
}
async function createDownloadJob(payload) {
  try {
    return await api('/api/jobs', 'POST', payload);
  } catch (error) {
    if (error.status !== 409 || error.code !== 'file_exists') throw error;
    const choice = await askExistingFile(error.details);
    if (!choice) return null;
    return await api('/api/jobs', 'POST', {...payload, existing_action:choice});
  }
}

function msg(id, value='', error=false) {
  const target = $(id);
  target.textContent = value;
  target.classList.toggle('error', error);
}
function showAuth(needsSetup) {
  firstSetup = needsSetup;
  $('auth-title').textContent = needsSetup ? 'Primeiro acesso' : 'Bem-vindo de volta';
  $('auth-desc').textContent = needsSetup ? 'Crie um usuário e senha para proteger os downloads.' : 'Entre para acessar sua biblioteca musical.';
  $('auth-submit').textContent = needsSetup ? 'Criar usuário' : 'Entrar';
  $('password').setAttribute('autocomplete', needsSetup ? 'new-password' : 'current-password');
  $('auth').hidden = false;
  $('app').hidden = true;
}
function showApp(username) {
  $('app').hidden = false;
  $('auth').hidden = true;
  $('account-name').textContent = username || 'Usuário';
  refreshJobs();
}
async function loadState() {
  try {
    const state = await api('/api/state');
    if (state.logged_in) showApp(state.username);
    else showAuth(state.needs_setup);
  } catch (e) {
    showAuth(false);
    msg('auth-msg', 'Não foi possível conectar ao servidor: ' + e.message, true);
  }
}
$('auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const username = $('username').value.trim();
  const password = $('password').value;
  if (password.length < 6) {msg('auth-msg', 'A senha precisa ter no mínimo 6 caracteres.', true);return;}
  $('auth-submit').disabled = true;
  msg('auth-msg', 'Verificando...');
  try {
    await api(firstSetup ? '/api/setup' : '/api/login', 'POST', {username, password});
    $('password').value = '';
    msg('auth-msg');
    showApp(username);
  } catch (e) { msg('auth-msg', e.message, true); }
  finally { $('auth-submit').disabled = false; }
});
$('logout').addEventListener('click', async () => {
  try { await api('/api/logout','POST'); } catch (_) { }
  showAuth(false);
});
// Pesquisa leve: mostra vídeos sem fazer download até o usuário confirmar.
function formatDuration(seconds) {
  if (!Number.isInteger(seconds) || seconds < 0) return 'Duração não informada';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const sec = String(seconds % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}
// Player em streaming: o áudio começa por blocos sem esperar MP3 completo.
// Se já existir MP3 na biblioteca/cache, o servidor entrega arquivo seekable.
let playerGeneration = 0;
let currentVideo = null;
let playerList = [];
let searchTracks = []; // Resultados da pesquisa corrente.
let activePlaylistGeneration = null; // Preserva a fila ao iniciar uma nova pesquisa.
let skippingTrack = false;
let currentPlayerId = null;
let playbackState = 'stopped';
// Em streaming, HTMLAudioElement.currentTime é relativo ao trecho pedido.
// Somamos o deslocamento para que a barra mostre o ponto na música inteira.
let playbackStartSeconds = 0;
let scrubbingProgress = false;
let nativeSeekAvailable = false;
let playbackTotalSeconds = 0;
let lastIndicatorState = '';
let lastIndicatorId = null;
let lastSystemPositionSecond = -1;

// Integração com o controle de mídia do iOS (tela bloqueada / Central de Controle).
// Ao contrário da barra no aplicativo, estes botões devem TROCAR a faixa.
// No Safari, os handlers devem ser registrados NOVAMENTE quando o áudio passa a tocar:
// antes do evento 'playing', o iOS pode usar o controle padrão de +/-10 segundos.
function systemMediaSession() {
  return 'mediaSession' in navigator ? navigator.mediaSession : null;
}
function mediaAction(name, handler) {
  const session = systemMediaSession();
  if (!session || typeof session.setActionHandler !== 'function') return;
  try { session.setActionHandler(name, handler); } catch (_) { /* Ação indisponível neste navegador. */ }
}
function publishSystemMediaMetadata() {
  const session = systemMediaSession();
  if (!session || !currentVideo || typeof MediaMetadata === 'undefined') return;
  const appIconUrl = location.origin.startsWith('http') ? `${location.origin}/icon.png` : '/icon.png';
  const saved = downloadedArtwork.get(currentVideo.id);
  const cover = saved ? `/api/jobs/${saved.id}/cover?rev=${encodeURIComponent(saved.updated_at || '')}`
    : ((typeof currentVideo.thumbnail === 'string' && /^https:\/\//.test(currentVideo.thumbnail))
      ? currentVideo.thumbnail : appIconUrl);
  try {
    session.metadata = new MediaMetadata({
      title: trackFromSearch(currentVideo),
      artist: currentVideo.channel || 'Canal não informado',
      album: 'YouTube Music ZimaOS',
      artwork: [{src:cover}, {src:currentVideo.thumbnail || appIconUrl}, {src:appIconUrl, sizes:'256x256', type:'image/png'}]
    });
  } catch (_) { /* Metadados opcionais. */ }
  lastSystemPositionSecond = -1;
  syncSystemMediaPosition(true);
}
function syncSystemMediaPosition(force = false) {
  const session = systemMediaSession();
  if (!session || typeof session.setPositionState !== 'function' || !currentVideo) return;
  // A duração nativa do MP3 em streaming é estimada pelo Safari e pode ser incorreta.
  // Preferimos a duração real devolvida pela pesquisa do YouTube.
  const duration = Number(currentVideo.duration);
  if (!Number.isFinite(duration) || duration <= 0 || duration > 1500) return;
  const audio = $('player-audio');
  const absolutePosition = Math.max(0, Math.min(duration, playbackStartSeconds + (audio.currentTime || 0)));
  const wholeSecond = Math.floor(absolutePosition);
  if (!force && wholeSecond === lastSystemPositionSecond) return;
  try {
    session.setPositionState({duration, position:absolutePosition, playbackRate:1});
    lastSystemPositionSecond = wholeSecond;
  } catch (_) { /* Nem todo Safari implementa setPositionState. */ }
}
function syncSystemMediaPlaybackState(state) {
  const session = systemMediaSession();
  if (!session) return;
  try { session.playbackState = state; } catch (_) { }
}
function registerSystemMediaControls() {
  if (!currentVideo || !systemMediaSession()) return;
  // iOS pode dar prioridade aos atalhos de 10s se ambos estiverem presentes.
  mediaAction('seekbackward', null);
  mediaAction('seekforward', null);
  mediaAction('previoustrack', () => { void skipTrack(-1); });
  mediaAction('nexttrack', () => { void skipTrack(1); });
  mediaAction('play', () => {
    if (currentVideo && $('player-audio').paused) togglePlayer();
  });
  mediaAction('pause', () => {
    if (currentVideo && !$('player-audio').paused) $('player-audio').pause();
  });
  mediaAction('seekto', details => {
    if (details && Number.isFinite(details.seekTime)) seekPlayerTo(details.seekTime);
  });
  syncSystemMediaPosition(true);
}
function resetSystemMediaSession() {
  for (const name of ['nexttrack','previoustrack','play','pause','seekto','seekforward','seekbackward']) {
    mediaAction(name, null);
  }
  const session = systemMediaSession();
  if (!session) return;
  syncSystemMediaPlaybackState('none');
  try { session.metadata = null; } catch (_) { }
  lastSystemPositionSecond = -1;
}

// Controles vetoriais sem emoji. As barras animadas ficam apenas no status,
// e nunca substituem os ícones normais de Play / Pause.
function playbackIcon(type) {
  if (type === 'play') return '<svg viewBox="0 0 24 24" class="result-icon" aria-hidden="true" focusable="false"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>';
  return '<svg viewBox="0 0 24 24" class="result-icon" aria-hidden="true" focusable="false"><rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/><rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/></svg>';
}
function setPlaybackState(state) {
  if (playbackState !== state) playbackState = state;
  if (currentVideo) {
    const audio = $('player-audio');
    const active = state === 'playing' || (state === 'loading' && !audio.paused);
    syncSystemMediaPlaybackState(active ? 'playing' : 'paused');
  }
  syncPlayIndicators();
}
function updatePlayButton(button) {
  const active = button.dataset.videoId === currentPlayerId;
  const state = active ? playbackState : 'stopped';
  const text = button.querySelector('.play-text');
  button.classList.toggle('is-current', active);
  button.dataset.state = state;
  const labels = {playing:'Pausar', loading:'Pausar', paused:'Retomar', error:'Tentar novamente', stopped:'Tocar'};
  const label = labels[state] || 'Tocar';
  if (text) text.textContent = label;
  button.setAttribute('aria-label', `${label}: ${button.dataset.videoTitle || 'música'}`);
  button.setAttribute('aria-pressed', String(active && state === 'playing'));
  button.title = label;
}
function syncPlayIndicators(force = false) {
  if (!force && lastIndicatorId === currentPlayerId && lastIndicatorState === playbackState) return;
  lastIndicatorId = currentPlayerId;
  lastIndicatorState = playbackState;
  for (const button of document.querySelectorAll('.search-play')) updatePlayButton(button);
  const toggle = $('player-toggle');
  toggle.dataset.state = playbackState;
  const labels = {playing:'Pausar', loading:'Pausar', paused:'Retomar', error:'Tentar novamente', stopped:'Tocar'};
  toggle.setAttribute('aria-label', labels[playbackState] || 'Tocar');
  toggle.title = labels[playbackState] || 'Tocar';
  toggle.setAttribute('aria-pressed', String(playbackState === 'playing'));
  const dock = $('player-panel');
  dock.dataset.state = playbackState;
  $('player-state-label').textContent = ({playing:'REPRODUZINDO',loading:'CARREGANDO ÁUDIO',paused:'PAUSADO',error:'ERRO NA REPRODUÇÃO',stopped:'OUVINDO AGORA'})[playbackState];
}
function activateTab(tab) {
  const labels = {search:'Buscar músicas',link:'Baixar por link',downloads:'Downloads'};
  if (!labels[tab]) return;
  for (const button of document.querySelectorAll('.nav-tab')) {
    const active = button.dataset.tab === tab;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-selected', String(active));
  }
  for (const pane of document.querySelectorAll('.tab-pane')) {
    const active = pane.dataset.pane === tab;
    pane.classList.toggle('active', active);
    pane.hidden = !active;
  }
  $('current-section').textContent = labels[tab];
  if (tab === 'downloads') { refreshJobs(); refreshCoverRepairStatus(); }
  if (tab === 'search') window.requestAnimationFrame(checkSearchScroll);
}
for (const button of document.querySelectorAll('.nav-tab')) {
  button.addEventListener('click', () => activateTab(button.dataset.tab));
}
// Accessible tab orientation follows the responsive mobile bottom bar.
const mobileNavigation = window.matchMedia('(max-width:720px)');
function syncNavOrientation(){ document.querySelector('.main-nav').setAttribute('aria-orientation',mobileNavigation.matches?'horizontal':'vertical'); }
syncNavOrientation();
if (mobileNavigation.addEventListener) mobileNavigation.addEventListener('change',syncNavOrientation);
$('mobile-logout').addEventListener('click', () => $('logout').click());
function playerClock(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  return formatDuration(Math.floor(seconds));
}
function stopPlayer() {
  playerGeneration++;
  playbackStartSeconds = 0;
  scrubbingProgress = false;
  nativeSeekAvailable = false;
  playbackTotalSeconds = 0;
  const audio = $('player-audio');
  resetSystemMediaSession();
  audio.pause();
  audio.removeAttribute('src');
  audio.load();
  $('player-panel').hidden = true;
  currentVideo = null;
  currentPlayerId = null;
  setPlaybackState('stopped');
  msg('player-msg');
}
function syncPlayerUI() {
  const audio = $('player-audio');
  const knownDuration = Number(currentVideo?.duration) || 0;
  const nativeDuration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0;
  const total = knownDuration > 0 ? knownDuration : (nativeDuration > 0 ? playbackStartSeconds + nativeDuration : 0);
  const position = Math.max(0, Math.min(total || Infinity, playbackStartSeconds + (audio.currentTime || 0)));
  playbackTotalSeconds = total;
  $('player-total').textContent = playerClock(total);
  if (!scrubbingProgress) {
    $('player-elapsed').textContent = playerClock(position);
    $('player-seek').value = total > 0 ? Math.min(1000, Math.round(position / total * 1000)) : 0;
  }
  // Arquivos locais com HTTP Range permitem salto imediato via currentTime.
  // Em transmissões ao vivo por chunks, avançar reinicia o stream na posição selecionada.
  nativeSeekAvailable = false;
  if (nativeDuration > 0 && playbackStartSeconds === 0 && audio.seekable.length) {
    try { nativeSeekAvailable = audio.seekable.end(audio.seekable.length - 1) >= nativeDuration - 1; }
    catch (_) { }
  }
  const canSeek = total > 0 && total <= 1500;
  $('player-seek').disabled = !canSeek;
  $('player-seek').title = canSeek ? 'Arraste ou clique para avançar e voltar na música' : 'A duração não está disponível para avançar';
  $('player-seek').style.setProperty('--seek-progress', `${Number($('player-seek').value)/10}%`);
  $('player-stream-mode').textContent = nativeSeekAvailable ? 'MP3 • avanço instantâneo' : 'Streaming • avance pela barra';
  syncSystemMediaPosition();
  syncPlayIndicators();
  const pos = playerList.findIndex(item => item.id === currentPlayerId);
  $('player-prev').disabled = pos <= 0;
  // Próxima faixa carrega mais resultados automaticamente se ainda houver pesquisa.
  const moreInActiveSearch = hasMore && activePlaylistGeneration === searchGeneration;
  $('player-next').disabled = skippingTrack || pos < 0 || (pos >= playerList.length - 1 && !moreInActiveSearch);
}
function playAudio(video) {
  if (!/^[A-Za-z0-9_-]{11}$/.test(video.id)) return;
  // Reutiliza o mesmo elemento <audio> e a sessão de mídia do iOS ao trocar de faixa.
  // Parar/desmontar o player aqui faria a tela bloqueada perder os botões.
  playerGeneration++;
  const audio = $('player-audio');
  // Trocar o src interrompe a faixa anterior sem enviar um pause explícito ao iOS.
  playbackStartSeconds = 0;
  scrubbingProgress = false;
  nativeSeekAvailable = false;
  playbackTotalSeconds = 0;
  currentVideo = video;
  currentPlayerId = video.id;
  const generation = playerGeneration;
  $('player-title').textContent = video.title;
  $('player-channel').textContent = video.channel;
  const savedArt = downloadedArtwork.get(video.id);
  $('player-cover').onerror = () => { $('player-cover').onerror = null; $('player-cover').src = video.thumbnail || '/icon.png'; };
  $('player-cover').src = savedArt ? `/api/jobs/${savedArt.id}/cover?rev=${encodeURIComponent(savedArt.updated_at || '')}` : (video.thumbnail || '/icon.png');
  $('player-panel').hidden = false;
  publishSystemMediaMetadata();
  setPlaybackState('loading');
  msg('player-msg','Conectando ao áudio...');
  audio.src = `/api/stream/${encodeURIComponent(video.id)}`;
  audio.volume = Number($('player-volume').value) / 100;
  audio.load();
  // chamada diretamente dentro do clique: menos bloqueios de autoplay
  audio.play().catch(() => {
    if (generation === playerGeneration && audio.paused && audio.error == null) {
      setPlaybackState('paused');
      msg('player-msg','Clique em Tocar para iniciar. O navegador pode bloquear o início automático.');
    }
  });
  syncPlayerUI();
}
function togglePlayer() {
  const audio = $('player-audio');
  if (!currentVideo) return;
  if (!audio.paused && playbackState !== 'error') {
    audio.pause();
  } else {
    if (audio.error) {
      // Recomeçar um streaming que falhou e cuja origem está inválida.
      playAudio(currentVideo);
      return;
    }
    setPlaybackState('loading');
    audio.play().catch(() => {
      setPlaybackState('paused');
      msg('player-msg', 'Não foi possível iniciar o áudio.', true);
    });
  }
  syncPlayerUI();
}
$('player-toggle').addEventListener('click', togglePlayer);
// Transporte de faixa: os botões nunca alteram currentTime (somente a barra faz seek).
async function skipTrack(direction) {
  if (!currentVideo || skippingTrack) return;
  const from = playerList.findIndex(v => v.id === currentPlayerId);
  if (from < 0) return;
  if (direction > 0 && from === playerList.length - 1 &&
      activePlaylistGeneration === searchGeneration && hasMore) {
    skippingTrack = true;
    syncPlayerUI();
    try { await loadNextSearchPage(); }
    finally { skippingTrack = false; }
  }
  const to = from + direction;
  if (to >= 0 && to < playerList.length) playAudio(playerList[to]);
  else syncPlayerUI();
}
$('player-prev').addEventListener('click', () => { void skipTrack(-1); });
$('player-next').addEventListener('click', () => { void skipTrack(1); });
// Durante o arraste: apenas mostrar prévia do tempo (sem reiniciar a rede a cada pixel).
const seekBar = $('player-seek');
seekBar.addEventListener('pointerdown', () => {
  if (!seekBar.disabled) scrubbingProgress = true;
});
seekBar.addEventListener('input', () => {
  if (seekBar.disabled || !playbackTotalSeconds) return;
  scrubbingProgress = true;
  const seconds = Math.floor(Number(seekBar.value) / 1000 * playbackTotalSeconds);
  $('player-elapsed').textContent = playerClock(seconds);
  seekBar.style.setProperty('--seek-progress', `${Number(seekBar.value)/10}%`);
  seekBar.setAttribute('aria-valuetext', `${playerClock(seconds)} de ${playerClock(playbackTotalSeconds)}`);
});
function seekPlayerTo(value) {
  if (!currentVideo || !playbackTotalSeconds || !Number.isFinite(value)) {
    scrubbingProgress = false;
    return;
  }
  const seconds = Math.min(1499, Math.max(0, Math.floor(value)));
  scrubbingProgress = false;
  const audio = $('player-audio');
  if (nativeSeekAvailable) {
    // MP3 salvo/cacheado: navegador busca os bytes necessários via HTTP Range.
    audio.currentTime = seconds;
    syncPlayerUI();
    syncSystemMediaPosition(true);
    return;
  }
  // Áudio ainda em streaming: pede apenas o trecho desejado, sem esperar o cache completo.
  const pausedBeforeSeek = audio.paused && playbackState === 'paused';
  playerGeneration++;
  playbackStartSeconds = seconds;
  audio.pause();
  audio.src = `/api/stream/${encodeURIComponent(currentVideo.id)}?start=${seconds}`;
  audio.load();
  if (pausedBeforeSeek) {
    setPlaybackState('paused');
    msg('player-msg', 'Posição selecionada. Aperte Tocar para continuar.');
  } else {
    setPlaybackState('loading');
    msg('player-msg', `Carregando áudio a partir de ${playerClock(seconds)}...`);
    audio.play().catch(() => {
      if (!currentVideo || audio.error) return;
      setPlaybackState('paused');
      msg('player-msg', 'Clique em Tocar para continuar.');
    });
  }
  syncPlayerUI();
  syncSystemMediaPosition(true);
}
seekBar.addEventListener('change', () => {
  if (seekBar.disabled || !currentVideo || !playbackTotalSeconds) {
    scrubbingProgress = false;
    return;
  }
  seekPlayerTo(Number(seekBar.value) / 1000 * playbackTotalSeconds);
});
$('player-volume').addEventListener('input', () => {
  const audio = $('player-audio'); audio.volume = Number($('player-volume').value) / 100;
  audio.muted = false; syncVolumeButton();
});
function syncVolumeButton() {
  const audio = $('player-audio');
  const muted = audio.muted || audio.volume === 0;
  const button = $('player-mute');
  button.classList.toggle('is-muted', muted);
  button.setAttribute('aria-label', muted ? 'Ativar volume' : 'Silenciar');
  button.setAttribute('aria-pressed', String(muted));
  button.title = muted ? 'Ativar volume' : 'Silenciar';
}
$('player-mute').addEventListener('click', () => {
  const audio = $('player-audio'); audio.muted = !audio.muted;
  syncVolumeButton();
});
const playerAudio = $('player-audio');
for (const ev of ['timeupdate','durationchange','loadedmetadata','progress']) {
  playerAudio.addEventListener(ev, syncPlayerUI);
}
playerAudio.addEventListener('waiting', () => {
  if (!currentVideo) return;
  setPlaybackState('loading');
  msg('player-msg','Carregando áudio...');
});
playerAudio.addEventListener('playing', () => {
  if (!currentVideo) return;
  setPlaybackState('playing');
  publishSystemMediaMetadata();
  registerSystemMediaControls();
  msg('player-msg');
  syncPlayerUI();
});
playerAudio.addEventListener('pause', () => {
  if (!currentVideo) return;
  // Um pause provocado por erro/ended não pode mostrar a animação.
  if (!playerAudio.ended && playbackState !== 'error') setPlaybackState('paused');
  syncPlayerUI();
});
playerAudio.addEventListener('ended', () => {
  setPlaybackState('paused');
  void skipTrack(1);
});
playerAudio.addEventListener('error', async () => {
  if (!currentVideo) return;
  const videoId = currentVideo.id; const generation = playerGeneration;
  let reason = 'Não foi possível reproduzir esta música. Confira os logs do Docker.';
  try {
    const state = await api(`/api/playback/${videoId}`);
    if (state.state === 'error') reason = state.message || reason;
  } catch (_) { }
  if (generation === playerGeneration) {
    setPlaybackState('error');
    msg('player-msg', reason, true);
  }
});
$('close-player').addEventListener('click', stopPlayer);
setInterval(async () => {
  if (!currentVideo || $('app').hidden || $('player-panel').hidden) return;
  const generation = playerGeneration;
  try {
    const state = await api(`/api/playback/${currentVideo.id}`);
    if (generation !== playerGeneration) return;
    if (state.state === 'error') {
      setPlaybackState('error');
      msg('player-msg', state.message, true);
    }
    else if (state.state === 'connecting' && playerAudio.readyState < 2) msg('player-msg', state.message);
    else if (state.state === 'ready' && !playerAudio.error) syncPlayerUI();
  } catch (_) { }
}, 2500);
function toast(text, isError=false) {
  const el = $('toast-notice');
  el.textContent = text;
  el.classList.toggle('error', isError);
  el.hidden = false;
  clearTimeout(toast.timeout);
  toast.timeout = setTimeout(() => { el.hidden = true; }, 5500);
}
function trackFromSearch(video) {
  const title = (video.title || '').trim();
  const channel = (video.channel || '').trim();
  for (const delimiter of [' - ', ' – ', ' — ']) {
    if (channel && title.toLocaleLowerCase('pt-BR').startsWith((channel + delimiter).toLocaleLowerCase('pt-BR')))
      return title.slice((channel + delimiter).length).trim() || title;
  }
  return title || 'Música sem título';
}
function searchCard(video) {
  const card = document.createElement('article'); card.className = 'search-card';
  const image = document.createElement('img');
  image.className = 'search-cover'; image.src = video.thumbnail;
  image.alt = ''; image.loading = 'lazy'; image.decoding = 'async'; image.referrerPolicy = 'no-referrer';
  const details = document.createElement('div'); details.className = 'search-details';
  const title = document.createElement('h3'); title.textContent = video.title;
  const channel = document.createElement('p'); channel.textContent = `Canal: ${video.channel}`;
  const time = document.createElement('small'); time.textContent = formatDuration(video.duration);
  details.append(title, channel, time);
  const actions = document.createElement('div'); actions.className = 'search-actions';
  const play = document.createElement('button');
  play.type = 'button'; play.className = 'btn secondary search-play';
  play.dataset.videoId = video.id; play.dataset.videoTitle = video.title;
  play.innerHTML = `<span class="play-icon" aria-hidden="true">${playbackIcon('play')}</span><span class="pause-icon" aria-hidden="true">${playbackIcon('pause')}</span><span class="play-text">Tocar</span>`;
  updatePlayButton(play);
  const save = document.createElement('button'); save.type='button'; save.className = 'btn primary'; save.textContent='↧ MP3';
  const saveVideo = document.createElement('button'); saveVideo.type='button'; saveVideo.className = 'btn secondary'; saveVideo.textContent='▣ MP4';
  play.addEventListener('click', () => {
    if (currentPlayerId === video.id && currentVideo) togglePlayer();
    else {
      playerList = searchTracks;
      activePlaylistGeneration = searchGeneration;
      playAudio(video);
    }
  });
  async function queueFromSearch(mediaType, button) {
    if (button.disabled) return;
    button.disabled = true; button.textContent = 'Adicionando...';
    try {
      const result = await createDownloadJob({
        url:video.url, artist:video.channel || 'Canal não informado',
        track:trackFromSearch(video), album:'', media_type:mediaType,
        video_quality:Number($('video-quality').value)
      });
      if (!result) { button.disabled=false; button.textContent=mediaType === 'video' ? '▣ MP4' : '↧ MP3'; return; }
      button.textContent = '✓ Na fila';
      toast(`${mediaType === 'video' ? 'Vídeo MP4' : 'Música MP3'} adicionado(a): ${video.title}. Edite em Downloads.`);
      refreshJobs();
    } catch (error) {
      button.disabled = false; button.textContent = mediaType === 'video' ? '▣ MP4' : '↧ MP3';
      toast(error.message, true);
    }
  }
  save.addEventListener('click', () => queueFromSearch('audio', save));
  saveVideo.addEventListener('click', () => queueFromSearch('video', saveVideo));
  actions.append(play, save, saveVideo);
  card.append(image, details, actions);
  return card;
}
// Paginação de 20 em 20; carrega a próxima página apenas perto do fim da tela.
// O navegador não recebe centenas de resultados numa única resposta.
const RESULTS_PER_PAGE = 20;
let currentQuery = '';
let nextOffset = 0;
let hasMore = false;
let loadingResults = false;
let pageInFlight = null;
let searchGeneration = 0;
let totalResults = 0;
let shownIds = new Set();
async function loadNextSearchPage(generation = searchGeneration) {
  if (loadingResults) return pageInFlight;
  if (!hasMore || !currentQuery || generation !== searchGeneration) return;
  loadingResults = true;
  const thisPage = pageInFlight = (async () => {
  $('search-more').disabled = true;
  $('search-scroll-status').textContent = 'Carregando mais músicas...';
  try {
    const result = await api('/api/search', 'POST', {
      query: currentQuery, offset: nextOffset, limit: RESULTS_PER_PAGE
    });
    if (generation !== searchGeneration) return; // Resposta de pesquisa antiga.
    const fragment = document.createDocumentFragment();
    for (const video of result.results) {
      if (shownIds.has(video.id)) continue;
      shownIds.add(video.id);
      searchTracks.push(video);
      fragment.append(searchCard(video));
      totalResults++;
    }
    $('search-results').append(fragment);
    nextOffset = result.next_offset;
    hasMore = Boolean(result.has_more) && result.results.length > 0;
    $('search-pagination').hidden = !hasMore;
    $('search-scroll-status').textContent = `${totalResults} resultados exibidos${hasMore ? ' • role para ver mais' : ''}`;
    msg('search-msg', totalResults ? `${totalResults} músicas encontradas${hasMore ? ' • role dentro da lista para carregar mais' : ''}.` : 'Nenhum resultado. Tente outro termo.');
  } catch (e) {
    if (generation !== searchGeneration) return;
    msg('search-msg', `Falha ao buscar: ${e.message}`, true);
    $('search-pagination').hidden = false;
    $('search-scroll-status').textContent = 'Não foi possível carregar esta página. Tente novamente.';
  } finally {
    if (generation === searchGeneration) {
      loadingResults = false;
      $('search-more').disabled = false;
    }
  }
  })();
  try { return await thisPage; }
  finally { if (pageInFlight === thisPage) pageInFlight = null; }
}
$('search-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const query = $('search-query').value.trim();
  if (query.length < 2) {msg('search-msg','Digite pelo menos 2 caracteres.',true);return;}
  // Fechar o teclado virtual ao pesquisar: libera espaço para navegar na lista.
  if (mobileNavigation.matches && document.activeElement === $('search-query')) $('search-query').blur();
  searchGeneration++;
  currentQuery = query;
  nextOffset = 0;
  hasMore = true;
  loadingResults = false;
  totalResults = 0;
  shownIds.clear();
  searchTracks = []; // playerList anterior permanece até escolher outra faixa.
  $('search-results').replaceChildren();
  $('results-scroll').scrollTop = 0;
  $('search-pagination').hidden = false;
  msg('search-msg', 'Buscando músicas no YouTube...');
  loadNextSearchPage(searchGeneration);
});
$('search-more').addEventListener('click', () => loadNextSearchPage());
// Observa o fim da LISTA, não o fim da página. Um scroll fica contido na lista.
const searchScroll = $('results-scroll');
let loadObserver = null;
if ('IntersectionObserver' in window) {
  loadObserver = new IntersectionObserver((entries) => {
    if (entries.some(entry => entry.isIntersecting) && !$('pane-search').hidden) {
      loadNextSearchPage();
    }
  }, {root: searchScroll, rootMargin: '350px 0px'});
  loadObserver.observe($('search-sentinel'));
}
function checkSearchScroll() {
  if ($('pane-search').hidden || loadingResults || !hasMore) return;
  if (searchScroll.scrollHeight - searchScroll.scrollTop - searchScroll.clientHeight < 400) loadNextSearchPage();
}
searchScroll.addEventListener('scroll', checkSearchScroll, {passive:true});
$('analyze-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  $('analyze-btn').disabled = true;
  $('preview').hidden = true;
  msg('analyze-msg', 'Analisando metadados do vídeo...');
  try {
    const result = await api('/api/analyze','POST',{url:$('video-url').value.trim()});
    selectedUrl = result.url;
    $('music-artist').value = result.artist || '';
    $('music-track').value = result.track || '';
    $('music-album').value = result.album || '';
    $('metadata-info').textContent = `Artista sugerido a partir do canal: ${result.channel || 'não informado'}. Fonte: ${result.source}. Se o canal não for o cantor, edite antes de baixar.`;
    const chip = $('confidence');
    chip.className = 'status-chip ' + result.confidence;
    chip.textContent = ({alta:'Metadados confiáveis',media:'Nome do canal • confira',revisar:'Conferir artista'})[result.confidence] || 'Conferir';
    const cover = $('video-cover');
    if (result.thumbnail && result.thumbnail.startsWith('https://')) { cover.src = result.thumbnail; cover.hidden = false; }
    else { cover.hidden = true; cover.removeAttribute('src'); }
    $('preview').hidden = false;
    $('preview').scrollIntoView({behavior:'smooth', block:'start'});
    msg('analyze-msg', 'Vídeo analisado. Revise os campos e inicie o download.');
    msg('download-msg');
    if (!result.artist) $('music-artist').focus();
  } catch (e) {msg('analyze-msg', e.message, true);}
  finally {busy = false;$('analyze-btn').disabled = false;}
});
async function queueFromLink(mediaType) {
  const artist = $('music-artist').value.trim();
  const track = $('music-track').value.trim();
  const album = $('music-album').value.trim();
  if (!selectedUrl) return;
  if (artist.length < 2 || !track) {msg('download-msg', 'Informe o canal/artista e o nome do arquivo.', true);return;}
  const button = $(mediaType === 'video' ? 'download-video-btn' : 'download-btn');
  button.disabled = true;
  msg('download-msg', 'Adicionando à fila...');
  try {
    const item = await createDownloadJob({url:selectedUrl,artist,track,album,media_type:mediaType,video_quality:Number($('video-quality').value)});
    if (!item) {msg('download-msg', 'Download cancelado. Nenhum arquivo foi alterado.'); return;}
    msg('download-msg', `${mediaType === 'video' ? 'Vídeo' : 'Música'} adicionado(a) à fila (tarefa #${item.id}).`);
    $('preview').hidden = true;
    selectedUrl = null;
    $('video-url').value = '';
    activateTab('downloads');
    refreshJobs();
  } catch(e) {msg('download-msg', e.message, true);}
  finally {button.disabled = false;}
}
$('download-btn').addEventListener('click', () => queueFromLink('audio'));
$('download-video-btn').addEventListener('click', () => queueFromLink('video'));
$('clear-preview').addEventListener('click', () => { $('preview').hidden=true; selectedUrl=null; });
$('refresh-jobs').addEventListener('click', refreshJobs);

const statusLabels = {
  pendente:'Na fila',baixando:'Baixando',convertendo:'Convertendo',finalizando:'Finalizando',
  cancelando:'Cancelando',concluido:'Concluído',existente:'Já existia',ausente:'Arquivo ausente',
  substituido:'Substituído',erro:'Erro',cancelado:'Cancelado'
};
let editTarget = null;
let deleteTarget = null;
function closeModal(id) { const dlg = $(id); if (dlg.open) dlg.close(); }
for (const id of ['edit-dialog','delete-dialog']) {
  $(id).addEventListener('click', ev => {
    if (ev.target === $(id)) closeModal(id);
  });
}
$('edit-close').addEventListener('click', () => closeModal('edit-dialog'));
$('edit-dismiss').addEventListener('click', () => closeModal('edit-dialog'));
$('delete-close').addEventListener('click', () => closeModal('delete-dialog'));
$('delete-dismiss').addEventListener('click', () => closeModal('delete-dialog'));
function openEdit(job) {
  editTarget = job;
  $('edit-artist').value = job.artist;
  $('edit-track').value = job.track;
  $('edit-album').value = job.album || '';
  const suffix = job.media_type === 'video' ? 'MP4' : 'MP3';
  $('edit-hint').textContent = job.status === 'concluido' ?
    `Arquivo ${suffix} salvo: alterar nomes também move o arquivo e atualiza os metadados.` :
    `Se estiver baixando, as alterações serão aplicadas ao salvar o ${suffix}.`;
  msg('edit-msg');
  $('edit-dialog').showModal();
  $('edit-artist').focus();
}
$('edit-form').addEventListener('submit', async ev => {
  ev.preventDefault(); if (!editTarget) return;
  const artist = $('edit-artist').value.trim();
  const track = $('edit-track').value.trim();
  const album = $('edit-album').value.trim();
  if (artist.length < 2 || !track) { msg('edit-msg', 'Preencha cantor e música.', true); return; }
  $('edit-save').disabled = true;
  msg('edit-msg', 'Salvando alterações...');
  try {
    await api(`/api/jobs/${editTarget.id}`, 'PATCH', {artist,track,album});
    closeModal('edit-dialog');
    toast('Nome da música e artista atualizados.');
    await refreshJobs();
  } catch(error) { msg('edit-msg', error.message, true); }
  finally { $('edit-save').disabled = false; }
});
function openDelete(job) {
  deleteTarget = job;
  const suffix = job.media_type === 'video' ? 'MP4' : 'MP3';
  $('delete-desc').textContent = job.status === 'concluido' ?
    `Excluir definitivamente "${job.track}" de ${job.artist}? O arquivo ${suffix} será apagado do servidor.` :
    `Remover o registro "${job.track}" do histórico? Nenhum arquivo existente será apagado.`;
  $('delete-confirm').textContent = job.status === 'concluido' ? `Excluir ${suffix} e registro` : 'Remover registro';
  msg('delete-msg');
  $('delete-dialog').showModal();
}
$('delete-confirm').addEventListener('click', async () => {
  if (!deleteTarget) return;
  $('delete-confirm').disabled = true;
  msg('delete-msg', 'Excluindo...');
  try {
    await api(`/api/jobs/${deleteTarget.id}`, 'DELETE');
    closeModal('delete-dialog');
    toast('Item excluído.');
    await refreshJobs();
  } catch(error) { msg('delete-msg', error.message, true); }
  finally { $('delete-confirm').disabled = false; }
});
function smallButton(text, className, onClick) {
  const el = document.createElement('button');
  el.type = 'button'; el.className = `mini-action ${className}`; el.textContent = text;
  el.addEventListener('click', onClick);
  return el;
}
function jobElement(job) {
  const wrap = document.createElement('article'); wrap.className = 'job';
  const icon = document.createElement('div'); icon.className = 'job-symbol';
  icon.textContent = job.media_type === 'video' ? '▣' : '♫';
  if (job.media_type === 'audio' && /^[A-Za-z0-9_-]{11}$/.test(job.video_id)) {
    const art = document.createElement('img'); art.className = 'job-artwork'; art.alt = ''; art.loading = 'lazy'; art.decoding = 'async';
    const remote = `https://i.ytimg.com/vi/${job.video_id}/hqdefault.jpg`;
    art.src = job.status === 'concluido'
      ? `/api/jobs/${job.id}/cover?rev=${encodeURIComponent(job.updated_at || '')}` : remote;
    art.onerror = () => { art.onerror = null; if (art.src !== remote) art.src = remote; else art.remove(); };
    icon.replaceChildren(art);
  }
  const info = document.createElement('div'); info.className = 'job-main';
  const name = document.createElement('div'); name.className = 'job-name'; name.textContent = job.track;
  const artist = document.createElement('div'); artist.className = 'job-artist'; artist.textContent = job.artist;
  info.append(name,artist);
  const mediaBadge = document.createElement('div'); mediaBadge.className = 'media-label';
  mediaBadge.textContent = job.media_type === 'video' ? `▣ Vídeo MP4 • até ${job.video_quality || 720}p` : '♫ Música MP3';
  info.append(mediaBadge);
  if (job.album) {const album=document.createElement('div');album.className='job-album';album.textContent=`Álbum: ${job.album}`;info.append(album);}
  if (['baixando','convertendo','finalizando','cancelando'].includes(job.status)) {
    const bar = document.createElement('div'); bar.className = 'progress';
    const fill = document.createElement('span'); fill.style.width = `${job.progress || 0}%`;
    bar.append(fill); info.append(bar);
    const progressText=document.createElement('div'); progressText.className='job-progress-label'; progressText.textContent=`${job.progress || 0}%`;info.append(progressText);
  }
  if (job.message) {const el=document.createElement('div');el.className='job-msg';el.textContent=job.message;info.append(el);}
  if (job.path && job.status === 'concluido') {
    const el=document.createElement('div');el.className='job-path';el.textContent='📁 '+job.path;info.append(el);
  }
  const side = document.createElement('div'); side.className = 'job-side';
  const chip = document.createElement('span'); chip.className = 'status-chip '+job.status;
  chip.textContent = statusLabels[job.status] || job.status;side.append(chip);
  const controls = document.createElement('div');controls.className='job-actions';
  if (!['cancelando','cancelado','finalizando'].includes(job.status)) {
    controls.append(smallButton('✎ Editar','edit',() => openEdit(job)));
  }
  if (job.status === 'concluido' && job.media_type === 'audio') {
    const coverButton = smallButton('Atualizar capa','artwork-action',async () => {
      coverButton.disabled = true; coverButton.textContent = 'Buscando capa...';
      try {
        await api(`/api/jobs/${job.id}/cover`,'POST');
        toast('Capa embutida no MP3 com sucesso.');
        await refreshJobs();
        if (currentVideo?.id === job.video_id) {
          downloadedArtwork.set(job.video_id, {...job, updated_at:Date.now()});
          $('player-cover').src = `/api/jobs/${job.id}/cover?rev=${Date.now()}`;
          publishSystemMediaMetadata();
        }
      } catch(error) { toast(error.message,true); }
      finally { coverButton.disabled = false; coverButton.textContent = 'Atualizar capa'; }
    });
    controls.append(coverButton);
  }
  if (['pendente','baixando','convertendo','finalizando'].includes(job.status)) {
    controls.append(smallButton('✕ Cancelar','cancel',async () => {
      try { await api(`/api/jobs/${job.id}/cancel`,'POST'); toast(`Cancelamento solicitado: ${job.track}`);refreshJobs(); }
      catch(e){toast(e.message,true);}
    }));
  } else if (['concluido','cancelado','erro','existente','ausente','substituido'].includes(job.status)) {
    controls.append(smallButton('▤ Excluir','delete',() => openDelete(job)));
  }
  side.append(controls);
  wrap.append(icon,info,side); return wrap;
}
let lastJobsSignature = '';
let jobsLimit = 60;
$('more-jobs').addEventListener('click', () => { jobsLimit = Math.min(500, jobsLimit + 60); refreshJobs(); });
let refreshBusy = false;
async function refreshJobs() {
  if ($('app').hidden || refreshBusy) return;
  refreshBusy = true;
  try {
    const response = await api(`/api/jobs?limit=${jobsLimit}`);
    const list = $('jobs-list');
    // Avoid rebuilding 60+ download cards every 2.5s if nothing has changed.
    // Keeps scroll fluid and reduces image decoding on slower mobile devices.
    const signature = JSON.stringify([response.has_more, response.jobs]);
    if (signature === lastJobsSignature) return;
    lastJobsSignature = signature;
    const scrollContainer = list; // Preserve the independently scrollable mobile Downloads list.
    const oldTop = scrollContainer.scrollTop;
    downloadedArtwork.clear();
    for (const job of response.jobs) {
      if (job.media_type === 'audio' && job.status === 'concluido' && !downloadedArtwork.has(job.video_id)) {
        downloadedArtwork.set(job.video_id, job);
      }
    }
    const nodes = response.jobs.map(job => jobElement(job));
    if (!nodes.length) {
      const empty = document.createElement('p'); empty.className = 'empty';
      empty.textContent = 'Nenhum download ainda. Pesquise e escolha MP3 ou MP4.';
      nodes.push(empty);
    }
    list.replaceChildren(...nodes);
    $('more-jobs').hidden = !response.has_more || jobsLimit >= 500;
    if (scrollContainer) scrollContainer.scrollTop = oldTop;
  } catch(e) {
    if (e.message.includes('login')) {showAuth(false);return;}
    $('jobs-list').textContent = 'Não foi possível carregar a fila: '+e.message;
  } finally { refreshBusy = false; }
}
loadState();
setInterval(() => { if (!document.hidden && !$('pane-downloads').hidden) refreshJobs(); }, 2500);

// Recover thumbnails for completed MP3s from earlier versions without re-downloading music.
let coverRepairPolling = false;
async function refreshCoverRepairStatus() {
  if (coverRepairPolling || $('app').hidden) return;
  coverRepairPolling = true;
  try {
    const state = await api('/api/covers/repair');
    $('repair-covers').disabled = Boolean(state.running);
    const node = $('cover-repair-status');
    const hasProgress = state.total > 0 || state.running || state.message;
    node.hidden = !hasProgress;
    if (hasProgress) node.textContent = `${state.message || 'Atualizando capas'} ${state.processed}/${state.total} • incorporadas: ${state.updated} • já tinham: ${state.skipped} • falhas: ${state.failed}`;
    if (!state.running && state.processed && state.processed === state.total) await refreshJobs();
  } catch (_) { /* A fila de downloads continua funcionando mesmo sem esta consulta. */ }
  finally { coverRepairPolling = false; }
}
$('repair-covers').addEventListener('click', async () => {
  $('repair-covers').disabled = true;
  $('cover-repair-status').hidden = false;
  $('cover-repair-status').textContent = 'Preparando atualização das capas...';
  try { await api('/api/covers/repair','POST'); await refreshCoverRepairStatus(); }
  catch (error) { toast(error.message,true); $('repair-covers').disabled = false; }
});
setInterval(() => { if (!document.hidden && !$('pane-downloads').hidden) refreshCoverRepairStatus(); }, 5000);
