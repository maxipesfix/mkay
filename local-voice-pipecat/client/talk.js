// The local voice page's client: asks /start for a session, connects to this server over
// SmallWebRTC, plays the bot's audio, draws both directions as a circular waveform, and shows
// the conversation. Bundled by `npm run build` to ../static/talk.js (committed, so running
// the voice client needs no Node). mkay-cloud's account page has the same page on Daily.
import { PipecatClient } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';
import { CircularWaveformCanvas, WaveformState } from './circular-waveform.ts';

const $ = (id) => document.getElementById(id);
const button = $('talk'), statusText = $('status'), dot = $('dot'), log = $('log'), error = $('error'),
  audio = $('bot-audio');
// Shown again when a session ends: the page's own status.
const idle = { text: statusText.textContent, state: dot.className.replace(/^dot\s*/, '') };
let client = null;

// ?wave=0 in the page address leaves Web Audio out entirely (the waveform then only animates).
const params = new URLSearchParams(location.search);
const waveAudio = params.get('wave') !== '0';

// The waveform behind the controls shows the microphone and the bot's voice, mixed.
const stage = $('stage');
const wave = new CircularWaveformCanvas($('wave'), { width: stage.clientWidth, height: stage.clientWidth });
new ResizeObserver(() => wave.updateCanvasSize(stage.clientWidth, stage.clientWidth)).observe(stage);
wave.startVisualization();  // The idle ring shows until a session brings audio.
let mix = null;  // { context, output, sources }: the session's audio tracks, mixed into one.

function startMix() {
  // Made during the click, so Safari and phones let the audio contexts run.
  if (!mix) {
    const context = new AudioContext();
    mix = { context, output: context.createMediaStreamDestination(), sources: new Map() };
    wave.connectToAudioTrack(mix.output.stream.getAudioTracks()[0]);
  }
  mix.context.resume().catch(() => {});
}

function visualize(track) {
  if (!mix || mix.sources.has(track.id)) return;
  const source = mix.context.createMediaStreamSource(new MediaStream([track]));
  source.connect(mix.output);
  mix.sources.set(track.id, source);
}

function stopMix() {
  if (mix) {
    mix.sources.forEach((source) => source.disconnect());
    mix.sources.clear();
  }
  wave.setState(WaveformState.IDLE);
}

// The menus for this session's services: what the server offers (API keys set, a local LLM
// server running), with the last choice remembered in this browser.
const menus = [...document.querySelectorAll('[data-choice]')];
fetch('/options').then((response) => response.json()).then((options) => {
  for (const menu of menus) {
    const key = menu.dataset.choice, groups = new Map();
    for (const option of options[key] || []) {
      const element = new Option(option.label, option.id);
      if (!option.group) { menu.append(element); continue; }
      if (!groups.has(option.group)) {
        const group = document.createElement('optgroup');
        group.label = option.group;
        groups.set(option.group, group);
        menu.append(group);
      }
      groups.get(option.group).append(element);
    }
    let saved = null;
    try { saved = localStorage.getItem(`mkay.${key}`); } catch { /* storage unavailable */ }
    const ids = [...menu.options].map((option) => option.value);
    menu.value = ids.includes(saved) ? saved : options.default?.[key];
    menu.addEventListener('change', () => {
      try { localStorage.setItem(`mkay.${key}`, menu.value); } catch { /* not remembered */ }
    });
  }
  $('choices').hidden = false;
}).catch(() => { /* an older server: its defaults apply */ });

const choices = () => Object.fromEntries(menus.filter((menu) => menu.value).map((menu) => [menu.dataset.choice, menu.value]));
const lockMenus = (locked) => menus.forEach((menu) => { menu.disabled = locked; });

function show(state, text) {
  statusText.textContent = text;
  dot.className = `dot ${state}`;
}

function line(who, text) {
  if (!text || !text.trim()) return;
  const last = log.lastElementChild;
  if (who === 'bot' && last && last.dataset.who === 'bot' && last.dataset.open) {
    last.lastChild.textContent += ` ${text.trim()}`;  // Sentences of one reply arrive one by one.
  } else {
    const item = document.createElement('li');
    item.dataset.who = who;
    if (who === 'bot') item.dataset.open = '1';
    const name = document.createElement('span');
    name.className = 'who';
    name.textContent = who === 'bot' ? 'm’kay' : 'You';
    item.append(name, document.createTextNode(text.trim()));
    log.append(item);
  }
  log.hidden = false;
  log.scrollTop = log.scrollHeight;
}

