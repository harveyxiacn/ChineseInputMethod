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
let recorder = null;
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
pinyin.addEventListener("keydown", (event) => {
  if (composing || event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey) return;
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
  $("record-label").textContent = { idle: "Start recording", requesting: "Cancel microphone request", recording: "Stop & transcribe", transcribing: "Transcribing…" }[state];
  if (state !== "recording") $("record-time").textContent = "";
}
function releaseMicrophone() {
  clearInterval(recordingTimer);
  microphone?.getTracks().forEach((track) => track.stop());
  microphone = null;
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
$("record").addEventListener("click", async () => {
  if (voiceState === "requesting") { ++voiceToken; setVoiceState("idle"); status("Microphone request canceled."); return; }
  if (voiceState === "recording") { setVoiceState("transcribing"); recorder.stop(); releaseMicrophone(); return; }
  if (voiceState !== "idle") return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    status("Microphone recording needs a supported browser on localhost or HTTPS. You can also upload an audio file.", true); return;
  }
  const token = ++voiceToken;
  const language = $("language").value;
  const script = $("script").value;
  setVoiceState("requesting");
  status("Allow microphone access in your browser to begin.");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (token !== voiceToken) { stream.getTracks().forEach((track) => track.stop()); return; }
    microphone = stream;
    const mimeType = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((type) => MediaRecorder.isTypeSupported(type));
    const activeRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorder = activeRecorder;
    const chunks = [];
    let failed = false;
    activeRecorder.addEventListener("dataavailable", (event) => { if (event.data.size) chunks.push(event.data); });
    activeRecorder.addEventListener("error", () => { failed = true; releaseMicrophone(); setVoiceState("idle"); status("The browser could not finish recording. Please try again.", true); });
    activeRecorder.addEventListener("stop", () => {
      if (failed) return;
      releaseMicrophone();
      const type = activeRecorder.mimeType || "audio/webm";
      const extension = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
      const file = new File(chunks, `recording.${extension}`, { type });
      if (!file.size) { setVoiceState("idle"); status("The recording was empty. Please try again.", true); return; }
      transcribe(file, language, script);
    }, { once: true });
    recorder.start(1000);
    recordingStarted = Date.now();
    setVoiceState("recording");
    recordingTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - recordingStarted) / 1000);
      $("record-time").textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
      if (seconds >= 115 && voiceState === "recording") {
        setVoiceState("transcribing");
        activeRecorder.stop();
        releaseMicrophone();
      }
    }, 500);
    status("Listening. Select Stop & transcribe when you’re finished. Recording stops automatically after 1:55.");
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
window.addEventListener("pagehide", () => { ++voiceToken; releaseMicrophone(); });
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
