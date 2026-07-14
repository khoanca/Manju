// Client Manju — tách từ index.html. Gồm: router màn hình, lịch sử,
// upload, live (mic → WS, có buffer + tự nối lại), settings (localStorage +
// server), wake lock, PWA, OPFS cho client mỏng (PRD FR-4).
const $ = (id) => document.getElementById(id);
const ICN = {
  cal:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 9h18M8 3v4M16 3v4"/></svg>',
  doc:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6"/></svg>',
  clk:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
};

// ── Client mỏng: truy cập từ máy khác (không phải máy chạy app) ────────────
const IS_REMOTE = !["localhost", "127.0.0.1", "::1"].includes(location.hostname);
const HAS_OPFS = !!(navigator.storage && navigator.storage.getDirectory && window.Worker);

// ── Settings (localStorage — giữ qua reload) ───────────────────────────────
const LS_KEY = "manju.settings";
let storeAudioLocal = false;
function loadSettings(){
  let s = {};
  try { s = JSON.parse(localStorage.getItem(LS_KEY)) || {}; } catch {}
  if (s.lang) $("lang").value = s.lang;
  if (s.model) $("model").value = s.model;
  if (s.prompt != null) $("prompt").value = s.prompt;
  if (s.correct === false){ $("correct").checked = false; $("swCorrect").classList.remove("on"); }
  // Client mỏng + có OPFS → mặc định giữ audio trên thiết bị (không lên server).
  storeAudioLocal = s.storeAudioLocal != null ? !!s.storeAudioLocal : (IS_REMOTE && HAS_OPFS);
  if (!HAS_OPFS) storeAudioLocal = false;
  $("swLocalAudio").classList.toggle("on", storeAudioLocal);
  if (!HAS_OPFS) $("localAudioHint").textContent = "Trình duyệt này không hỗ trợ (OPFS) — audio sẽ lưu trên server.";
}
function saveSettings(){
  localStorage.setItem(LS_KEY, JSON.stringify({
    lang: $("lang").value, model: $("model").value, prompt: $("prompt").value,
    correct: $("correct").checked, storeAudioLocal,
  }));
}
["lang", "model", "prompt"].forEach(id => $(id).addEventListener("change", saveSettings));
$("swLocalAudio").onclick = () => {
  if (!HAS_OPFS) return;
  storeAudioLocal = $("swLocalAudio").classList.toggle("on");
  saveSettings();
};

// ── Settings phía server (engine, thư mục audio) ───────────────────────────
async function loadServerSettings(){
  try {
    const s = await (await fetch("/api/settings")).json();
    $("engineVal").textContent = s.engine.model;
    $("audioDir").value = s.audio_dir;
  } catch { $("engineVal").textContent = "—"; }
}
$("saveAudioDir").onclick = async () => {
  const msg = $("audioDirMsg");
  try {
    const r = await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audio_dir: $("audioDir").value.trim() }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    $("audioDir").value = d.audio_dir;
    msg.textContent = "Đã lưu ✓"; msg.style.color = "var(--ok)";
  } catch (e) { msg.textContent = e.message; msg.style.color = "#dc2626"; }
  setTimeout(() => { msg.textContent = ""; }, 5000);
};

// ── Screen router ──────────────────────────────────────────────────────────
const SCREENS = ["home","record","settings","upload","detail"];
function showScreen(name){
  SCREENS.forEach(s => $("screen-" + s).classList.toggle("active", s === name));
  const tabbed = (name === "home" || name === "settings");
  $("tabbar").classList.toggle("hidden", !tabbed);
  // Đồng bộ trạng thái active cho cả tab bar (mobile) lẫn sidebar (laptop).
  document.querySelectorAll("[data-tab]").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  window.scrollTo(0, 0);
}
document.querySelectorAll("[data-tab]").forEach(t => t.onclick = () => {
  if (t.dataset.tab === "record") return enterRecord();
  if (live) return;                     // đang ghi thì không rời màn hình
  showScreen(t.dataset.tab);
});
document.querySelectorAll("[data-back]").forEach(b => b.onclick = () => showScreen("home"));
$("addBtn").onclick = () => showScreen("upload");
$("sideUpload").onclick = () => { if (!live) showScreen("upload"); };
$("searchBtn").onclick = () => { const b = $("searchBox"); b.classList.toggle("hidden"); if (!b.classList.contains("hidden")) $("searchInput").focus(); else { $("searchInput").value=""; renderHistory(); } };
$("searchInput").oninput = renderHistory;