function closeReply() {
  const last = log.lastElementChild;
  if (last) delete last.dataset.open;
}

const NO_MIC = 'Allow the microphone for this site, then try again.';

async function start() {
  error.hidden = true;
  button.disabled = true;
  show('busy', 'Connecting…');
  let micRefused = false, live = false;
  // Events of a session that has ended (late ones arrive while it disconnects) change nothing.
  const ours = (handler) => (...args) => { if (client === current) handler(...args); };
  const current = client = new PipecatClient({
    transport: new SmallWebRTCTransport(),
    enableMic: true,
    enableCam: false,
    callbacks: {
      onBotReady: ours(() => { show('on', 'Listening'); wave.setState(WaveformState.AUDIO); }),
      // The session ended on the server (idle, replaced by a newer one, a failure).
      onBotDisconnected: ours(() => stop('The session ended.')),
      onDisconnected: ours(() => stop('The session ended.')),
      onTrackStarted: ours((track, participant) => {
        if (track.kind !== 'audio') return;
        // SmallWebRTC reports the bot's track without a participant (Daily includes one).
        if (!participant?.local) {
          audio.srcObject = new MediaStream([track]);
          audio.play().catch(() => {});
        }
        visualize(track);
      }),
      onUserStartedSpeaking: ours(() => {
        closeReply();
        show('on', 'Hearing you');
        wave.setState(WaveformState.AUDIO);
      }),
      onUserStoppedSpeaking: ours(() => { show('busy', 'Thinking…'); wave.setState(WaveformState.THINKING); }),
      onBotStartedSpeaking: ours(() => { show('on', 'Speaking'); wave.setState(WaveformState.AUDIO); }),
      onBotStoppedSpeaking: ours(() => show('on', 'Listening')),
      onUserTranscript: ours((data) => { if (data.final) line('you', data.text); }),
      onBotOutput: ours((data) => {
        // Spoken sentences arrive when queued ("new") and once spoken ("completed"); show
        // what was actually said, so a reply cut off by the user shows only its spoken part.
        if (data.aggregated_by === 'word' || (data.will_be_spoken && data.spoken_status !== 'completed')) return;
        line('bot', data.spoken_progress?.accumulated_text || data.text);
      }),
      onDeviceError: (e) => {
        if (e.devices && !e.devices.includes('mic')) return;
        micRefused = true;
        if (live && client === current) fail(NO_MIC);  // For example, the microphone was unplugged.
      },
    },
  });
  // Unlock audio playback while the click still counts as a user gesture (Safari, phones).
  audio.play().catch(() => {});
  if (waveAudio) startMix();
  lockMenus(true);
  try {
    // Ask for the microphone first: a session without it could not hear the user.
    await current.initDevices();
    if (micRefused || client !== current) return fail(NO_MIC);
    await current.startBotAndConnect({ endpoint: '/start', requestData: choices() });
    if (client !== current) return current.disconnect();  // Ended while connecting.
    live = true;
    button.textContent = 'End';
    button.classList.add('secondary');
    button.disabled = false;
  } catch (e) {
    if (client === current) fail(micRefused ? NO_MIC : await reason(e));
  }
}

async function reason(e) {
  if (e instanceof Response) {
    try { return (await e.json()).detail || e.statusText; } catch { return e.statusText; }
  }
  return (e && e.message) || 'Could not connect. Please try again.';
}

function fail(message) {
  error.textContent = message;
  error.hidden = false;
  stop();
}

function stop(status = idle.text) {
  // Update the page first: disconnect() can take long or never settle when the bot already
  // left (the client disconnects itself then too).
  const current = client;
  stopped(status);
  if (current) current.disconnect().catch(() => { /* already gone */ });
}

function stopped(status = idle.text) {
  client = null;
  audio.srcObject = null;
  stopMix();
  lockMenus(false);
  closeReply();
  show(status === idle.text ? idle.state : '', status);
  button.textContent = 'Start talking';
  button.classList.remove('secondary');
  button.disabled = false;
}

button.addEventListener('click', () => (client ? stop() : start()));
window.addEventListener('pagehide', () => { if (client) client.disconnect(); });
