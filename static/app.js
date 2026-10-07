"use strict";
const $ = id => document.getElementById(id);
const editor = $("document"), pinyin = $("pinyin");
let selection = { start: 0, end: 0 }, candidates = [], selected = 0, candidateVersion = 0;
let composing = false, candidateTimer, settings = {}, undoStack = [], dirty = false, editorRevision = 0;
let committedContext = "", settingsQueue = Promise.resolve(), settingsVersion = 0;
let voiceState = "idle", voiceToken = 0, speechAvailable = false, liveSession = null;
let microphone = null, audioContext = null, capture = null, recordingTimer, uploadController = null;
let assistantAction = "polish", assistantSelection, assistantDocument, assistantToken = 0, assistantController = null;
const englishWords = new Set("api python javascript typescript timeout hello world docker git github linux windows macos vscode openai chatgpt http https www test email version update commit review server client token debug json html css npm pip node npm install function return class const async await sql select from where".split(" "));
function literalQuery(text) { return /[A-Z0-9@_/:\\.\-]/.test(text) || englishWords.has(text.toLowerCase()); }
function status(message, error = false) { $("status").textContent = message; $("status").classList.toggle("error", error); }
async function readResponse(response) {
  let data; try { data = await response.json(); } catch { throw new Error(`本机服务返回了无效响应 (${response.status})`); }
  if (!response.ok) throw new Error(typeof (data.error || data.detail) === "string" ? (data.error || data.detail) : `请求失败 (${response.status})`);
  return data;
}
const get = path => fetch(path).then(readResponse);
const post = (path, data, signal) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data), signal }).then(readResponse);
function rememberSelection() { const next = { start: editor.selectionStart, end: editor.selectionEnd }; const moved = next.start !== selection.start || next.end !== selection.end; selection = next; if (moved && pinyin.value && !composing) scheduleCandidates(); }
function contextText() { return editor.value.slice(Math.max(0, selection.start - 200), selection.start); }
function updateCount() { $("character-count").textContent = `${Array.from(editor.value).length} 字`; }
function saveDraft() {
  try { if (settings.save_draft) localStorage.setItem("shuangsheng-draft", editor.value); else localStorage.removeItem("shuangsheng-draft"); }
  catch { status("浏览器未能保存草稿，请复制或保存文件。", true); }
}
function checkpoint() { undoStack.push({ text: editor.value, ...selection }); if (undoStack.length > 30) undoStack.shift(); $("undo").disabled = false; }
function changed() { editorRevision++; dirty = true; updateCount(); saveDraft(); }
function insertText(text, target = selection) { checkpoint(); editor.setRangeText(text, target.start, target.end, "end"); rememberSelection(); changed(); }
for (const event of ["select", "keyup", "click", "blur", "input"]) editor.addEventListener(event, rememberSelection);
editor.addEventListener("input", changed);
$("undo").addEventListener("click", () => { const previous = undoStack.pop(); if (!previous) return; editor.value = previous.text; editor.setSelectionRange(previous.start, previous.end); rememberSelection(); changed(); $("undo").disabled = !undoStack.length; });
function drawCandidates(message = "输入拼音、英文或快捷短语。") {
  const container = $("candidates"); container.replaceChildren();
  if (!candidates.length) { const hint = document.createElement("span"); hint.className = "candidate-empty"; hint.textContent = message; container.append(hint); return; }
  candidates.forEach((candidate, index) => {
    const button = document.createElement("button"); button.type = "button";
    button.className = `candidate${selected === index ? " selected" : ""}${candidate.text === pinyin.value ? " raw" : ""}`;
    button.setAttribute("aria-label", `${index + 1}: ${candidate.text}, ${candidate.pinyin || ""}`);
    button.setAttribute("aria-pressed", String(selected === index));
    const number = document.createElement("span"); number.className = "candidate-index"; number.textContent = index + 1;
    button.append(number, document.createTextNode(candidate.text)); button.addEventListener("click", () => chooseCandidate(index)); container.append(button);
  });
}
function resetPinyin() { clearTimeout(candidateTimer); candidateVersion++; pinyin.value = ""; candidates = []; selected = 0; drawCandidates(); }
async function predict() {
  const version = ++candidateVersion;
  try { const data = await get(`/api/predict?${new URLSearchParams({ context: contextText(), script: $("script").value })}`); if (version !== candidateVersion || pinyin.value) return; candidates = data.candidates || []; selected = 0; drawCandidates(); }
  catch { /* Typing stays available when optional predictions fail. */ }
}
function chooseCandidate(index) {
  if (!candidates[index]) return;
  const query = pinyin.value, context = contextText().endsWith(committedContext) ? committedContext : "", text = candidates[index].text;
  insertText(text); committedContext = (context + text).slice(-120); resetPinyin(); pinyin.focus();
  if (!query) { void predict(); return; }
  void post("/api/commit", { query, text, context }).then(predict).catch(error => status(`文字已插入，但未能保存学习：${error.message}`, true));
}
async function requestCandidates(version) {
  const query = pinyin.value.trim(); if (!query || settings.input_mode === "english") return;
  try {
    const data = await get(`/api/candidates?${new URLSearchParams({ q: query, script: $("script").value, context: contextText(), scheme: $("scheme").value, fuzzy: String(Boolean(settings.fuzzy)), limit: String(settings.candidate_count || 9) })}`);
    if (version !== candidateVersion) return;
    candidates = (data.candidates || []).slice(0, settings.candidate_count || 9); selected = 0; drawCandidates("暂无匹配；回车可插入原文。");
  } catch (error) { if (version === candidateVersion) drawCandidates(error.message); }
}
function scheduleCandidates() { clearTimeout(candidateTimer); const version = ++candidateVersion; candidates = []; selected = 0; drawCandidates(pinyin.value ? "正在查找…" : undefined); if (!composing && settings.input_mode !== "english" && pinyin.value.trim()) candidateTimer = setTimeout(() => requestCandidates(version), 60); }
pinyin.addEventListener("compositionstart", () => { composing = true; candidateVersion++; clearTimeout(candidateTimer); candidates = []; drawCandidates("完成系统输入后显示候选。"); });
pinyin.addEventListener("compositionend", () => { composing = false; scheduleCandidates(); });
pinyin.addEventListener("input", () => { if (!composing) scheduleCandidates(); });
let doubleQuoteOpen = true, singleQuoteOpen = true;
const punctuationMap = { ",": "，", ".": "。", "?": "？", "!": "！", ":": "：", ";": "；", "(": "（", ")": "）", "[": "【", "]": "】", "<": "《", ">": "》", "\\": "、", "^": "……", "_": "——" };
pinyin.addEventListener("keydown", async event => {
  if (composing || event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey) return;
  if (settings.input_mode === "english" && !["Enter", "Escape"].includes(event.key)) return;
  if (event.altKey) { if (/^[1-9]$/.test(event.key)) { event.preventDefault(); chooseCandidate(Number(event.key) - 1); } return; }
  if (event.key === "Escape") { event.preventDefault(); resetPinyin(); return; }
  if (event.key === "Enter" && pinyin.value) { event.preventDefault(); const context = contextText().endsWith(committedContext) ? committedContext : ""; insertText(pinyin.value); committedContext = (context + pinyin.value).slice(-120); resetPinyin(); return; }
  const raw = literalQuery(pinyin.value), jyutping = $("scheme").value === "jyutping";
  if (event.key === " " && pinyin.value && raw) { event.preventDefault(); const context = contextText().endsWith(committedContext) ? committedContext : ""; insertText(pinyin.value + " "); committedContext = (context + pinyin.value + " ").slice(-120); resetPinyin(); return; }
  if (event.key === " " && pinyin.value && !raw) { event.preventDefault(); if (!candidates.length) { clearTimeout(candidateTimer); const version = ++candidateVersion; await requestCandidates(version); if (version !== candidateVersion) return; } chooseCandidate(selected); return; }
  if (event.key === " " && candidates.length) { event.preventDefault(); chooseCandidate(selected); return; }
  if (/^[1-9]$/.test(event.key) && pinyin.value && !jyutping && !raw && !/^[vr]$/.test(pinyin.value)) { event.preventDefault(); if (!candidates.length) { clearTimeout(candidateTimer); const version = ++candidateVersion; await requestCandidates(version); if (version !== candidateVersion) return; } chooseCandidate(Number(event.key) - 1); return; }
  if (["ArrowUp", "ArrowDown"].includes(event.key) && candidates.length) { event.preventDefault(); selected = (selected + (event.key === "ArrowDown" ? 1 : -1) + candidates.length) % candidates.length; drawCandidates(); return; }
  if (raw || (pinyin.value && ["@", "_", "/", "\\"].includes(event.key)) || (event.key === ";" && $("scheme").value === "shuangpin" && pinyin.value)) return;
  let punctuation = punctuationMap[event.key];
  if (event.key === '"') { punctuation = doubleQuoteOpen ? "“" : "”"; doubleQuoteOpen = !doubleQuoteOpen; }
  if (event.key === "'" && !pinyin.value) { punctuation = singleQuoteOpen ? "‘" : "’"; singleQuoteOpen = !singleQuoteOpen; }
  if (punctuation) { event.preventDefault(); if (pinyin.value && !candidates.length) { status("候选尚未就绪；请选词或按回车提交原文。", true); return; } insertText((candidates[selected]?.text || "") + punctuation); resetPinyin(); }
});
function applySettings() {
  document.documentElement.classList.remove("light", "dark", "contrast"); if (settings.theme !== "system") document.documentElement.classList.add(settings.theme || "light");
  document.documentElement.style.setProperty("--editor-size", `${settings.font_size || 18}px`);
  document.body.classList.toggle("compact", Boolean(settings.compact)); $("compact").setAttribute("aria-pressed", String(Boolean(settings.compact)));
  if (["zh", "yue", "en", "auto"].includes(settings.language)) $("language").value = settings.language;
  if (["simplified", "traditional"].includes(settings.script)) $("script").value = settings.script;
  if (["pinyin", "shuangpin", "jyutping"].includes(settings.input_scheme)) $("scheme").value = settings.input_scheme;
  $("input-mode").textContent = settings.input_mode === "english" ? "EN · 英文" : "中 · 中文";
  $("input-mode").setAttribute("aria-pressed", String(settings.input_mode === "english"));
}
function saveSettings(patch) { const version = ++settingsVersion; const result = settingsQueue.catch(() => {}).then(() => post("/api/settings", patch)); settingsQueue = result; return result.then(data => { if (version === settingsVersion) { settings = data; applySettings(); } return data; }); }
for (const [id, key] of [["script", "script"], ["language", "language"], ["scheme", "input_scheme"]]) $(id).addEventListener("change", () => { const value = $(id).value; settings[key] = value; scheduleCandidates(); void saveSettings({ [key]: value }).catch(error => status(error.message, true)); });
$("input-mode").addEventListener("click", () => void saveSettings({ input_mode: settings.input_mode === "english" ? "chinese" : "english" }).then(scheduleCandidates).catch(error => status(error.message, true)));
$("compact").addEventListener("click", () => void saveSettings({ compact: !settings.compact }).catch(error => status(error.message, true)));
function dismissAssistant() { assistantToken++; assistantController?.abort(); assistantController = null; $("assistant-run").disabled = false; }
for (const button of document.querySelectorAll("[data-close]")) button.addEventListener("click", () => { $(button.dataset.close).close(); if (button.dataset.close === "assistant-dialog") dismissAssistant(); });
$("assistant-dialog").addEventListener("cancel", dismissAssistant);
$("settings-open").addEventListener("click", () => { for (const element of $("settings-form").elements) { if (!element.name) continue; if (element.type === "checkbox") element.checked = Boolean(settings[element.name]); else element.value = element.name === "hotwords" ? (settings.hotwords || []).join(", ") : element.name === "fuzzy_pairs" ? (settings.fuzzy_pairs || []).map(pair => pair.join(":")).join(", ") : element.name === "app_profiles" ? JSON.stringify(settings.app_profiles || {}, null, 2) : settings[element.name] ?? ""; } $("settings-dialog").showModal(); });
$("settings-form").addEventListener("submit", async event => { event.preventDefault(); const patch = {}; for (const element of event.target.elements) if (element.name) patch[element.name] = element.type === "checkbox" ? element.checked : element.type === "number" ? Number(element.value) : element.value; if (typeof patch.hotwords === "string") patch.hotwords = patch.hotwords.split(/[,，]/).map(word => word.trim()).filter(Boolean); try { if (typeof patch.fuzzy_pairs === "string") patch.fuzzy_pairs = patch.fuzzy_pairs.split(",").map(pair => pair.trim().split(":")); if (typeof patch.app_profiles === "string") patch.app_profiles = JSON.parse(patch.app_profiles || "{}"); await saveSettings(patch); saveDraft(); $("settings-dialog").close(); scheduleCandidates(); status("设置已保存。"); await checkHealth(); } catch (error) { status(error.message, true); } });
function setVoiceState(state) {
  voiceState = state; const busy = state !== "idle";
  for (const id of ["language", "script", "audio-file", "warmup", "unload"]) $(id).disabled = busy || (id === "audio-file" && !speechAvailable);
  $("record").disabled = state === "transcribing" || !speechAvailable;
  $("record").classList.toggle("recording", state === "recording");
  $("record-label").textContent = { idle: settings.push_to_talk ? "按住说话" : "开始录音", requesting: "打开麦克风…", recording: settings.push_to_talk ? "松开结束" : "停止并上屏", transcribing: "正在收尾…" }[state];
  $("cancel-record").disabled = !busy; if (state !== "recording") $("record-time").textContent = "";
}
function releaseMicrophone() { clearInterval(recordingTimer); microphone?.getTracks().forEach(track => track.stop()); microphone = null; capture?.disconnect(); capture = null; if (audioContext) void audioContext.close(); audioContext = null; $("level").value = 0; }
function cancelVoice() { const session = liveSession; ++voiceToken; if (session) { session.stopped = true; session.controller.abort(); void post("/api/live/cancel", { id: session.id }).catch(() => {}); } liveSession = null; uploadController?.abort(); uploadController = null; releaseMicrophone(); setVoiceState("idle"); $("stable-transcript").textContent = ""; $("draft-transcript").textContent = ""; status("已取消，本次文字未插入。"); }
$("cancel-record").addEventListener("click", cancelVoice);
document.addEventListener("keydown", event => { if (document.querySelector("dialog[open]")) return; if (event.key === "Escape" && voiceState !== "idle") { event.preventDefault(); cancelVoice(); } });
function commitVoice(text, snapshot, target, revision) { if (!text?.trim()) { status("未识别到语音，请检查输入音量。"); return; } if (editor.value !== snapshot || editorRevision !== revision) { status("录音期间文本已修改，结果保留在预览中；可选中复制。", true); return; } insertText(text, target); status("识别文字已插入。"); }
async function transcribe(file) {
  if (file.size > 16 * 1024 * 1024) { status("请选择不超过 16 MiB / 2 分钟的录音。", true); return; }
  const token = ++voiceToken, snapshot = editor.value, target = { ...selection }, revision = editorRevision; uploadController = new AbortController();
  setVoiceState("transcribing"); status("正在本机识别；首次使用需要加载模型。");
  try { const form = new FormData(); form.append("file", file); form.append("language", $("language").value); form.append("script", $("script").value); const data = await readResponse(await fetch("/api/transcribe", { method: "POST", body: form, signal: uploadController.signal })); if (token !== voiceToken) return; $("stable-transcript").textContent = data.text || ""; $("draft-transcript").textContent = ""; commitVoice(data.text, snapshot, target, revision); }
  catch (error) { if (token === voiceToken) status(error.message, true); }
  finally { if (token === voiceToken) { setVoiceState("idle"); $("audio-file").value = ""; uploadController = null; } }
}
// Send only new PCM16 samples. Fractional positions use the global sample index,
// so devices whose actual AudioContext rate differs from 16k do not drift.
function pcmChunk(session) {
  const output = [], total = session.samples;
  while (session.nextSample < total - 1 || (session.stopped && session.nextSample < total)) {
    const at = Math.floor(session.nextSample), fraction = session.nextSample - at;
    const value = session.audio[at] * (1 - fraction) + (session.audio[Math.min(at + 1, total - 1)] || 0) * fraction;
    output.push(Math.round(Math.max(-1, Math.min(1, value)) * 32767)); session.nextSample += session.rate / 16000;
  }
  const bytes = new Uint8Array(output.length * 2), view = new DataView(bytes.buffer); output.forEach((value, i) => view.setInt16(i * 2, value, true));
  let binary = ""; for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(binary);
}
async function updateLive(session, final = false) {
  if (session.token !== voiceToken || session.busy || session.finished || liveSession !== session) return;
  session.busy = true;
  // Keep exactly the same request on a transport retry; the server deduplicates by sequence.
  if (!session.pending) session.pending = { id: session.id, sequence: session.sequence, pcm: pcmChunk(session), final };
  const request = session.pending;
  try {
    const data = await post("/api/live/chunk", request, session.controller.signal); if (session.token !== voiceToken) return;
    session.pending = null; session.sequence++; session.failures = 0;
    $("stable-transcript").textContent = data.stable_text || ""; $("draft-transcript").textContent = data.revisable_tail || "";
    if (request.final) { session.finished = true; commitVoice(data.text, session.document, session.target, session.revision); void post("/api/live/cancel", { id: session.id }).catch(() => {}); if (data.warning) status(`保留了已识别文字；结尾可能不完整：${data.warning}`, true); setVoiceState("idle"); liveSession = null; }
    else status(data.warning ? `预览暂不可用，将重试：${data.warning}` : "正在听。浅色尾句仍可能修订。", Boolean(data.warning));
  } catch (error) { if (session.token !== voiceToken) return; session.failures++; status(`识别连接中断：${error.message}`, true); if (session.failures >= 3) { void post("/api/live/cancel", { id: session.id }).catch(() => {}); releaseMicrophone(); session.stopped = true; setVoiceState("idle"); liveSession = null; status("识别未完成，已停止录音。预览文字仍可复制。", true); return; } }
  finally { session.busy = false; if (session.token === voiceToken && liveSession === session && session.stopped) setTimeout(() => { if (session.token === voiceToken && liveSession === session) void updateLive(session, true); }, session.pending ? 1000 : 0); }
}
function finishRecording() { const session = liveSession; if (!session || session.stopped) return; session.stopped = true; releaseMicrophone(); setVoiceState("transcribing"); status("正在完成最后一句…"); if (!session.busy) void updateLive(session, true); }
async function startRecording() {
  if (voiceState !== "idle") return;
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) { status("录音需要支持 AudioWorklet 的浏览器及 localhost / HTTPS。", true); return; }
  const token = ++voiceToken, target = { ...selection }, documentText = editor.value, revision = editorRevision; setVoiceState("requesting"); $("stable-transcript").textContent = ""; $("draft-transcript").textContent = "";
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true }); if (token !== voiceToken) { stream.getTracks().forEach(track => track.stop()); return; }
    microphone = stream; const context = new AudioContext({ sampleRate: 16000 }); audioContext = context; await context.audioWorklet.addModule("/capture.js"); if (token !== voiceToken) return; await context.resume();
    const created = await post("/api/live/start", { language: $("language").value, script: $("script").value }); if (token !== voiceToken) { void post("/api/live/cancel", { id: created.id }).catch(() => {}); return; }
    const session = { id: created.id, token, audio: new Float32Array(Math.ceil(context.sampleRate * 115) + 1), samples: 0, rate: context.sampleRate, nextSample: 0, sequence: 0, pending: null, failures: 0, busy: false, stopped: false, lastPreview: 0, controller: new AbortController(), target, document: documentText, revision, finished: false };
    liveSession = session; capture = new AudioWorkletNode(context, "capture");
    capture.port.onmessage = ({ data }) => { if (session.stopped || token !== voiceToken) return; const count = Math.min(data.length, Math.floor(session.rate * 115) - session.samples); session.audio.set(data.subarray(0, count), session.samples); session.samples += count; let energy = 0; for (const value of data) energy += value * value; $("level").value = Math.min(1, Math.sqrt(energy / data.length) * 4); if (session.samples >= Math.floor(session.rate * 115)) finishRecording(); };
    context.createMediaStreamSource(stream).connect(capture); capture.connect(context.destination); stream.getAudioTracks().forEach(track => track.addEventListener("ended", finishRecording));
    const started = Date.now(); setVoiceState("recording");
    recordingTimer = setInterval(() => { const seconds = Math.floor((Date.now() - started) / 1000); $("record-time").textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; if (seconds >= 115) { finishRecording(); return; } if (!session.busy && seconds - session.lastPreview >= 3 && session.samples > 0) { session.lastPreview = seconds; void updateLive(session); } }, 500);
    status("正在录音；Esc 取消，最长 115 秒。");
  } catch (error) { if (token !== voiceToken) return; if (liveSession) { liveSession.stopped = true; liveSession.controller.abort(); void post("/api/live/cancel", { id: liveSession.id }).catch(() => {}); liveSession = null; } releaseMicrophone(); setVoiceState("idle"); status(error.name === "NotAllowedError" ? "麦克风权限未获允许，请在浏览器设置中开启。" : error.message, true); }
}
$("record").addEventListener("click", () => { if (settings.push_to_talk) return; if (voiceState === "recording") finishRecording(); else if (voiceState === "requesting") cancelVoice(); else void startRecording(); });
$("record").addEventListener("pointerdown", event => { if (!settings.push_to_talk || voiceState !== "idle") return; event.preventDefault(); $("record").setPointerCapture(event.pointerId); void startRecording(); });
for (const name of ["pointerup", "pointercancel"]) $("record").addEventListener(name, () => { if (!settings.push_to_talk) return; if (voiceState === "requesting") cancelVoice(); else finishRecording(); });
$("record").addEventListener("keydown", event => { if (settings.push_to_talk && [" ", "Enter"].includes(event.key) && !event.repeat) { event.preventDefault(); void startRecording(); } });
$("record").addEventListener("keyup", event => { if (settings.push_to_talk && [" ", "Enter"].includes(event.key)) { event.preventDefault(); if (voiceState === "requesting") cancelVoice(); else finishRecording(); } });
$("record").addEventListener("blur", () => { if (!settings.push_to_talk) return; if (voiceState === "requesting") cancelVoice(); else if (voiceState === "recording") finishRecording(); });
$("audio-file").addEventListener("change", () => { const file = $("audio-file").files[0]; if (file && voiceState === "idle") void transcribe(file); });
async function checkHealth() { try { const data = await get("/api/health"); const speech = data.speech || {}; speechAvailable = Boolean(speech.available); $("speech-status").textContent = `${speech.model || "本地模型"} · ${speech.device || "CPU"} · ${speech.loaded ? "已加载" : speech.cached ? "已缓存，首次使用加载" : "请先准备本地模型"}${speech.detail ? ` · ${speech.detail}` : ""}`; } catch (error) { speechAvailable = false; $("speech-status").textContent = error.message; } setVoiceState(voiceState); }
for (const id of ["warmup", "unload"]) $(id).addEventListener("click", async () => { $(id).disabled = true; status(id === "warmup" ? "正在加载本地模型…" : "正在释放模型…"); try { await post(`/api/speech/${id}`, {}); status(id === "warmup" ? "本地模型已就绪。" : "模型已释放。"); } catch (error) { status(error.message, true); } finally { await checkHealth(); $(id).disabled = false; } });
$("copy").addEventListener("click", async () => { if (!editor.value) return; try { await navigator.clipboard.writeText(editor.value); dirty = false; status("文字已复制。"); } catch { editor.focus(); editor.select(); rememberSelection(); status("请按 Ctrl+C / ⌘C 复制所选文字。"); } });
function download(text, name, type = "text/plain;charset=utf-8") { const url = URL.createObjectURL(new Blob([text], { type })); const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
$("download").addEventListener("click", () => { download(editor.value, "shuangsheng.txt"); dirty = false; status("文本已保存。"); });
$("clear").addEventListener("click", () => { if (!editor.value) return; checkpoint(); editor.value = ""; selection = { start: 0, end: 0 }; committedContext = ""; changed(); status("已清空，可撤销。"); });
async function refreshTerms() {
  const data = await get("/api/lexicon"); $("terms").replaceChildren();
  for (const entry of data.entries) { const row = document.createElement("div"); row.className = "term"; const text = document.createElement("span"); text.textContent = entry.text; const detail = document.createElement("small"); detail.textContent = [entry.pinyin, entry.shortcut].filter(Boolean).join(" · "); text.append(detail); const pin = document.createElement("button"); pin.textContent = entry.pinned ? "取消置顶" : "置顶"; pin.addEventListener("click", () => void post("/api/lexicon", { ...entry, action: "upsert", pinned: !entry.pinned }).then(refreshTerms).catch(error => status(error.message, true))); const remove = document.createElement("button"); remove.textContent = "删除"; remove.addEventListener("click", () => void post("/api/lexicon", { action: "delete", id: entry.id }).then(refreshTerms).catch(error => status(error.message, true))); row.append(text, pin, remove); $("terms").append(row); }
}
$("lexicon-open").addEventListener("click", () => { $("lexicon-dialog").showModal(); void refreshTerms().catch(error => status(error.message, true)); });
$("term-form").addEventListener("submit", async event => { event.preventDefault(); const form = event.target.elements; try { await post("/api/lexicon", { text: form.text.value, pinyin: form.pinyin.value, shortcut: form.shortcut.value, pinned: form.pinned.checked }); event.target.reset(); await refreshTerms(); } catch (error) { status(error.message, true); } });
$("terms-export").addEventListener("click", async () => { try { const data = await get("/api/lexicon"); download(JSON.stringify(data, null, 2), "shuangsheng-terms.json", "application/json"); } catch (error) { status(error.message, true); } });
$("terms-import").addEventListener("change", async () => { const file = $("terms-import").files[0]; if (!file) return; let added = 0; try { if (file.size > 100000) throw new Error("词库文件过大，请分批导入。"); const data = JSON.parse(await file.text()); if (!Array.isArray(data.entries) || data.entries.length > 200) throw new Error("请选择最多 200 条的词库 JSON。"); for (const row of data.entries) if (!row || typeof row.text !== "string" || typeof (row.pinyin || "") !== "string" || typeof (row.shortcut || "") !== "string") throw new Error("词库格式无效。"); for (const row of data.entries) { await post("/api/lexicon", { text: row.text, pinyin: row.pinyin || "", shortcut: row.shortcut || "", pinned: Boolean(row.pinned) }); added++; } await refreshTerms(); status(`已导入 ${added} 条词条。`); } catch (error) { status(`已导入 ${added} 条；${error.message}`, true); } finally { $("terms-import").value = ""; } });
for (const button of document.querySelectorAll("[data-assist]")) button.addEventListener("click", () => { assistantAction = button.dataset.assist; assistantSelection = selection.start !== selection.end ? { ...selection } : { start: 0, end: editor.value.length }; assistantDocument = editor.value; $("assistant-original").value = editor.value.slice(assistantSelection.start, assistantSelection.end); $("assistant-result").value = ""; $("assistant-status").textContent = settings.llm_enabled ? "点击生成建议，文字只发送给配置的本机模型。" : "请先在设置中启用本地智能助手。"; $("assistant-accept").disabled = true; $("assistant-dialog").showModal(); });
$("assistant-run").addEventListener("click", async () => { const token = ++assistantToken; assistantController?.abort(); assistantController = new AbortController(); $("assistant-run").disabled = true; $("assistant-accept").disabled = true; $("assistant-status").textContent = "本地模型正在生成…"; try { const data = await post("/api/assist", { text: $("assistant-original").value, action: assistantAction, language: $("translate-language").value }, assistantController.signal); if (token !== assistantToken) return; $("assistant-result").value = data.text; $("assistant-accept").disabled = false; $("assistant-status").textContent = "请检查人名、数字及原意，再决定采用。"; } catch (error) { if (token === assistantToken) $("assistant-status").textContent = error.message; } finally { if (token === assistantToken) { $("assistant-run").disabled = false; assistantController = null; } } });
$("assistant-accept").addEventListener("click", () => { if (editor.value !== assistantDocument) { $("assistant-status").textContent = "原文已改变，请重新选择文字生成建议。"; return; } const target = assistantAction === "complete" ? { start: assistantSelection.end, end: assistantSelection.end } : assistantSelection; insertText($("assistant-result").value, target); $("assistant-dialog").close(); status("已采用建议，可撤销。"); });
window.addEventListener("beforeunload", event => { if (dirty && editor.value && !settings.save_draft) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("pagehide", () => { ++voiceToken; liveSession?.controller.abort(); if (liveSession) void fetch("/api/live/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: liveSession.id }), keepalive: true }); releaseMicrophone(); });
async function initialize() { drawCandidates(); try { settings = await get("/api/settings"); applySettings(); if (settings.save_draft) { editor.value = localStorage.getItem("shuangsheng-draft") || ""; updateCount(); } else localStorage.removeItem("shuangsheng-draft"); } catch (error) { status(`设置未加载：${error.message}`, true); } await checkHealth(); window.__shuangshengReady = true; }
void initialize();