// ── Lọc & sắp xếp bản ghi ──────────────────────────────────────────────────
const FILTER_IDS = ["sortBy", "fDateFrom", "fDateTo", "fTimeFrom", "fTimeTo", "fDurMin", "fDurMax"];
$("filterBtn").onclick = () => {
  const p = $("filters");
  const open = p.classList.toggle("hidden") === false;
  $("filterBtn").classList.toggle("on", open || filtersActive());
};
FILTER_IDS.forEach(id => $(id).addEventListener("input", renderHistory));
$("fClear").onclick = () => {
  ["fDateFrom", "fDateTo", "fTimeFrom", "fTimeTo", "fDurMin", "fDurMax"].forEach(id => $(id).value = "");
  $("sortBy").value = "date-desc";
  renderHistory();
};
// Có bất kỳ điều kiện lọc nào đang bật (không tính sắp xếp) → highlight nút.
function filtersActive(){
  return ["fDateFrom", "fDateTo", "fTimeFrom", "fTimeTo", "fDurMin", "fDurMax"]
    .some(id => $(id).value) || !!($("searchInput").value || "").trim();
}

// ── Settings toggle (checkbox mirror) ──────────────────────────────────────
$("swCorrect").onclick = () => {
  const on = $("swCorrect").classList.toggle("on");
  $("correct").checked = on;
  saveSettings();
};

