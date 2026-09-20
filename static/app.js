"use strict";
const $ = (id) => document.getElementById(id);
const editor = $("document");
const pinyin = $("pinyin");
let selection = { start: 0, end: 0 };
let candidates = [];
let selected = 0;
let candidateVersion = 0;
let composing = false;
let candidateTimer;
let audioContext = null;
let capture = null;
let liveSession = null;
let microphone = null;
let recordingTimer;
let recordingStarted;
let voiceState = "idle";
let voiceToken = 0;
let speechAvailable = false;

function status(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}
function rememberSelection() {
  selection = { start: editor.selectionStart, end: editor.selectionEnd };
}
function updateCount() {
  const count = Array.from(editor.value).length;
  $("character-count").textContent = `${count} character${count === 1 ? "" : "s"}`;
}
function insertText(text) {
  editor.setRangeText(text, selection.start, selection.end, "end");
  rememberSelection();
  updateCount();
}
for (const event of ["select", "keyup", "click", "blur", "input"]) editor.addEventListener(event, rememberSelection);
editor.addEventListener("input", updateCount);
function drawCandidates(message = "Your characters will appear here.") {
  const container = $("candidates");
  container.replaceChildren();
  if (!candidates.length) {
    const hint = document.createElement("span");
    hint.className = "candidate-empty";
    hint.textContent = message;
    container.append(hint);
    return;
  }
  candidates.forEach((candidate, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `candidate${selected === index ? " selected" : ""}`;
    button.setAttribute("aria-label", `${index + 1}: ${candidate.text}, ${candidate.pinyin || ""}`);
    button.setAttribute("aria-pressed", String(selected === index));
    const number = document.createElement("span");
    number.className = "candidate-index";
    number.textContent = index + 1;
    button.append(number, document.createTextNode(candidate.text));
    button.addEventListener("click", () => chooseCandidate(index));
    container.append(button);
  });
}
function resetPinyin() {
  clearTimeout(candidateTimer);
  candidateVersion++;
  pinyin.value = "";
  candidates = [];
  selected = 0;
  drawCandidates();
}
function chooseCandidate(index) {
  if (!candidates[index]) return;
  insertText(candidates[index].text);
  resetPinyin();
  pinyin.focus();
}
async function requestCandidates(version) {
  const query = pinyin.value.trim();
  if (!query) return;
  try {
    const response = await fetch(`/api/candidates?${new URLSearchParams({ q: query, script: $("script").value })}`);
    const data = await readResponse(response);
    if (version !== candidateVersion) return;
    candidates = (data.candidates || []).slice(0, 9);
    selected = 0;
    drawCandidates("No match yet. Try another spelling or press Enter to insert as typed.");
  } catch (error) {
    if (version === candidateVersion) drawCandidates(`Could not load candidates: ${error.message}`);
  }
}
function scheduleCandidates() {
  clearTimeout(candidateTimer);
  const version = ++candidateVersion;
  candidates = [];
  selected = 0;
  drawCandidates(pinyin.value.trim() ? "Finding characters…" : undefined);
  if (!composing && pinyin.value.trim()) candidateTimer = setTimeout(() => requestCandidates(version), 100);
}
pinyin.addEventListener("compositionstart", () => { composing = true; ++candidateVersion; clearTimeout(candidateTimer); candidates = []; drawCandidates("Finish composing to see candidates."); });
pinyin.addEventListener("compositionend", () => { composing = false; scheduleCandidates(); });
pinyin.addEventListener("input", () => { if (!composing) scheduleCandidates(); });
$("script").addEventListener("change", () => { scheduleCandidates(); status("Character style applies to new pinyin and voice input."); });
let doubleQuoteOpen = true;
let singleQuoteOpen = true;
const chinesePunctuation = { ",": "，", ".": "。", "?": "？", "!": "！", ":": "：", ";": "；",
  "(": "（", ")": "）", "[": "【", "]": "】", "<": "《", ">": "》", "\\": "、", "^": "……", "_": "——" };