// ── Formatting helpers ─────────────────────────────────────────────────────
const pad = (n) => String(n).padStart(2, "0");
function fmtClock(sec){ sec = Math.max(0, Math.floor(sec||0)); return pad(Math.floor(sec/3600)) + ":" + pad(Math.floor(sec/60)%60) + ":" + pad(sec%60); }
function fmtDur(sec){ sec = Math.max(0, Math.round(sec||0)); const m = Math.floor(sec/60), s = sec%60; return (m>=60 ? Math.floor(m/60)+":"+pad(m%60) : m) + ":" + pad(s); }
function fmtDate(iso){ try{ return new Date(iso).toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"}); }catch{ return ""; } }
const fmtTime = (s) => { s = Math.max(0, Math.round(s||0)); return Math.floor(s/60) + ":" + pad(s%60); };
const nfmt = (n) => (n||0).toLocaleString("en-US");

// ── History ────────────────────────────────────────────────────────────────
let ALL = [];
async function loadHistory(){ ALL = await (await fetch("/api/transcripts")).json(); renderHistory(); }

const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const SORTERS = {
  "date-desc": (a, b) => cmpStr(b.created_at, a.created_at),   // created_at là ISO → so chuỗi = so thời gian
  "date-asc":  (a, b) => cmpStr(a.created_at, b.created_at),
  "title-asc": (a, b) => (a.title || "").localeCompare(b.title || "", "vi"),
  "title-desc":(a, b) => (b.title || "").localeCompare(a.title || "", "vi"),
  "dur-desc":  (a, b) => (b.duration || 0) - (a.duration || 0),
  "dur-asc":   (a, b) => (a.duration || 0) - (b.duration || 0),
};
const localYMD = (d) => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
const localHM  = (d) => pad(d.getHours()) + ":" + pad(d.getMinutes());

// Lọc theo tên + khoảng ngày + khoảng giờ trong ngày + khoảng thời lượng, rồi sắp xếp.
function filterSortHistory(){
  const q = ($("searchInput").value || "").trim().toLowerCase();
  const dFrom = $("fDateFrom").value, dTo = $("fDateTo").value;   // "YYYY-MM-DD" | ""
  const tFrom = $("fTimeFrom").value, tTo = $("fTimeTo").value;   // "HH:MM" | ""
  const durMin = parseFloat($("fDurMin").value), durMax = parseFloat($("fDurMax").value);
  const out = ALL.filter(m => {
    if (q && !(m.title || "").toLowerCase().includes(q)) return false;
    const dt = new Date(m.created_at);
    if (!isNaN(dt)){
      const ymd = localYMD(dt), hm = localHM(dt);
      if (dFrom && ymd < dFrom) return false;
      if (dTo   && ymd > dTo)   return false;
      if (tFrom && hm  < tFrom) return false;
      if (tTo   && hm  > tTo)   return false;
    }
    const mins = (m.duration || 0) / 60;
    if (!isNaN(durMin) && mins < durMin) return false;
    if (!isNaN(durMax) && mins > durMax) return false;
    return true;
  });
  return out.sort(SORTERS[$("sortBy").value] || SORTERS["date-desc"]);
}

function renderHistory(){
  const box = $("history");
  const items = filterSortHistory();
  const active = filtersActive();
  $("filterBtn").classList.toggle("on", active || !$("filters").classList.contains("hidden"));
  box.innerHTML = "";
  if (!items.length){
    box.innerHTML = `<div class="empty"><span class="big">🎙️</span><b>${active ? "Không tìm thấy" : "Chưa có bản ghi nào"}</b>${active ? "" : "Bấm nút micro để ghi, hoặc ＋ để tải file lên."}</div>`;
    return;
  }
  const opfs = opfsMap();
  for (const m of items){
    const li = document.createElement("li");
    li.className = "rec-card";
    const words = m.words != null ? m.words : Math.round((m.chars||0)/5);
    const chips = (m.audio ? `<span class="chip audio">🔊 Audio</span>` : (opfs[m.id] ? `<span class="chip audio">📱 Audio</span>` : ""))
                + (m.corrected ? `<span class="chip ai">✦ AI</span>` : "");
    li.innerHTML = `
      <div class="rec-top">
        <div class="avatar"><i style="height:9px"></i><i style="height:16px"></i><i style="height:22px"></i><i style="height:14px"></i><i style="height:8px"></i></div>
        <div class="rec-main">
          <b></b>
          <div class="rec-sub">
            <span>${ICN.cal} ${fmtDate(m.created_at)}</span>
            <span>${ICN.doc} ${nfmt(words)} words</span>
          </div>
        </div>
      </div>
      <div class="rec-div"></div>
      <div class="rec-foot">
        <span class="dur">${ICN.clk} ${fmtDur(m.duration)}</span>
        <div class="chips">${chips}</div>
      </div>`;
    li.querySelector("b").textContent = m.title;
    li.onclick = () => openDetail(m);
    box.appendChild(li);
  }
}

// ── OPFS: audio của client mỏng nằm trên thiết bị ──────────────────────────
const OPFS_KEY = "manju.opfsAudio";
function opfsMap(){ try{ return JSON.parse(localStorage.getItem(OPFS_KEY)) || {}; }catch{ return {}; } }
function rememberOpfs(id, file){ const m = opfsMap(); m[id] = file; localStorage.setItem(OPFS_KEY, JSON.stringify(m)); }

function wavHeader(dataLen, rate){
  const h = new DataView(new ArrayBuffer(44));
  const s = (o, t) => { for (let i = 0; i < t.length; i++) h.setUint8(o + i, t.charCodeAt(i)); };
  s(0,"RIFF"); h.setUint32(4, 36 + dataLen, true); s(8,"WAVE"); s(12,"fmt ");
  h.setUint32(16,16,true); h.setUint16(20,1,true); h.setUint16(22,1,true);
  h.setUint32(24,rate,true); h.setUint32(28,rate*2,true); h.setUint16(32,2,true); h.setUint16(34,16,true);
  s(36,"data"); h.setUint32(40,dataLen,true);
  return h.buffer;
}
async function opfsWavBlob(name){
  const root = await navigator.storage.getDirectory();
  const dir = await root.getDirectoryHandle("recordings");
  const file = await (await dir.getFileHandle(name)).getFile();
  const pcm = await file.arrayBuffer();
  return new Blob([wavHeader(pcm.byteLength, 16000), pcm], { type: "audio/wav" });
}
function startOpfsWriter(){
  const name = "live-" + Date.now() + ".pcm";
  const worker = new Worker("/static/opfs-writer.js");
  worker.postMessage({ cmd: "start", name });
  return {
    name,
    write(buf){ const copy = buf.slice(0); worker.postMessage({ cmd: "write", data: copy }, [copy]); },
    finish(){
      return new Promise(res => {
        worker.onmessage = (e) => { if (e.data.cmd === "done"){ worker.terminate(); res(e.data.size > 0 ? name : null); } };
        worker.postMessage({ cmd: "finish" });
        setTimeout(() => { try{ worker.terminate(); }catch{} res(null); }, 3000); // worker kẹt → bỏ qua
      });
    },
  };
}

// ── Detail / result ────────────────────────────────────────────────────────
let fixedText = "", rawText = null, showingRaw = false, downloadName = "transcript.txt";
let lastBlobUrl = null;
async function showAudio(id, hasServerAudio){
  const wrap = $("audioWrap"), player = $("player"), dl = $("dlAudio");
  if (lastBlobUrl){ URL.revokeObjectURL(lastBlobUrl); lastBlobUrl = null; }
  player.removeAttribute("src"); player.load(); wrap.classList.add("hidden");
  if (hasServerAudio && id){
    const url = "/api/transcripts/" + id + "/audio";
    player.src = url; dl.href = url; dl.removeAttribute("download");
    wrap.classList.remove("hidden");
    return;
  }
  // Không có audio trên server → thử bản ghi OPFS trên thiết bị này.
  const name = id && opfsMap()[id];
  if (name && HAS_OPFS){
    try {
      const blob = await opfsWavBlob(name);
      lastBlobUrl = URL.createObjectURL(blob);
      player.src = lastBlobUrl; dl.href = lastBlobUrl; dl.download = id + ".wav";
      wrap.classList.remove("hidden");
    } catch {} // file đã bị trình duyệt dọn (iOS xoá storage sau ~7 ngày không dùng)
  }
}
function setResult(text, raw, segments, id, hasServerAudio){
  fixedText = text; rawText = raw || null; showingRaw = false;
  $("result").value = text;
  $("toggleRaw").style.display = rawText ? "" : "none";
  $("toggleRaw").textContent = "Bản gốc";
  renderSegments(segments);
  showAudio(id, hasServerAudio);
}
async function openDetail(m){
  showScreen("detail");
  $("detTitle").textContent = m.title || "Bản ghi";
  downloadName = (m.title || m.id).replace(/\.[^.]+$/, "") + ".txt";
  setResult("Đang tải…", null, null, null, false);
  const d = await (await fetch("/api/transcripts/" + m.id)).json();
  setResult(d.text, d.raw_text, d.segments, d.id, !!d.audio);
}
$("toggleRaw").onclick = () => { showingRaw = !showingRaw; $("result").value = showingRaw ? rawText : fixedText; $("toggleRaw").textContent = showingRaw ? "Bản sửa" : "Bản gốc"; };
function renderSegments(segments){
  const box = $("segList"), btn = $("toggleSegs"); box.innerHTML = "";
  if (!segments || !segments.length){ box.style.display = "none"; btn.style.display = "none"; return; }
  for (const s of segments){
    const p = document.createElement("p"); p.className = "seg";
    const t = document.createElement("time"); t.textContent = fmtTime(s.start);
    const span = document.createElement("span"); span.textContent = s.text;
    p.append(t, span); box.appendChild(p);
  }
  box.style.display = "none"; btn.style.display = ""; btn.textContent = "Mốc thời gian";
}
$("toggleSegs").onclick = () => { const h = $("segList").style.display === "none"; $("segList").style.display = h ? "" : "none"; $("toggleSegs").textContent = h ? "Ẩn mốc" : "Mốc thời gian"; };
$("copy").onclick = () => { navigator.clipboard.writeText($("result").value); $("copy").textContent = "Đã copy!"; setTimeout(()=>$("copy").textContent="Copy",1200); };
$("download").onclick = () => { const b = new Blob([$("result").value],{type:"text/plain"}); const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = downloadName; a.click(); };

// ── Upload → transcribe ────────────────────────────────────────────────────
const fileInput = $("file"), drop = $("drop"), go = $("go");
let chosen = null;
drop.onclick = () => fileInput.click();
fileInput.onchange = () => setFile(fileInput.files[0]);
["dragover","dragenter"].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.style.borderColor = "var(--accent)"; }));
["dragleave","drop"].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.style.borderColor = ""; }));
drop.addEventListener("drop", ev => setFile(ev.dataTransfer.files[0]));
function setFile(f){ if (!f) return; chosen = f; $("fileName").textContent = "📎 " + f.name; go.disabled = false; }
go.onclick = async () => {
  if (!chosen) return;
  go.disabled = true;
  setStatus("Đang tải file lên…", 0);
  const fd = new FormData();
  fd.append("file", chosen); fd.append("language", $("lang").value); fd.append("model", $("model").value);
  fd.append("prompt", $("prompt").value); fd.append("correct", $("correct").checked);
  downloadName = chosen.name.replace(/\.[^.]+$/, "") + ".txt";
  try {
    const r = await fetch("/api/transcribe", { method:"POST", body:fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    poll((await r.json()).job_id);
  } catch (e) { setStatus("Lỗi: " + e.message, 0); go.disabled = false; }
};
function poll(jobId){
  setStatus("Đang chuyển giọng nói thành text…", 0);
  const tick = async () => {
    const j = await (await fetch("/api/jobs/" + jobId)).json();
    setStatus(label(j.status), j.progress);
    if (j.status === "done"){
      go.disabled = false; chosen = null; $("fileName").textContent = ""; setStatus("Xong ✅", 1);
      await loadHistory();
      if (j.transcript_id){ const m = ALL.find(x => x.id === j.transcript_id) || {id:j.transcript_id, title:downloadName}; openDetail(m); }
      return;
    }
    if (j.status === "error"){ setStatus("Lỗi: " + j.error, 0); go.disabled = false; return; }
    setTimeout(tick, 1500);
  };
  tick();
}
const label = (s) => ({queued:"Đang xếp hàng…", running:"Đang transcribe…", correcting:"Đang sửa thuật ngữ (AI)…", done:"Xong ✅"}[s] || s);
function setStatus(t, p){ $("status").textContent = t; $("bar").style.width = Math.round((p||0)*100) + "%"; }

// ── Waveform bars ──────────────────────────────────────────────────────────
const wave = $("wave");
(function buildWave(){
  const H = [10,20,34,26,44,30,54,40,64,48,70,54,58,44,66,50,60,40,50,30,42,24,34,18,26,14,20,10];
  wave.innerHTML = "";
  H.concat([...H].reverse()).forEach((h,i) => {
    const b = document.createElement("i");
    b.style.height = h + "px";
    b.style.animationDelay = (i * 0.045) + "s";
    b.style.animationDuration = (0.9 + (i % 5) * 0.12) + "s";
    wave.appendChild(b);
  });
})();

// ── Wake lock: giữ màn hình sáng khi đang ghi (PRD FR-4) ───────────────────
let wakeLock = null;
async function acquireWakeLock(){
  try { wakeLock = await navigator.wakeLock?.request("screen"); } catch {}
}
function releaseWakeLock(){ try{ wakeLock?.release(); }catch{} wakeLock = null; }
document.addEventListener("visibilitychange", () => {
  if (live && document.visibilityState === "visible") acquireWakeLock();
});

// ── Kết nối live bền: buffer PCM 60s + tự nối lại (resume) ─────────────────
const PCM_BPS = 32000;                 // 16kHz × 2 byte
const BUF_MAX_BYTES = 60 * PCM_BPS;
const RETRY_DELAYS = [1000, 2000, 5000, 5000, 5000];

class LiveConn {
  constructor(startMsg, onMsg, onState){
    this.startMsg = startMsg; this.onMsg = onMsg; this.onState = onState;
    this.token = null; this.sent = 0;
    this.chunks = []; this.buffered = 0;   // [{off, data}] — chưa được server ack
    this.closedByUser = false; this.attempt = 0;
    this._open(false);
  }
  _open(isResume){
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = this.ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      this.attempt = 0;
      ws.send(JSON.stringify(isResume ? { type: "resume", token: this.token } : this.startMsg));
    };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "session"){ this.token = m.token; return; }
      if (m.type === "ack"){ this._prune(m.bytes); return; }
      if (m.type === "resumed"){ this._prune(m.bytes); this._replay(m.bytes); this.onState("resumed"); return; }
      this.onMsg(m);
    };
    ws.onclose = () => {
      if (this.closedByUser) return;
      if (!this.token){ this.onState("dead"); return; }   // rớt trước khi mở được phiên
      this._retry();
    };
  }
  _retry(){
    if (this.attempt >= RETRY_DELAYS.length){ this.onState("dead"); return; }
    this.onState("reconnecting");
    setTimeout(() => { if (!this.closedByUser) this._open(true); }, RETRY_DELAYS[this.attempt++]);
  }
  send(data){
    this.chunks.push({ off: this.sent, data });
    this.sent += data.byteLength; this.buffered += data.byteLength;
    while (this.buffered > BUF_MAX_BYTES){ this.buffered -= this.chunks.shift().data.byteLength; }
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(data);
  }
  _prune(acked){
    while (this.chunks.length && this.chunks[0].off + this.chunks[0].data.byteLength <= acked){
      this.buffered -= this.chunks[0].data.byteLength; this.chunks.shift();
    }
  }
  _replay(from){
    for (const c of this.chunks) if (c.off + c.data.byteLength > from) this.ws.send(c.data);
  }
  sendJSON(obj){
    if (this.ws.readyState === WebSocket.OPEN){ this.ws.send(JSON.stringify(obj)); return true; }
    return false;
  }
  close(){ this.closedByUser = true; try{ this.ws.close(); }catch{} }
}

// ── Feature 1: Ghi âm trực tiếp (mic → WS → subtitle + lưu audio) ───────────
const subs = $("subtitles"), recTimer = $("recTimer"), recPill = $("recPill"), recHint = $("recHint");
let live = null;   // {conn, ctx, stream, opfs, stopping, deadline, paused, base, resumeAt, timer}
let tw = null;

function enterRecord(){ showScreen("record"); if (!live) startLive(); }
function setPill(text, on){ recPill.innerHTML = `<span class="dot"></span> ${text}`; recPill.classList.toggle("live", !!on); }
function setWave(state){ wave.classList.toggle("on", state === "on"); wave.classList.toggle("paused", state === "paused"); }

function tickTimer(){ if (!live) return; const el = live.base + (live.paused ? 0 : (Date.now() - live.resumeAt)/1000); recTimer.textContent = fmtClock(el); }

async function startLive(){
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio:{ echoCancellation:true, noiseSuppression:true, autoGainControl:true } });
  } catch (e){
    setPill("MIC BỊ CHẶN", false); setWave("off");
    recHint.textContent = e.name === "NotAllowedError" ? "Bạn chưa cho phép dùng micro (kiểm tra quyền trình duyệt)." : e.message;
    return;
  }
  const ctx = new AudioContext();
  await ctx.audioWorklet.addModule("/static/pcm-worklet.js");
  const node = new AudioWorkletNode(ctx, "pcm-worklet");
  ctx.createMediaStreamSource(stream).connect(node);

  const useOpfs = storeAudioLocal && HAS_OPFS;
  const conn = new LiveConn(
    { type:"start", language:$("lang").value, glossary:$("prompt").value,
      correct:$("correct").checked, store_audio: !useOpfs },
    handleLiveMsg,
    (state) => {
      if (!live) return;
      if (state === "reconnecting"){ setPill("MẤT MẠNG — ĐANG NỐI LẠI…", false); return; }
      if (state === "resumed"){ setPill("RECORDING", true); return; }
      if (state === "dead"){
        if (live.stopping){ endLive(); showScreen("home"); return; }
        cleanupLive();
      }
    }
  );
  live = { conn, ctx, stream, opfs: useOpfs ? startOpfsWriter() : null,
           stopping:false, deadline:null, paused:false, base:0, resumeAt:Date.now(),
           timer:setInterval(tickTimer,300) };
  subs.innerHTML = ""; recTimer.textContent = "00:00:00";
  setPill("KẾT NỐI…", true); setWave("on");
  $("recPause").textContent = "❚❚ Pause";
  recHint.textContent = useOpfs
    ? "Audio lưu trên thiết bị này (không gửi lên server) — nhớ tải về nếu cần giữ lâu."
    : "Bấm Stop để kết thúc — văn bản & file ghi âm tự lưu.";
  acquireWakeLock();

  node.port.onmessage = (ev) => {
    if (!live || live.paused) return;
    live.conn.send(ev.data);
    if (live.opfs) live.opfs.write(ev.data);
  };
}