pinyin.addEventListener("keydown", (event) => {
  if (composing || event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey) return;
  let punctuation = chinesePunctuation[event.key];
  if (event.key === '"') { punctuation = doubleQuoteOpen ? "“" : "”"; doubleQuoteOpen = !doubleQuoteOpen; }
  if (event.key === "'" && !pinyin.value) { punctuation = singleQuoteOpen ? "‘" : "’"; singleQuoteOpen = !singleQuoteOpen; }
  if (event.key === "." && /[0-9]$/.test(pinyin.value)) punctuation = null;
  if (punctuation) {
    event.preventDefault();
    insertText((candidates[selected]?.text || pinyin.value) + punctuation);
    resetPinyin(); return;
  }
  if (event.key === "Escape") { event.preventDefault(); resetPinyin(); }
  else if (event.key === "Enter" && pinyin.value) {
    event.preventDefault(); insertText(pinyin.value); resetPinyin();
  } else if (event.key === " " && candidates.length) {
    event.preventDefault(); chooseCandidate(selected);
  } else if (/^[1-9]$/.test(event.key) && candidates[Number(event.key) - 1]) {
    event.preventDefault(); chooseCandidate(Number(event.key) - 1);
  } else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key) && candidates.length) {
    event.preventDefault();
    selected = (selected + (["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1) + candidates.length) % candidates.length;
    drawCandidates();
  }
});
async function readResponse(response) {
  let data;
  try { data = await response.json(); } catch { throw new Error(`The local server returned an unreadable response (${response.status}).`); }
  if (!response.ok) {
    const message = data.error || data.detail || `Request failed (${response.status}).`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return data;
}
function setVoiceState(state) {
  voiceState = state;
  const busy = state !== "idle";
  $("language").disabled = busy;
  $("script").disabled = busy;
  $("audio-file").disabled = busy || !speechAvailable;
  $("record").disabled = state === "transcribing" || !speechAvailable;
  $("record").classList.toggle("recording", state === "recording");
  $("record-label").textContent = { idle: "Start recording", requesting: "Cancel microphone request", recording: "Stop & finish", transcribing: "Transcribing…" }[state];
  if (state !== "recording") $("record-time").textContent = "";
}
function releaseMicrophone() {
  clearInterval(recordingTimer);
  microphone?.getTracks().forEach((track) => track.stop());
  microphone = null;
  capture?.disconnect();
  capture = null;
  if (audioContext) void audioContext.close();
  audioContext = null;
}
async function transcribe(file, language, script) {
  if (file.size > 16 * 1024 * 1024) {
    setVoiceState("idle");
    $("audio-file").value = "";
    status("This recording is larger than 16 MiB. Choose a smaller audio file, up to 2 minutes long.", true);
    return;
  }
  setVoiceState("transcribing");
  status("Transcribing locally. The first recording may take longer while the model loads.");
  try {
    const form = new FormData();
    form.append("file", file, file.name || "recording.webm");
    form.append("language", language);
    form.append("script", script);
    const data = await readResponse(await fetch("/api/transcribe", { method: "POST", body: form }));
    if (!data.text?.trim()) status("No speech was recognized. Try a clearer recording.");
    else { insertText(data.text); status("Transcription added to your text."); }
  } catch (error) { status(error.message, true); }
  finally { setVoiceState("idle"); $("audio-file").value = ""; }
}
// WAV snapshots are independently decodable, including while capture continues.
function recordingFile(session) {
  const buffer = new ArrayBuffer(44 + session.samples * 2);
  const view = new DataView(buffer);
  const tag = (offset, text) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  tag(0, "RIFF"); view.setUint32(4, buffer.byteLength - 8, true); tag(8, "WAVE");
  tag(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, session.rate, true);
  view.setUint32(28, session.rate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); tag(36, "data"); view.setUint32(40, session.samples * 2, true);
  let offset = 44;
  for (const chunk of session.chunks) for (const sample of chunk) {
    view.setInt16(offset, Math.max(-1, Math.min(1, sample)) * 32767, true); offset += 2;
  }
  return new File([buffer], "recording.wav", { type: "audio/wav" });
}
async function updateLive(session, final = false) {
  if (session.token !== voiceToken || session.busy) return;
  session.busy = true;
  try {
    const form = new FormData();
    form.append("file", recordingFile(session));
    form.append("language", session.language); form.append("script", session.script);
    const data = await readResponse(await fetch("/api/transcribe", { method: "POST", body: form, signal: session.controller.signal }));
    if (session.token !== voiceToken) return;
    $("live-transcript").textContent = data.text || "";
    if (final) {
      if (data.text?.trim()) { insertText(data.text); status("Transcription added to your text."); }
      else status("No speech was recognized. Try a clearer recording.");
    } else status("Listening. Live text may change as you continue speaking.");
  } catch (error) {
    if (session.token !== voiceToken) return;
    status(final ? error.message : `Live preview unavailable: ${error.message} Will retry.`, true);
  } finally {
    session.busy = false;
    if (session.token === voiceToken) {
      if (final) { setVoiceState("idle"); liveSession = null; }
      else if (session.stopped) void updateLive(session, true);
    }
  }
}
function finishRecording() {
  const session = liveSession;
  if (!session || session.stopped) return;
  session.stopped = true;
  releaseMicrophone();
  setVoiceState("transcribing"); status("Finishing local transcription…");
  if (!session.busy) void updateLive(session, true);
}
$("record").addEventListener("click", async () => {
  if (voiceState === "requesting") { ++voiceToken; releaseMicrophone(); setVoiceState("idle"); status("Microphone request canceled."); return; }
  if (voiceState === "recording") { finishRecording(); return; }
  if (voiceState !== "idle") return;
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext || !window.AudioWorkletNode) {
    status("Live recording needs a supported browser on localhost or HTTPS. You can also upload audio.", true); return;
  }
  const token = ++voiceToken;
  setVoiceState("requesting");
  $("live-transcript").textContent = "";
  status("Allow microphone access in your browser to begin.");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (token !== voiceToken) { stream.getTracks().forEach((track) => track.stop()); return; }
    microphone = stream;
    const context = new AudioContext({ sampleRate: 16000 });
    audioContext = context;
    await context.audioWorklet.addModule("/capture.js");
    if (token !== voiceToken) return;
    await context.resume();
    if (token !== voiceToken) return;
    const session = { token, chunks: [], samples: 0, rate: context.sampleRate,
      language: $("language").value, script: $("script").value, busy: false,
      stopped: false, lastPreview: 0, controller: new AbortController() };
    liveSession = session;
    capture = new AudioWorkletNode(context, "capture");
    capture.port.onmessage = ({ data }) => {
      if (session.stopped || token !== voiceToken) return;
      session.chunks.push(data); session.samples += data.length;
      if (session.samples >= session.rate * 115) finishRecording();
    };
    context.createMediaStreamSource(stream).connect(capture);
    // The processor outputs silence; connecting it keeps capture running.
    capture.connect(context.destination);
    stream.getAudioTracks().forEach(track => track.addEventListener("ended", finishRecording));
    recordingStarted = Date.now();
    setVoiceState("recording");
    recordingTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - recordingStarted) / 1000);
      $("record-time").textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
      if (seconds >= 115) { finishRecording(); return; }
      if (!session.busy && seconds - session.lastPreview >= 3 && session.samples > 0) {
        session.lastPreview = seconds; void updateLive(session);
      }
    }, 500);
    status("Listening. Live text appears below; Stop & finish inserts the final text. Maximum 1:55.");
  } catch (error) {
    if (token !== voiceToken) return;
    releaseMicrophone(); setVoiceState("idle");
    status(error.name === "NotAllowedError" ? "Microphone access was denied. Allow it in your browser settings, or upload audio." : `Could not start recording: ${error.message}`, true);
  }
});
$("audio-file").addEventListener("change", () => {
  const file = $("audio-file").files[0];
  if (file && voiceState === "idle") transcribe(file, $("language").value, $("script").value);
});
$("copy").addEventListener("click", async () => {
  if (!editor.value) { status("Add some text first."); return; }
  try { await navigator.clipboard.writeText(editor.value); status("Text copied."); }
  catch { editor.focus(); editor.select(); rememberSelection(); status("Copy is unavailable here. Your text is selected; press Ctrl+C or ⌘C."); }
});
$("download").addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([editor.value], { type: "text/plain;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = "shuangsheng.txt"; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  status("Text downloaded.");
});
$("clear").addEventListener("click", () => { editor.value = ""; selection = { start: 0, end: 0 }; updateCount(); status("Text cleared."); });
window.addEventListener("pagehide", () => { ++voiceToken; liveSession?.controller.abort(); releaseMicrophone(); });
async function checkHealth() {
  setVoiceState("idle");
  try {
    const data = await readResponse(await fetch("/api/health"));
    speechAvailable = Boolean(data.speech?.available);
    const speech = data.speech || {};
    $("speech-status").textContent = speechAvailable
      ? `${speech.loaded ? "Local speech ready" : "Local speech configured"} · ${speech.model || "Whisper"}${speech.detail ? `. ${speech.detail}` : ""}`
      : (speech.detail || "Speech model is not configured. See the README to enable local voice input.");
  } catch (error) { $("speech-status").textContent = `Could not connect to the local service: ${error.message}`; }
  setVoiceState("idle");
}
checkHealth();