function handleLiveMsg(m){
  if (m.type === "ready"){ setPill("RECORDING", true); return; }
  if (m.type === "error"){ setPill("LỖI", false); recHint.textContent = m.message; return; }
  if (m.type === "partial"){
    const el = subEl(m.utt); el.classList.add("partial");
    if (!tw || tw.utt !== m.utt) startTypewriter(m.utt, el);
    retarget(m.text);
  } else if (m.type === "final"){
    stopTypewriter(m.utt); const el = subEl(m.utt);
    if (!m.text){ el.remove(); return; }
    el.textContent = m.text; el.classList.remove("partial"); autoscroll();
  } else if (m.type === "corrected"){
    if (!m.changed) return;
    const el = subs.querySelector(`[data-utt="${m.utt}"]`);
    if (el){ el.textContent = m.text; el.classList.remove("fixed"); void el.offsetWidth; el.classList.add("fixed"); }
  } else if (m.type === "saved"){
    const id = m.transcript_id;
    const opfsDone = live && live.opfs ? live.opfs.finish() : Promise.resolve(null);
    endLive();
    opfsDone
      .then(file => { if (file && id) rememberOpfs(id, file); })
      .then(loadHistory)
      .then(() => {
        showScreen("home");
        if (id){ const it = ALL.find(x => x.id === id); if (it) openDetail(it); }
      });
  }
}
function subEl(utt){ let el = subs.querySelector(`[data-utt="${utt}"]`); if (!el){ el = document.createElement("p"); el.className = "line"; el.dataset.utt = utt; subs.appendChild(el); } return el; }
function startTypewriter(utt, el){ if (tw) stopTypewriter(tw.utt); tw = { utt, el, displayed:"", target:"", timer:setInterval(revealWord, 70) }; }
function retarget(text){ if (!tw) return; let p = 0; while (p < tw.displayed.length && p < text.length && tw.displayed[p] === text[p]) p++; if (p < tw.displayed.length){ tw.displayed = tw.displayed.slice(0, p); tw.el.textContent = tw.displayed; } tw.target = text; }
function revealWord(){ if (!tw || tw.displayed.length >= tw.target.length) return; const rest = tw.target.slice(tw.displayed.length); const m = rest.match(/^\s*\S+/); tw.displayed += m ? m[0] : rest; tw.el.textContent = tw.displayed; autoscroll(); }
function stopTypewriter(utt){ if (tw && tw.utt === utt){ clearInterval(tw.timer); tw = null; } }
function autoscroll(){ if (subs.scrollHeight - subs.scrollTop - subs.clientHeight < 80) subs.scrollTop = subs.scrollHeight; }

// pause / resume
$("recPause").onclick = () => {
  if (!live || live.stopping) return;
  if (!live.paused){ live.base += (Date.now() - live.resumeAt)/1000; live.paused = true; setPill("TẠM DỪNG", false); setWave("paused"); $("recPause").textContent = "▶ Tiếp tục"; }
  else { live.resumeAt = Date.now(); live.paused = false; setPill("RECORDING", true); setWave("on"); $("recPause").textContent = "❚❚ Pause"; }
};

// stop & close both finish + save
$("recStop").onclick = stopLive;
$("recClose").onclick = () => { if (live) stopLive(); else showScreen("home"); };
function stopLive(){
  if (!live || live.stopping) return;
  live.stopping = true;
  $("recStop").disabled = true;
  setPill("ĐANG LƯU…", false); setWave("off"); recHint.textContent = "Đang chốt câu cuối & lưu…";
  stopAudio();
  if (live.conn.sendJSON({ type:"stop" })){
    live.deadline = setTimeout(() => { endLive(); showScreen("home"); }, 30000);
  } else {
    // Đang mất kết nối: server giữ phiên và sẽ tự chốt & lưu sau ~60s.
    if (live.opfs) live.opfs.finish();
    endLive(); showScreen("home");
  }
}
function stopAudio(){ if (!live) return; try{ live.stream.getTracks().forEach(t => t.stop()); }catch{} try{ live.ctx.close(); }catch{} }
// Dọn mọi timer/state của phiên ghi và giải phóng `live`.
function endLive(){
  if (live){ clearInterval(live.timer); clearTimeout(live.deadline); try{ live.conn.close(); }catch{} }
  if (tw){ clearInterval(tw.timer); tw = null; }
  releaseWakeLock();
  $("recStop").disabled = false;
  live = null;
}
function cleanupLive(){ // hết đường nối lại (server không giữ phiên / rớt quá lâu)
  if (!live) return;
  const opfs = live.opfs;
  stopAudio(); endLive();
  if (opfs) opfs.finish();
  setPill("MẤT KẾT NỐI", false); setWave("off");
  recHint.textContent = "Mất kết nối với server — phần đã ghi sẽ tự lưu trên server trong ~1 phút.";
}
window.addEventListener("beforeunload", () => { if (live){ try{ live.conn.close(); }catch{} } });

// ── Init ───────────────────────────────────────────────────────────────────
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
loadSettings();
loadServerSettings();
loadHistory();
// Deep-link tiện dụng (và để xem trước từng màn): #settings / #upload / #record.
if (["#settings","#upload","#record"].includes(location.hash)) showScreen(location.hash.slice(1));
