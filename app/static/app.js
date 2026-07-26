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
// Glossary: server là nguồn chân lý, localStorage chỉ là cache. User sửa xong
// (change) → PUT lên server; lỗi mạng thì giữ cache, glossary vẫn gửi kèm
// từng request nên không cần báo lỗi ồn ào.
$("prompt").addEventListener("change", () => putGlossary($("prompt").value));
async function putGlossary(text){
  try {
    const r = await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ glossary: text }),
    });
    if (!r.ok) throw new Error(r.statusText);
  } catch (e) { console.warn("Chưa sync glossary lên server:", e.message); }
}
$("swLocalAudio").onclick = () => {
  if (!HAS_OPFS) return;
  storeAudioLocal = $("swLocalAudio").classList.toggle("on");
  saveSettings();
};

// ── Settings phía server (engine, thư mục audio) ───────────────────────────
let diarizeReady = false;  // model đã tải chưa → cho phép bật pass 3
async function loadServerSettings(){
  try {
    const s = await (await fetch("/api/settings")).json();
    $("engineVal").textContent = s.engine.model;
    $("audioDir").value = s.audio_dir;
    // Glossary: server có → dùng làm chân lý (trừ khi user đang gõ dở);
    // server rỗng mà local có → đẩy lên MỘT lần (migration từ bản chỉ-localStorage).
    const remoteGlossary = s.glossary || "";
    if (remoteGlossary){
      if (document.activeElement !== $("prompt") && $("prompt").value !== remoteGlossary){
        $("prompt").value = remoteGlossary;
        saveSettings();
      }
    } else if ($("prompt").value.trim()){
      console.info("Glossary: server chưa có — đồng bộ bản local lên server.");
      putGlossary($("prompt").value);
    }
    const d = s.diarize || {};
    diarizeReady = !!d.models_present;
    $("swDiarize").classList.toggle("on", !!d.enabled);
    $("diarizeHint").textContent = diarizeReady
      ? 'Pass 3 — tách "ai nói câu nào" trong bản ghi mới'
      : "Chưa tải model — chạy: uv run python scripts/fetch_diarize_models.py";
    // Toggle "0"/"1" phía server: đánh dấu từ nghe không rõ + khử ồn mic
    for (const [id, key] of Object.entries(SRV_SW)) $(id).classList.toggle("on", s[key] === "1");
    // T-005: nạp cấu hình khử ồn mở rộng từ object con s.denoise
    const dn = s.denoise || {};
    $("dnStrength").value = dn.strength != null ? dn.strength : 100;
    $("dnStrengthVal").textContent = $("dnStrength").value + "%";
    $("dnMode").value = dn.mode || "stationary";
    $("swDnRumble").classList.toggle("on", (dn.highpass || 0) > 0);
    $("dnHum").value = dn.hum || "off";
    $("swDnUpload").classList.toggle("on", !!dn.upload_enabled);
    syncDenoiseAdv();
    // US-804: trạng thái toggle seed lexicon + URL nguồn cập nhật
    const lex = s.lexicon || {};
    for (const [id, key] of Object.entries(LEX_IDS)) $(id).checked = !!lex[key];
    if (document.activeElement !== $("lexUrl")) $("lexUrl").value = lex.url || "";
    if (document.activeElement !== $("slangSources")) $("slangSources").value = s.slang_sources || "";
    // US-818: ô hashtag chỉ hiện khi server có APIFY_TOKEN
    $("slangHashtagsRow").classList.toggle("hidden", !s.apify_enabled);
    if (document.activeElement !== $("slangHashtags")) $("slangHashtags").value = s.slang_hashtags || "";
    // Lớp B/C: toggle tự đoán ngành + danh sách ngành để research
    $("domainAuto").checked = s.domain_auto !== false;
    populateDomainPick(s.domains || []);
    syncLexBtn();
  } catch { $("engineVal").textContent = "—"; }
}
$("swDiarize").onclick = async () => {
  if (!diarizeReady && !$("swDiarize").classList.contains("on")){
    alert("Chưa tải model. Chạy: uv run python scripts/fetch_diarize_models.py rồi tải lại trang.");
    return;
  }
  const on = $("swDiarize").classList.toggle("on");
  try {
    await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ diarize_enabled: on }),
    });
  } catch { $("swDiarize").classList.toggle("on"); }
};
// Toggle server-backed dạng chuỗi "0"/"1" (flag từ nghe không rõ, khử ồn mic).
const SRV_SW = { swFlagWords: "flag_words", swDenoise: "denoise_enabled" };
Object.keys(SRV_SW).forEach(id => $(id).onclick = async () => {
  const on = $(id).classList.toggle("on");
  try {
    await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [SRV_SW[id]]: on ? "1" : "0" }),
    });
  } catch { $(id).classList.toggle("on"); }
});
// T-005: khử ồn mở rộng — phần con chỉ hiện khi master swDenoise bật.
function syncDenoiseAdv(){
  $("denoiseAdv").classList.toggle("hidden", !$("swDenoise").classList.contains("on"));
}
$("swDenoise").addEventListener("click", syncDenoiseAdv);  // sau onclick của SRV_SW (đã toggle .on)
async function putDenoise(body){
  try {
    const r = await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(r.statusText);
  } catch (e) { console.warn("Chưa sync cấu hình khử ồn:", e.message); }
}
// Slider: cập nhật nhãn % liên tục (input), chỉ PUT khi thả (change).
$("dnStrength").addEventListener("input", () => { $("dnStrengthVal").textContent = $("dnStrength").value + "%"; });
$("dnStrength").addEventListener("change", () => putDenoise({ denoise_strength: parseInt($("dnStrength").value, 10) }));
$("dnMode").addEventListener("change", () => putDenoise({ denoise_mode: $("dnMode").value }));
$("dnHum").addEventListener("change", () => putDenoise({ denoise_hum: $("dnHum").value }));
$("swDnRumble").onclick = () => { const on = $("swDnRumble").classList.toggle("on"); putDenoise({ denoise_highpass: on ? 80 : 0 }); };
$("swDnUpload").onclick = () => { const on = $("swDnUpload").classList.toggle("on"); putDenoise({ denoise_upload_enabled: on }); };
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
  if (name === "settings"){ loadServerSettings(); loadSpeakerList(); refreshOpfsExport(); }
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

// Nhãn ngày cho tiêu đề nhóm: hôm nay / hôm qua / thứ + ngày đầy đủ.
function dayLabel(iso){
  const d = new Date(iso);
  if (isNaN(d)) return "Không rõ ngày";
  const ymd = localYMD(d);
  const now = new Date();
  const yest = new Date(now); yest.setDate(yest.getDate() - 1);
  if (ymd === localYMD(now))  return "Hôm nay";
  if (ymd === localYMD(yest)) return "Hôm qua";
  try{ return d.toLocaleDateString("vi-VN", {weekday:"long", day:"numeric", month:"long", year:"numeric"}); }
  catch{ return fmtDate(iso); }
}
// Giờ trong ngày (HH:MM) — hiển thị trên thẻ vì ngày đã nằm ở tiêu đề nhóm.
function fmtHM(iso){ const d = new Date(iso); return isNaN(d) ? "" : localHM(d); }

function recCard(m, opfs){
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
          <span>${ICN.clk} ${fmtHM(m.created_at)}</span>
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
  return li;
}

// Tiêu đề nhóm ngày: nhãn ngày + tóm tắt (số bản ghi · tổng thời lượng).
function dayHeader(label, group){
  const li = document.createElement("li");
  li.className = "day-head";
  const total = group.reduce((s, m) => s + (m.duration || 0), 0);
  li.innerHTML = `<span class="day-name">${label}</span>`
    + `<span class="day-meta">${group.length} bản ghi · ${fmtDur(total)}</span>`;
  return li;
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
  // Gom theo ngày chỉ khi đang sắp theo ngày; sắp theo tên/thời lượng thì để phẳng.
  const grouped = $("sortBy").value.startsWith("date-");
  if (!grouped){
    for (const m of items) box.appendChild(recCard(m, opfs));
    return;
  }
  let curDay = null, curGroup = null, curHead = null;
  for (const m of items){
    const day = (() => { const d = new Date(m.created_at); return isNaN(d) ? "?" : localYMD(d); })();
    if (day !== curDay){
      curDay = day; curGroup = [];
      curHead = dayHeader(dayLabel(m.created_at), curGroup);
      box.appendChild(curHead);
    }
    curGroup.push(m);
    // Cập nhật tóm tắt (số bản ghi · tổng thời lượng) khi thêm bản ghi vào nhóm.
    const total = curGroup.reduce((s, x) => s + (x.duration || 0), 0);
    curHead.querySelector(".day-meta").textContent = `${curGroup.length} bản ghi · ${fmtDur(total)}`;
    box.appendChild(recCard(m, opfs));
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

// ── Xuất bản ghi OPFS về máy (PC/điện thoại) ───────────────────────────────
// Gom mọi file PCM trong trình duyệt thành 1 file .zip chứa WAV — tải 1 lần
// duy nhất nên không bị iOS/Android chặn multi-download. ZIP dùng method
// "store" (WAV vốn không nén được thêm); phần data trỏ thẳng vào File của
// OPFS nên không phải giữ toàn bộ audio trong RAM.
const CRC_TABLE = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
  return c >>> 0;
});
function crcUpdate(state, bytes){
  let c = state;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return c >>> 0;
}
async function crcOfWav(headerBuf, file){
  let state = crcUpdate(0xFFFFFFFF, new Uint8Array(headerBuf));
  const CHUNK = 4 * 1024 * 1024;
  for (let off = 0; off < file.size; off += CHUNK)
    state = crcUpdate(state, new Uint8Array(await file.slice(off, off + CHUNK).arrayBuffer()));
  return (~state) >>> 0;
}
function dosDateTime(d){
  return {
    time: (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1),
    date: ((Math.max(0, d.getFullYear() - 1980) & 0x7f) << 9) | ((d.getMonth() + 1) << 5) | d.getDate(),
  };
}
function zipStore(entries){ // entries: {name, crc, size, mtime, parts:[...]}
  const enc = new TextEncoder();
  const parts = [], central = [];
  let offset = 0;
  for (const e of entries){
    const nameB = enc.encode(e.name);
    const { time, date } = dosDateTime(e.mtime);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true);
    lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true); // UTF-8 name
    lh.setUint16(10, time, true); lh.setUint16(12, date, true);
    lh.setUint32(14, e.crc, true);
    lh.setUint32(18, e.size, true); lh.setUint32(22, e.size, true);
    lh.setUint16(26, nameB.length, true);
    parts.push(lh.buffer, nameB, ...e.parts);
    central.push({ nameB, time, date, crc: e.crc, size: e.size, offset });
    offset += 30 + nameB.length + e.size;
  }
  const cdStart = offset;
  for (const c of central){
    const h = new DataView(new ArrayBuffer(46));
    h.setUint32(0, 0x02014b50, true);
    h.setUint16(4, 20, true); h.setUint16(6, 20, true); h.setUint16(8, 0x0800, true);
    h.setUint16(12, c.time, true); h.setUint16(14, c.date, true);
    h.setUint32(16, c.crc, true);
    h.setUint32(20, c.size, true); h.setUint32(24, c.size, true);
    h.setUint16(28, c.nameB.length, true);
    h.setUint32(42, c.offset, true);
    parts.push(h.buffer, c.nameB);
    offset += 46 + c.nameB.length;
  }
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(8, central.length, true); eocd.setUint16(10, central.length, true);
  eocd.setUint32(12, offset - cdStart, true); eocd.setUint32(16, cdStart, true);
  parts.push(eocd.buffer);
  return new Blob(parts, { type: "application/zip" });
}
async function listOpfsRecordings(){
  const out = [];
  if (!HAS_OPFS) return out;
  try {
    const root = await navigator.storage.getDirectory();
    const dir = await root.getDirectoryHandle("recordings");
    for await (const [name, handle] of dir.entries()){
      if (handle.kind === "file") out.push({ name, file: await handle.getFile() });
    }
  } catch {} // chưa có thư mục recordings → coi như rỗng
  return out;
}
function opfsWavName(name, used){ // tên đẹp trong zip: ngày + title bản ghi
  const map = opfsMap();
  const id = Object.keys(map).find(k => map[k] === name);
  const m = id ? ALL.find(x => x.id === id) : null;
  const ms = Number((name.match(/^live-(\d+)/) || [])[1]);
  const d = m && m.created_at ? new Date(m.created_at) : (ms ? new Date(ms) : new Date());
  const day = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  const title = ((m && m.title) || name.replace(/\.pcm$/, "")).replace(/[/\\:*?"<>|]/g, "-").slice(0, 80);
  let base = day + " " + title, out = base + ".wav", n = 2;
  while (used.has(out)) out = base + " (" + (n++) + ").wav";
  used.add(out);
  return { zipName: out, mtime: d };
}
const fmtBytes = (b) => b >= 1e9 ? (b / 1e9).toFixed(2) + " GB" : b >= 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1e3)) + " KB";
async function refreshOpfsExport(){
  if (!HAS_OPFS){ $("rowOpfsExport").classList.add("hidden"); return; }
  const files = await listOpfsRecordings();
  const total = files.reduce((s, f) => s + f.file.size + 44, 0);
  $("opfsExport").disabled = !files.length;
  $("opfsExportHint").textContent = files.length
    ? files.length + " bản ghi · ~" + fmtBytes(total) + " — gom thành 1 file .zip tải về Downloads/Files"
    : "Chưa có bản ghi âm nào trong trình duyệt này.";
}
$("opfsExport").onclick = async () => {
  const btn = $("opfsExport"), hint = $("opfsExportHint");
  btn.disabled = true;
  try {
    const files = await listOpfsRecordings();
    if (!files.length){ hint.textContent = "Chưa có bản ghi âm nào trong trình duyệt này."; return; }
    const used = new Set(), entries = [];
    for (let i = 0; i < files.length; i++){
      hint.textContent = "Đang đóng gói " + (i + 1) + "/" + files.length + "…";
      const { name, file } = files[i];
      const header = wavHeader(file.size, 16000);
      const { zipName, mtime } = opfsWavName(name, used);
      entries.push({ name: zipName, crc: await crcOfWav(header, file),
                     size: 44 + file.size, mtime, parts: [header, file] });
    }
    const d = new Date();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(zipStore(entries));
    a.download = "manju-ghi-am-" + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + ".zip";
    a.click();
    // Không revoke URL: zip lớn có thể tải lâu, revoke sớm sẽ hủy download.
    // Blob trỏ vào File trên đĩa (OPFS) nên giữ URL không tốn RAM đáng kể.
    hint.textContent = "Đã xuất " + entries.length + " bản ghi. File vẫn còn trong trình duyệt — bật lại Settings để xuất tiếp sau.";
  } catch (e) {
    hint.textContent = "Xuất thất bại: " + (e && e.message || e);
  } finally { btn.disabled = false; }
};

// ── Detail / result ────────────────────────────────────────────────────────
let fixedText = "", rawText = null, editedText = null, downloadName = "transcript.txt";
let curView = "pass2";      // bản đang xem: "edited" | "pass2" | "raw"
let baseTextAtOpen = "";    // bản hiệu lực lúc mở editor — gửi làm base_text khi PATCH (US-801)
// US-819: bản ghi này có đang được dùng làm chuẩn đo WER không. Giữ ngoài
// setResult (đã 7 tham số) thay vì nhồi thêm — set ở nơi có `d`.
let goldenFlag = false;
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
// ── Lớp speaker (nhãn giọng, gán tên, phụ đề) ──────────────────────────────
let SPK_NAMES = {};        // speaker_id → tên
let curSegments = null, curSpeakerMap = {}, curDetailId = null, curHasServerAudio = false;
const SPK_COLORS = ["#7c6cf0","#e0698a","#3fa7d6","#f2a154","#4caf88","#b57edc","#d9534f","#5b7fbd"];
const spkColor = (spk) => SPK_COLORS[((spk % SPK_COLORS.length) + SPK_COLORS.length) % SPK_COLORS.length];
async function loadSpeakers(){
  try { const arr = await (await fetch("/api/speakers")).json();
    SPK_NAMES = {}; for (const s of arr) SPK_NAMES[s.id] = s.name;
  } catch { /* giữ nguyên */ }
}
function clusterLabel(spk){
  const sid = curSpeakerMap[String(spk)];
  if (sid && SPK_NAMES[sid]) return SPK_NAMES[sid];      // đã gán / voiceprint khớp
  const order = Object.keys(curSpeakerMap).map(Number).sort((a,b)=>a-b);
  const idx = order.indexOf(Number(spk));               // Unknown → "Người N" theo thứ tự cụm
  return "Người " + (idx >= 0 ? idx + 1 : spk + 1);
}

function setResult(text, raw, segments, id, hasServerAudio, speakerMap, edited){
  fixedText = text; rawText = raw || null; editedText = edited != null ? edited : null;
  curView = editedText != null ? "edited" : "pass2";
  baseTextAtOpen = editedText != null ? editedText : text;
  curSegments = segments; curSpeakerMap = speakerMap || {}; curDetailId = id;
  curHasServerAudio = !!(hasServerAudio && id);
  resetVarResult();   // đổi bản ghi → xoá kết quả chạy lại của bản trước
  applyView();
  renderSegments(segments);
  showAudio(id, hasServerAudio);
}
async function openDetail(m){
  showScreen("detail");
  $("detTitle").textContent = m.title || "Bản ghi";
  downloadName = (m.title || m.id).replace(/\.[^.]+$/, "") + ".txt";
  setResult("Đang tải…", null, null, null, false, null, null);
  await loadSpeakers();
  const d = await (await fetch("/api/transcripts/" + m.id)).json();
  goldenFlag = !!d.golden;
  setResult(d.text, d.raw_text, d.segments, d.id, !!d.audio, d.speaker_map, d.edited_text);
}

// ── US-801: 3 bản (sửa tay / pass 2 / máy thô) + sửa toàn văn & lưu ─────────
const VIEW_LABELS = { edited: "Bản sửa tay", pass2: "Bản pass 2", raw: "Bản máy thô" };
const viewText = (v) => (v === "edited" ? editedText : v === "raw" ? rawText : fixedText);
function viewOrder(){
  const o = [];
  if (editedText != null) o.push("edited");
  o.push("pass2");
  if (rawText) o.push("raw");
  return o;
}
// Bản hiệu lực (edited ?? pass 2) — dùng cho copy / tải .txt.
const effectiveText = () => (editedText != null ? editedText : fixedText);
function applyView(){
  $("result").value = viewText(curView) || "";
  $("result").readOnly = curView === "raw";        // bản máy thô chỉ xem
  const order = viewOrder();
  const next = order[(order.indexOf(curView) + 1) % order.length];
  $("toggleRaw").style.display = order.length > 1 ? "" : "none";
  $("toggleRaw").textContent = "Xem " + VIEW_LABELS[next].toLowerCase();
  $("editedBadge").classList.toggle("hidden", editedText == null);
  $("delEdit").style.display = editedText != null ? "" : "none";
  // US-819: chỉ đánh dấu chuẩn được khi đã có bản sửa tay — chuẩn phải do
  // người soát, chấm điểm máy bằng chính output của máy là số đẹp giả.
  $("goldenBadge").classList.toggle("hidden", !goldenFlag);
  $("goldenBtn").style.display = editedText != null ? "" : "none";
  $("goldenBtn").textContent = goldenFlag ? "Bỏ đánh dấu chuẩn" : "Dùng làm chuẩn đo";
  $("editHint").style.display = curView === "raw" ? "none" : "";
  // Rà lặp/nhiễu chỉ áp cho bản sửa được (pass 2 / sửa tay), không cho bản máy thô.
  $("reviewFix").style.display = curView === "raw" ? "none" : "";
  // Chạy lại biến thể cần giải mã lại file ghi âm gốc → chỉ hiện khi có audio server.
  $("varPanel").style.display = curHasServerAudio ? "" : "none";
  if (curHasServerAudio) loadVarOptions();
  updateSaveBtn();
}
$("toggleRaw").onclick = () => {
  if (curView !== "raw" && $("result").value !== (viewText(curView) || "")
      && !confirm("Bỏ nội dung đang sửa chưa lưu?")) return;
  const order = viewOrder();
  curView = order[(order.indexOf(curView) + 1) % order.length];
  applyView();
};
// Nút Lưu chỉ hiện khi nội dung khác bản đang xem (không áp dụng cho bản máy thô).
function updateSaveBtn(){
  const dirty = curDetailId && curView !== "raw" && $("result").value !== (viewText(curView) || "");
  $("saveEdit").style.display = dirty ? "" : "none";
}
$("result").addEventListener("input", updateSaveBtn);

async function reloadDetail(){
  if (!curDetailId) return;
  const d = await (await fetch("/api/transcripts/" + curDetailId)).json();
  goldenFlag = !!d.golden;
  setResult(d.text, d.raw_text, d.segments, d.id, !!d.audio, d.speaker_map, d.edited_text);
}
// PATCH bản sửa tay; 409 = bản ghi đã đổi nơi khác → báo + reload. Trả true nếu lưu OK.
async function patchText(editedVal){
  const r = await fetch(`/api/transcripts/${curDetailId}/text`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ edited_text: editedVal, base_text: baseTextAtOpen }),
  });
  if (r.status === 409){
    alert("Bản ghi đã thay đổi ở nơi khác, tải lại rồi sửa tiếp.");
    await reloadDetail();
    return false;
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return true;
}
$("saveEdit").onclick = async () => {
  if (!curDetailId) return;
  const text = $("result").value;
  const b = $("saveEdit"); b.disabled = true;
  try {
    if (await patchText(text)){
      editedText = text; baseTextAtOpen = text; curView = "edited";
      applyView();
    }
  } catch (e) { alert("Lỗi lưu bản sửa: " + e.message); }
  finally { b.disabled = false; }
};
$("delEdit").onclick = async () => {
  if (!curDetailId || editedText == null) return;
  if (!confirm("Xoá bản sửa tay? Sẽ dùng lại bản pass 2.")) return;
  try {
    if (await patchText(null)){
      editedText = null; baseTextAtOpen = fixedText; curView = "pass2";
      applyView();
    }
  } catch (e) { alert("Lỗi xoá bản sửa: " + e.message); }
};
// US-819: bật/tắt cờ dùng bản ghi làm chuẩn đo WER.
$("goldenBtn").onclick = async () => {
  if (!curDetailId) return;
  const b = $("goldenBtn"); b.disabled = true;
  try {
    const r = await fetch(`/api/transcripts/${curDetailId}/golden`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ golden: !goldenFlag }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    goldenFlag = j.golden;
    applyView();
  } catch (e) { alert("Lỗi đánh dấu bản chuẩn: " + e.message); }
  finally { b.disabled = false; }
};
// Rà & sửa lặp/nhiễu toàn văn (hậu kỳ) — Whisper hay lặp cụm/token khi có noise.
// Endpoint trả BẢN XEM TRƯỚC, không tự lưu: đổ vào ô soạn để người dùng soát rồi
// bấm "Lưu bản sửa" (patchText giữ base_text → vẫn chống ghi đè chéo).
$("reviewFix").onclick = async () => {
  if (!curDetailId) return;
  const b = $("reviewFix"); b.disabled = true;
  try {
    const r = await fetch(`/api/transcripts/${curDetailId}/review-fix`, { method: "POST" });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    if (!j.changed){ alert("Không tìm thấy đoạn lặp/nhiễu nào để sửa."); return; }
    $("result").value = j.cleaned;
    updateSaveBtn();
    alert("Đã gom lặp/nhiễu: cắt " + j.chars_removed + " ký tự"
      + (j.dropped.length ? ", bỏ " + j.dropped.length + " cụm nhiễu" : "")
      + ".\nSoát lại rồi bấm Lưu bản sửa để áp.");
  } catch (e) { alert("Lỗi rà sửa: " + e.message); }
  finally { b.disabled = false; }
};

// ── Chạy lại biến thể: phiên bản pipeline × model sửa ──────────────────────
// Giải mã lại file ghi âm gốc trên server (cache theo bản ghi), áp phiên bản
// ASR-cleanup + model LLM chọn được. Vừa test (đổi dropdown so kết quả) vừa cho
// user "Dùng bản này" để lưu thành bản sửa tay.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let varText = "", varOptsLoaded = false;
async function loadVarOptions(){
  if (varOptsLoaded) return;
  try {
    const o = await (await fetch("/api/reanalyze-options")).json();
    $("varVersion").innerHTML = o.versions
      .map(v => `<option value="${v.key}">${v.label}</option>`).join("");
    $("varModel").innerHTML = o.models
      .map(m => `<option value="${m.key}"${m.available ? "" : " disabled"}>`
        + `${m.label}${m.available ? "" : " — chưa có key"}</option>`).join("");
    varOptsLoaded = true;
  } catch { /* để trống → dropdown rỗng, người dùng thấy panel không chạy */ }
}
function resetVarResult(){
  varText = "";
  $("varText").style.display = "none";
  $("varActions").style.display = "none";
  $("varNote").textContent = "Chọn phiên bản xử lý + model sửa rồi “Chạy lại” để "
    + "xem kết quả trên chính bản ghi này.";
}
$("varRun").onclick = async () => {
  if (!curDetailId) return;
  const b = $("varRun"); b.disabled = true; const label = b.textContent;
  b.textContent = "Đang chạy…";
  $("varNote").textContent = "Đang giải mã lại + xử lý… (lần đầu lâu hơn vì phải giải mã audio)";
  try {
    const r = await fetch(`/api/transcripts/${curDetailId}/reanalyze`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: $("varVersion").value, model: $("varModel").value }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    let job;
    for (;;) {
      await sleep(1000);
      job = await (await fetch(`/api/reanalyze/${j.job_id}`)).json();
      if (job.status === "done" || job.status === "error") break;
    }
    if (job.status === "error") throw new Error(job.error || "chạy lại thất bại");
    varText = job.text || "";
    $("varText").textContent = varText || "(rỗng)";
    $("varText").style.display = "";
    $("varActions").style.display = "";
    const vLabel = $("varVersion").selectedOptions[0].textContent;
    const mLabel = $("varModel").selectedOptions[0].textContent;
    const src = job.from_live
      ? "từ segment live gốc (đúng cái live nghe)"
      : "giải mã lại dạng batch — KHÁC luồng live, phiên bản cleanup chỉ tham khảo";
    $("varNote").textContent = `${vLabel} · ${mLabel} — ${job.segments} segment, `
      + `${varText.length} ký tự · ${src}.`;
  } catch (e) { $("varNote").textContent = "Lỗi: " + e.message; }
  finally { b.disabled = false; b.textContent = label; }
};
$("varCopy").onclick = () => navigator.clipboard?.writeText(varText);
$("varApply").onclick = async () => {
  if (!varText || !curDetailId) return;
  if (!confirm("Lưu bản này thành bản sửa tay của bản ghi?")) return;
  const b = $("varApply"); b.disabled = true;
  try {
    if (await patchText(varText)){ await reloadDetail(); }
  } catch (e) { alert("Lỗi lưu: " + e.message); }
  finally { b.disabled = false; }
};

// ── US-801: sửa từng segment inline (click vào text) ───────────────────────
const occCount = (hay, needle) => { let c = 0, i = -1; while ((i = hay.indexOf(needle, i + 1)) >= 0) c++; return c; };
// Thay lần xuất hiện thứ n (0-based) của oldStr trong full; không thấy → null.
function replaceNth(full, oldStr, newStr, n){
  let idx = -1;
  for (let i = 0; i <= n; i++){ idx = full.indexOf(oldStr, idx + 1); if (idx < 0) return null; }
  return full.slice(0, idx) + newStr + full.slice(idx + oldStr.length);
}
function editSegment(s, span){
  const inp = document.createElement("input");
  inp.type = "text"; inp.className = "seg-edit"; inp.value = s.text;
  span.replaceWith(inp); inp.focus();
  let done = false;
  const finish = (commit) => {
    if (done) return; done = true;
    const val = inp.value.trim();
    if (commit && val && val !== s.text) applySegmentEdit(s, val);
    span.textContent = s.text;
    inp.replaceWith(span);
  };
  inp.onkeydown = (e) => {
    if (e.key === "Enter"){ e.preventDefault(); finish(true); }
    else if (e.key === "Escape"){ e.preventDefault(); finish(false); }
  };
  inp.onblur = () => finish(true);
}
// Cập nhật segment + áp thay thế chuỗi tương ứng vào bản full đang xem.
// KHÔNG rebuild full bằng join segments (segment là ASR thô — join mất pass 2 chỗ khác).
function applySegmentEdit(s, val){
  const old = s.text;
  let n = 0;   // old là lần xuất hiện thứ mấy, đếm theo các segment đứng trước
  for (const t of curSegments){ if (t === s) break; n += occCount(t.text, old); }
  s.text = val;
  if (curView === "raw") return;                     // bản máy thô chỉ xem, không áp
  const full = $("result").value;
  const replaced = replaceNth(full, old, val, n)
    ?? (full.includes(old) ? full.replace(old, val) : null);
  if (replaced != null){ $("result").value = replaced; updateSaveBtn(); }
  // không thấy chuỗi gốc trong bản full → giữ sửa ở segment, bỏ qua phần áp
}
// ── Từ khả nghi: gạch đỏ + popup phương án (segment list + live) ────────────
// Tín hiệu: probability từ Whisper < 0.5 → suspect. Dùng chung cho cả 2 nơi.
const SUSPECT_PROB = 0.5;
let suggPop = null;   // popup đang mở (chỉ 1 tại một thời điểm)

// Tách token Whisper thành khoảng-trắng-đầu + lõi từ + khoảng-trắng-cuối.
// Giữ nguyên khoảng trắng gốc: nối lại mọi token = text gốc.
function splitToken(w){
  const lead = w.match(/^\s*/)[0];
  const rest = w.slice(lead.length);
  const trail = rest.match(/\s*$/)[0];
  return { lead, core: rest.slice(0, rest.length - trail.length), trail };
}
function replaceCore(token, core){ const t = splitToken(token); return t.lead + core + t.trail; }

// Dựng span-per-word vào container. wk = mảng làm việc [{w,p}] (bản sao, sửa được).
// Từ suspect (p<0.5) gạch đỏ + bấm mở popup; khoảng trắng giữ nguyên bằng text node.
function fillWordSpans(container, wk, onPick){
  wk.forEach((wd, i) => {
    const { lead, core, trail } = splitToken(wd.w);
    if (lead) container.appendChild(document.createTextNode(lead));
    if (core){
      const sp = document.createElement("span"); sp.className = "word"; sp.textContent = core;
      if (wd.p != null && wd.p < SUSPECT_PROB){
        sp.classList.add("suspect"); sp.title = "Bấm để chọn phương án khác";
        sp.onclick = (e) => { e.stopPropagation(); onPick(i, sp, core); };
      }
      container.appendChild(sp);
    }
    if (trail) container.appendChild(document.createTextNode(trail));
  });
}

async function fetchSuggest(word, context){
  try {
    const r = await fetch("/api/suggest", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word, context: context || "", language: "vi" }),
    });
    if (!r.ok) return [];
    const j = await r.json().catch(() => ({}));
    return Array.isArray(j.alternatives) ? j.alternatives : [];
  } catch { return []; }   // never-fail: lỗi mạng → coi như không có gợi ý
}

function closeSuggPop(){
  if (!suggPop) return;
  suggPop.remove(); suggPop = null;
  document.removeEventListener("mousedown", onSuggDocDown, true);
  document.removeEventListener("keydown", onSuggKey, true);
  window.removeEventListener("scroll", closeSuggPop, true);
  window.removeEventListener("resize", closeSuggPop, true);
}
function onSuggDocDown(e){ if (suggPop && !suggPop.contains(e.target)) closeSuggPop(); }
function onSuggKey(e){ if (e.key === "Escape"){ e.preventDefault(); closeSuggPop(); } }
// Neo popup theo vị trí từ được bấm (position:fixed → toạ độ viewport); lật lên/kẹp mép.
function positionSuggPop(pop, anchor){
  const r = anchor.getBoundingClientRect();
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  const pw = pop.offsetWidth, ph = pop.offsetHeight;
  let left = Math.min(r.left, vw - pw - 8); left = Math.max(8, left);
  let top = r.bottom + 6;
  if (top + ph > vh - 8) top = Math.max(8, r.top - ph - 6);   // hết chỗ dưới → lật lên trên
  pop.style.left = left + "px"; pop.style.top = top + "px";
}
// Mở popup phương án cho từ được bấm. onChoose(newCore) áp lựa chọn.
function openWordPopup(anchor, word, context, onChoose){
  closeSuggPop();
  const pop = document.createElement("div"); pop.className = "sugg-pop";
  const head = document.createElement("div"); head.className = "sugg-head";
  head.textContent = `Phương án cho "${word}"`;
  const list = document.createElement("div"); list.className = "sugg-list";
  const loading = document.createElement("div"); loading.className = "sugg-loading";
  loading.textContent = "Đang tìm phương án…";
  list.appendChild(loading);
  const free = document.createElement("div"); free.className = "sugg-free";
  const inp = document.createElement("input"); inp.type = "text"; inp.placeholder = "Nhập từ khác…";
  const ok = document.createElement("button"); ok.textContent = "Thay";
  const pick = (val) => { const v = (val || "").trim(); if (!v) return; onChoose(v); closeSuggPop(); };
  ok.onclick = () => pick(inp.value);
  inp.onkeydown = (e) => { if (e.key === "Enter"){ e.preventDefault(); pick(inp.value); } };
  free.append(inp, ok);
  pop.append(head, list, free);
  document.body.appendChild(pop);
  positionSuggPop(pop, anchor);
  suggPop = pop;
  // Đăng ký sau tick hiện tại để click mở popup không tự đóng nó.
  setTimeout(() => {
    if (suggPop !== pop) return;
    document.addEventListener("mousedown", onSuggDocDown, true);
    document.addEventListener("keydown", onSuggKey, true);
    window.addEventListener("scroll", closeSuggPop, true);
    window.addEventListener("resize", closeSuggPop, true);
  }, 0);
  fetchSuggest(word, context).then(alts => {
    if (suggPop !== pop) return;   // popup đã bị đóng/thay khi chờ mạng
    list.innerHTML = "";
    if (!alts.length){
      const empty = document.createElement("div"); empty.className = "sugg-empty";
      empty.textContent = "Không có gợi ý — nhập từ khác bên dưới.";
      list.appendChild(empty);
    } else {
      for (const a of alts){
        const b = document.createElement("button"); b.className = "sugg-item"; b.textContent = a;
        b.onclick = () => { onChoose(a); closeSuggPop(); };
        list.appendChild(b);
      }
    }
    const keep = document.createElement("button"); keep.className = "sugg-item sugg-keep";
    keep.textContent = "Giữ nguyên"; keep.onclick = () => closeSuggPop();
    list.appendChild(keep);
    positionSuggPop(pop, anchor);   // list dài hơn sau khi có gợi ý → neo lại
  });
}

function renderSegments(segments){
  const box = $("segList"), btn = $("toggleSegs");
  const wasOpen = box.style.display !== "none";  // giữ trạng thái mở khi vẽ lại (vd sau khi đặt tên)
  box.innerHTML = "";
  const hasSegs = !!(segments && segments.length);
  const hasSpk = hasSegs && segments.some(s => s.spk !== undefined);
  // Nút tách giọng: hiện khi có segment + có file audio server để chạy lại pass 3
  $("diarizeBtn").style.display = (hasSegs && curDetailId && curHasServerAudio) || hasSpk ? "" : "none";
  $("diarizeBtn").textContent = hasSpk ? "Tách lại" : "Tách giọng";
  $("dlSrt").style.display = $("dlVtt").style.display = hasSegs ? "" : "none";
  if (!hasSegs){ box.style.display = "none"; btn.style.display = "none"; return; }
  for (const s of segments){
    const p = document.createElement("p"); p.className = "seg";
    const t = document.createElement("time"); t.textContent = fmtTime(s.start);
    p.append(t);
    if (s.spk !== undefined){
      const chip = document.createElement("button");
      chip.className = "spk-chip"; chip.style.background = spkColor(s.spk);
      chip.textContent = clusterLabel(s.spk); chip.title = "Bấm để đặt tên";
      chip.onclick = () => assignCluster(s.spk);
      p.append(chip);
    }
    const span = document.createElement("span"); span.className = "seg-txt";
    if (s.words && s.words.length){
      // Bản sao làm việc — mỗi lần chọn phương án cập nhật wk rồi dựng lại full text
      // (an toàn khi sửa nhiều từ trong 1 câu trước khi vẽ lại).
      const wk = s.words.map(w => ({ w: w.w, p: w.p }));
      span.title = "Bấm từ gạch đỏ để chọn lại; bấm chữ để sửa cả câu";
      fillWordSpans(span, wk, (i, sp, core) => {
        openWordPopup(sp, core, s.text, (chosen) => {
          wk[i].w = replaceCore(wk[i].w, chosen);
          applySegmentEdit(s, wk.map(x => x.w).join(""));   // tái dùng: PATCH + học cặp
          sp.textContent = chosen; sp.classList.remove("suspect"); sp.onclick = null; sp.title = "";
        });
      });
      span.onclick = () => editSegment(s, span);   // bấm vùng chữ thường → sửa cả segment
    } else {
      span.textContent = s.text; span.title = "Bấm để sửa";
      span.onclick = () => editSegment(s, span);
    }
    p.append(span); box.appendChild(p);
  }
  box.style.display = wasOpen ? "" : "none";
  btn.style.display = ""; btn.textContent = wasOpen ? "Ẩn mốc" : "Mốc thời gian";
}
$("toggleSegs").onclick = () => { const h = $("segList").style.display === "none"; $("segList").style.display = h ? "" : "none"; $("toggleSegs").textContent = h ? "Ẩn mốc" : "Mốc thời gian"; };

async function assignCluster(spk){
  const cur = SPK_NAMES[curSpeakerMap[String(spk)]] || "";
  const name = prompt("Tên người cho giọng này (để trống = bỏ gán):", cur);
  if (name === null) return;
  try {
    const r = await fetch(`/api/transcripts/${curDetailId}/speaker-map`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cluster: spk, name: name.trim() }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.statusText);
    curSpeakerMap = j.speaker_map;
    SPK_NAMES = { ...SPK_NAMES, ...(j.speakers || {}) };
    renderSegments(curSegments);
    // US-703: ghi nhớ giọng để tự nhận diện bản ghi sau.
    const sid = curSpeakerMap[String(spk)];
    if (sid && diarizeReady && confirm(`Ghi nhớ giọng "${SPK_NAMES[sid]}" để tự nhận diện lần sau?`))
      await enrollCluster(sid, spk);
  } catch (e) { alert("Lỗi gán tên: " + e.message); }
}
async function enrollCluster(speakerId, spk){
  try {
    const r = await fetch(`/api/speakers/${speakerId}/enroll`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript_id: curDetailId, cluster: spk }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.statusText);
  } catch (e) { alert("Lỗi ghi nhớ giọng: " + e.message); }
}

// ── Quản lý giọng đã lưu (Cài đặt) ─────────────────────────────────────────
async function loadSpeakerList(){
  const box = $("speakerList");
  let arr = [];
  try { arr = await (await fetch("/api/speakers")).json(); } catch { return; }
  if (!arr.length){ box.innerHTML = '<small style="color:var(--faint)">Chưa có giọng nào được ghi nhớ.</small>'; return; }
  box.innerHTML = "";
  for (const s of arr){
    const row = document.createElement("div"); row.className = "spk-row";
    const nm = document.createElement("span"); nm.className = "nm"; nm.textContent = s.name;
    const ct = document.createElement("span"); ct.className = "ct";
    ct.textContent = s.voiceprints ? `${s.voiceprints} mẫu giọng` : "chưa có mẫu giọng";
    const rg = document.createElement("select"); rg.title = "Giọng miền nào";
    for (const [v, lbl] of SPK_REGIONS){
      const o = document.createElement("option"); o.value = v; o.textContent = lbl; rg.append(o);
    }
    rg.value = s.region || "";
    rg.onchange = () => setSpeakerRegion(s.id, rg.value);
    const ren = document.createElement("button"); ren.className = "mini"; ren.textContent = "Đổi tên";
    ren.onclick = () => renameSpeaker(s.id, s.name);
    const del = document.createElement("button"); del.className = "mini"; del.textContent = "Xoá";
    del.onclick = () => deleteSpeaker(s.id, s.name);
    row.append(nm, ct, rg, ren, del); box.appendChild(row);
  }
}
const SPK_REGIONS = [["", "(trống)"], ["bac", "Bắc"], ["trung", "Trung"], ["nam", "Nam"]];
async function setSpeakerRegion(id, region){
  await fetch(`/api/speakers/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ region }) });
  loadSpeakerList();
}
async function renameSpeaker(id, cur){
  const name = prompt("Đổi tên người:", cur);
  if (!name || !name.trim()) return;
  await fetch(`/api/speakers/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim() }) });
  loadSpeakerList();
}
async function deleteSpeaker(id, name){
  if (!confirm(`Xoá giọng "${name}"? Các bản ghi đã gán sẽ trở về chưa đặt tên.`)) return;
  await fetch(`/api/speakers/${id}`, { method: "DELETE" });
  loadSpeakerList();
}

// ── US-802: Thư viện từ — cặp sửa lỗi (Settings, load lười khi mở mục) ─────
const CORR_SRC = { user: "Bạn sửa", seed: "Có sẵn", remote: "Tải về", trend: "Xu hướng" };
const CORR_ST = { pending: "Chờ duyệt", approved: "Đã duyệt", rejected: "Đã loại" };
const CORR_TAG_SUGS = ["bắc", "trung", "nam", "en"];
let corrFetched = false;   // chỉ fetch lần đầu mở mục, không fetch lúc khởi động

$("corrToggle").onclick = () => {
  const open = !$("corrBody").classList.toggle("hidden");
  $("corrChevron").textContent = open ? "▾" : "▸";
  if (open && !corrFetched){ corrFetched = true; loadCorrections(); }
  if (open) refreshSlangBadge();
};
["corrStatus", "corrSource", "corrTagFilter"].forEach(id => $(id).addEventListener("change", loadCorrections));
$("corrTagFilter").addEventListener("keydown", e => { if (e.key === "Enter") loadCorrections(); });

async function loadCorrections(){
  const qs = new URLSearchParams();
  if ($("corrStatus").value) qs.set("status", $("corrStatus").value);
  if ($("corrSource").value) qs.set("source", $("corrSource").value);
  const tag = $("corrTagFilter").value.trim();
  if (tag) qs.set("tag", tag);
  const q = qs.toString();
  try {
    const r = await fetch("/api/corrections" + (q ? "?" + q : ""));
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    renderCorrections(await r.json());
  } catch (e) { alert("Lỗi tải thư viện từ: " + e.message); }
  refreshSlangBadge();  // duyệt/loại/xoá đều đi qua đây → badge luôn tươi
}
function renderCorrections(arr){
  const box = $("corrList");
  box.innerHTML = "";
  if (!arr.length){
    const filtered = $("corrStatus").value || $("corrSource").value || $("corrTagFilter").value.trim();
    box.innerHTML = `<small style="color:var(--faint)">${filtered
      ? "Không có cặp nào khớp bộ lọc."
      : "Chưa có cặp nào — sửa transcript trong màn chi tiết để app bắt đầu học."}</small>`;
    return;
  }
  for (const c of arr) box.appendChild(corrRow(c));
}
const corrMini = (label, fn, pri) => {
  const b = document.createElement("button");
  b.className = "mini" + (pri ? " pri" : ""); b.textContent = label; b.onclick = fn;
  return b;
};
function corrRow(c){
  const row = document.createElement("div"); row.className = "corr-item";
  const pair = document.createElement("div"); pair.className = "corr-pair";
  const w = document.createElement("span"); w.className = "w"; w.textContent = c.wrong;
  const r = document.createElement("span"); r.className = "r"; r.textContent = c.right;
  pair.append(w, " → ", r);
  const meta = document.createElement("div"); meta.className = "corr-meta";
  const tag = document.createElement("button"); tag.className = "chip corr-tag";
  tag.textContent = c.tag ? "🏷 " + c.tag : "🏷 tag…"; tag.title = "Bấm để sửa tag";
  tag.onclick = () => editCorrTag(c, tag);
  const src = document.createElement("span"); src.className = "chip";
  src.textContent = CORR_SRC[c.source] || c.source;
  const ct = document.createElement("span"); ct.className = "chip"; ct.textContent = "×" + (c.count || 0);
  const st = document.createElement("span"); st.className = "chip badge-" + c.status;
  st.textContent = CORR_ST[c.status] || c.status;
  meta.append(tag, src, ct, st);
  const acts = document.createElement("div"); acts.className = "corr-acts";
  if (c.status !== "approved") acts.append(corrMini("✓ Duyệt", () => patchCorrection(c.id, { status: "approved" }), true));
  if (c.status !== "rejected") acts.append(corrMini("✕ Loại", () => patchCorrection(c.id, { status: "rejected" })));
  acts.append(corrMini("🗑", () => deleteCorrection(c)));
  meta.append(acts);
  row.append(pair, meta);
  return row;
}
// Re-render cả list sau mỗi hành động (giữ style codebase).
async function patchCorrection(id, body){
  try {
    const r = await fetch("/api/corrections/" + id, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  } catch (e) { alert("Lỗi cập nhật cặp sửa lỗi: " + e.message); }
  loadCorrections();
}
async function deleteCorrection(c){
  if (!confirm(`Xoá cặp "${c.wrong} → ${c.right}"?`)) return;
  try {
    const r = await fetch("/api/corrections/" + c.id, { method: "DELETE" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  } catch (e) { alert("Lỗi xoá cặp sửa lỗi: " + e.message); }
  loadCorrections();
}
// ── US-804: seed lexicon vùng miền + cập nhật online (opt-in) ──────────────
const LEX_IDS = { lexBac: "bac", lexTrung: "trung", lexNam: "nam", lexEn: "en", lexSlang: "slang" };
Object.entries(LEX_IDS).forEach(([id, key]) => $(id).addEventListener("change", async () => {
  try {
    const r = await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ["lexicon_" + key]: $(id).checked }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  } catch (e) { $(id).checked = !$(id).checked; alert("Lỗi bật/tắt từ địa phương: " + e.message); }
  loadCorrections();
}));
function syncLexBtn(){
  const has = !!$("lexUrl").value.trim();
  $("lexUpdate").disabled = !has;
  $("lexUpdate").title = has ? "Tải cặp mới từ nguồn đã cấu hình" : "Nhập URL nguồn cập nhật trước";
}
$("lexUrl").addEventListener("input", syncLexBtn);
$("lexUrl").addEventListener("change", () => putLexiconUrl($("lexUrl").value.trim()));
async function putLexiconUrl(url){
  try {
    const r = await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lexicon_url: url }),
    });
    if (!r.ok) throw new Error(r.statusText);
  } catch (e) { console.warn("Chưa sync URL thư viện lên server:", e.message); }
}
// ── US-816/817: LLM + trang web public tổng hợp tiếng lóng → pending duyệt ─
$("slangSources").addEventListener("change", async () => {
  try {
    await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slang_sources: $("slangSources").value.trim() }),
    });
  } catch (e) { console.warn("Chưa sync nguồn trend lên server:", e.message); }
});
$("slangHashtags").addEventListener("change", async () => {
  try {
    await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slang_hashtags: $("slangHashtags").value.trim() }),
    });
  } catch (e) { console.warn("Chưa sync hashtag Apify lên server:", e.message); }
});
$("slangTrend").onclick = async () => {
  const btn = $("slangTrend");
  btn.disabled = true;
  try {
    const r = await fetch("/api/lexicon/slang-trend", { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    alert(`Đã thêm ${d.new_pending} cặp tiếng lóng chờ duyệt (bỏ qua ${d.skipped}).`);
    // Nhảy thẳng vào flow duyệt: lọc sẵn pending + nguồn Xu hướng
    $("corrStatus").value = "pending"; $("corrSource").value = "trend";
    loadCorrections();
  } catch (e) { alert("Lỗi cập nhật tiếng lóng: " + e.message); }
  btn.disabled = false;
};
// ── Lớp B/C: tự đoán ngành + research thuật ngữ ngành ──────────────────────
function populateDomainPick(domains){
  const sel = $("domainPick");
  if (sel.options.length === domains.length && domains.length) return;  // đã fill
  sel.innerHTML = "";
  for (const d of domains){
    const o = document.createElement("option"); o.value = d; o.textContent = d;
    sel.appendChild(o);
  }
}
$("domainAuto").addEventListener("change", async () => {
  try {
    const r = await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain_auto: $("domainAuto").checked }),
    });
    if (!r.ok) throw new Error(r.statusText);
  } catch (e) { $("domainAuto").checked = !$("domainAuto").checked; alert("Lỗi bật/tắt đoán ngành: " + e.message); }
});
$("domainResearch").onclick = async () => {
  const btn = $("domainResearch"), domain = $("domainPick").value;
  if (!domain) return;
  btn.disabled = true;
  try {
    const r = await fetch("/api/lexicon/domain-research", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    alert(`Đã thêm ${d.new_pending} thuật ngữ «${domain}» chờ duyệt (bỏ qua ${d.skipped}).`);
    $("corrStatus").value = "pending"; $("corrSource").value = "trend";
    loadCorrections();
  } catch (e) { alert("Lỗi research thuật ngữ ngành: " + e.message); }
  btn.disabled = false;
};
async function refreshSlangBadge(){
  try {
    const rows = await (await fetch("/api/corrections?status=pending&source=trend")).json();
    const b = $("slangPendingBadge");
    b.classList.toggle("hidden", !rows.length);
    b.textContent = rows.length ? `${rows.length} chờ duyệt` : "";
  } catch { /* badge chỉ là trang trí — lỗi thì thôi */ }
}

$("lexUpdate").onclick = async () => {
  await putLexiconUrl($("lexUrl").value.trim());  // chốt URL trước khi gọi update
  try {
    const r = await fetch("/api/lexicon/update", { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    alert(`Đã nhập ${d.imported} cặp mới.`);
    loadCorrections();
  } catch (e) { alert("Lỗi cập nhật thư viện: " + e.message); }
};

// Sửa tag inline: Enter lưu, Esc/blur huỷ; chip gợi ý nhanh (mousedown để thắng blur).
function editCorrTag(c, tagBtn){
  const wrap = document.createElement("span"); wrap.className = "tag-editwrap";
  const inp = document.createElement("input");
  inp.className = "tag-edit"; inp.value = c.tag || ""; inp.placeholder = "tag…";
  const sugs = document.createElement("span"); sugs.className = "tag-sugs";
  let done = false;
  const save = (val) => { if (done) return; done = true; patchCorrection(c.id, { tag: val.trim() }); };
  const cancel = () => { if (done) return; done = true; wrap.replaceWith(tagBtn); };
  for (const t of CORR_TAG_SUGS){
    const s = document.createElement("button"); s.className = "chip"; s.textContent = t;
    s.onmousedown = (e) => { e.preventDefault(); save(t); };   // trước blur của input
    sugs.append(s);
  }
  wrap.append(inp, sugs);
  tagBtn.replaceWith(wrap);
  inp.focus();
  inp.onkeydown = (e) => {
    if (e.key === "Enter"){ e.preventDefault(); save(inp.value); }
    else if (e.key === "Escape"){ e.preventDefault(); cancel(); }
  };
  inp.onblur = cancel;
}

function downloadSub(fmt){
  if (!curDetailId) return;
  const a = document.createElement("a");
  a.href = `/api/transcripts/${curDetailId}/subtitle?format=${fmt}`;
  a.download = downloadName.replace(/\.txt$/, "." + fmt);
  a.click();
}
$("dlSrt").onclick = () => downloadSub("srt");
$("dlVtt").onclick = () => downloadSub("vtt");

$("diarizeBtn").onclick = async () => {
  if (!curDetailId) return;
  const named = Object.values(curSpeakerMap).some(v => v);
  if (named && !confirm("Tách giọng lại sẽ xoá các tên đã gán cho bản ghi này. Tiếp tục?")) return;
  const b = $("diarizeBtn"); b.disabled = true; const old = b.textContent; b.textContent = "Đang tách…";
  try {
    const r = await fetch(`/api/transcripts/${curDetailId}/diarize`, { method: "POST" });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.statusText);
    curSegments = j.segments; curSpeakerMap = j.speaker_map;
    renderSegments(curSegments);
    $("segList").style.display = ""; $("toggleSegs").textContent = "Ẩn mốc";
  } catch (e) { alert("Lỗi tách giọng: " + e.message); }
  finally { b.disabled = false; b.textContent = old; }
};
// Copy / tải .txt dùng bản hiệu lực (edited ?? pass 2), không phụ thuộc bản đang xem.
$("copy").onclick = () => { navigator.clipboard.writeText(effectiveText()); $("copy").textContent = "Đã copy!"; setTimeout(()=>$("copy").textContent="Copy",1200); };
$("download").onclick = () => { const b = new Blob([effectiveText()],{type:"text/plain"}); const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = downloadName; a.click(); };

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
let live = null;   // {conn, ctx, stream, opfs, stopping, paused, base, resumeAt, timer}
let tw = null;

function enterRecord(){
  showScreen("record");
  if (live){ showStartCard(false); return; }   // đang ghi → về thẳng màn ghi
  showStartCard(true);
  loadParticipants();
}
function showStartCard(on){
  $("startCard").classList.toggle("hidden", !on);
  $("recBody").classList.toggle("hidden", on);
}

// ── Thẻ chuẩn bị: tên họp + agenda + người tham gia (nhớ lần trước) ────────
const PART_KEY = "manju.participants";
let partList = [];   // speakers đang hiện checkbox, giữ nguyên kiểu id
async function loadParticipants(){
  const box = $("mtParticipants");
  box.innerHTML = ""; partList = [];
  $("mtPartDD").classList.add("hidden"); $("mtPartLbl").classList.add("hidden");
  let arr = [];
  try { arr = await (await fetch("/api/speakers")).json(); } catch { return; }   // lỗi → ẩn list, vẫn cho Bắt đầu
  if (!arr.length) return;
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem(PART_KEY)) || []; } catch {}
  const savedIds = saved.map(String);
  partList = arr;
  for (const s of arr){
    const lb = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = savedIds.includes(String(s.id));
    cb.onchange = updatePartSummary;
    lb.append(cb, " " + s.name);
    box.appendChild(lb);
  }
  $("mtPartDD").classList.remove("hidden"); $("mtPartLbl").classList.remove("hidden");
  updatePartSummary();
}
function updatePartSummary(){
  const el = $("mtPartSummary");
  const names = [];
  $("mtParticipants").querySelectorAll("input").forEach((cb, i) => { if (cb.checked && partList[i]) names.push(partList[i].name); });
  if (!names.length){ el.textContent = "Chọn người tham gia…"; el.classList.add("ph"); return; }
  el.classList.remove("ph");
  el.textContent = names.length <= 2 ? names.join(", ") : `${names.length} người tham gia`;
}
function checkedParticipants(){
  const ids = [];
  $("mtParticipants").querySelectorAll("input").forEach((cb, i) => { if (cb.checked && partList[i]) ids.push(partList[i].id); });
  return ids;
}
$("startBtn").onclick = () => {
  if (live) return;
  const ids = checkedParticipants();
  localStorage.setItem(PART_KEY, JSON.stringify(ids));
  const meta = {};
  if (ids.length) meta.participants = ids;
  const title = $("mtTitle").value.trim(); if (title) meta.title = title;
  const agenda = $("mtAgenda").value.trim(); if (agenda) meta.agenda = agenda;
  showStartCard(false);
  startLive(meta);
};
function setPill(text, on){ recPill.innerHTML = `<span class="dot"></span> ${text}`; recPill.classList.toggle("live", !!on); }
function setWave(state){ wave.classList.toggle("on", state === "on"); wave.classList.toggle("paused", state === "paused"); }

function tickTimer(){ if (!live) return; const el = live.base + (live.paused ? 0 : (Date.now() - live.resumeAt)/1000); recTimer.textContent = fmtClock(el); }

async function startLive(meta){
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
      correct:$("correct").checked, store_audio: !useOpfs, ...(meta || {}) },
    handleLiveMsg,
    (state) => {
      if (!live) return;
      if (state === "reconnecting"){ setPill("MẤT MẠNG — ĐANG NỐI LẠI…", false); return; }
      if (state === "resumed"){ setPill("RECORDING", true); return; }
      if (state === "dead") cleanupLive();   // sau Stop: stopLive đã chuyển handler sang bgSaveStart
    }
  );
  live = { conn, ctx, stream, opfs: useOpfs ? startOpfsWriter() : null,
           stopping:false, paused:false, base:0, resumeAt:Date.now(),
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
  if (!live) return;   // phiên đã rời (Stop → handler nền) — không đụng DOM màn ghi
  if (m.type === "ready"){ setPill("RECORDING", true); return; }
  if (m.type === "error"){ setPill("LỖI", false); recHint.textContent = m.message; return; }
  if (m.type === "partial"){
    const el = subEl(m.utt); el.classList.add("partial");
    if (!tw || tw.utt !== m.utt) startTypewriter(m.utt, el);
    retarget(m.text);
  } else if (m.type === "final"){
    stopTypewriter(m.utt); const el = subEl(m.utt);
    if (!m.text){ el.remove(); return; }
    if (m.words && m.words.length) renderLineWords(el, m.words, m.text);   // có gạch đỏ
    else setLine(el, m.text);
    el.classList.remove("partial"); autoscroll();
  } else if (m.type === "corrected" || m.type === "revise"){
    if (m.type === "corrected" && !m.changed) return;
    const el = subs.querySelector(`[data-utt="${m.utt}"]`);
    if (el){ setLine(el, m.text); el.classList.remove("fixed"); void el.offsetWidth; el.classList.add("fixed"); }
  } else if (m.type === "speaker"){
    const el = subs.querySelector(`[data-utt="${m.utt}"]`);
    if (el){ el.dataset.spk = m.name; setLine(el, el.dataset.raw != null ? el.dataset.raw : el.textContent); }
  }
  // "saved" không tới đây: stopLive() đã chuyển onMsg sang bgSaveMsg (lưu nền).
}
function subEl(utt){ let el = subs.querySelector(`[data-utt="${utt}"]`); if (!el){ el = document.createElement("p"); el.className = "line"; el.dataset.utt = utt; subs.appendChild(el); } return el; }
// Vẽ 1 dòng subtitle: giữ text gốc trong dataset.raw, thêm "Tên: " nếu đã biết người nói.
function setLine(el, text){ el.dataset.raw = text; el.textContent = el.dataset.spk ? el.dataset.spk + ": " + text : text; }
// Dòng live có words ([[word,prob],…]): dựng span-per-word, gạch đỏ từ khả nghi.
// Giữ tên người nói (prefix) + data-utt + data-raw. Chọn phương án → setLine (plain).
function renderLineWords(el, words, text){
  const wk = words.map(([w, p]) => ({ w, p }));
  el.dataset.raw = text; el.textContent = "";
  if (el.dataset.spk) el.appendChild(document.createTextNode(el.dataset.spk + ": "));
  fillWordSpans(el, wk, (i, sp, core) => {
    openWordPopup(sp, core, text, (chosen) => {
      wk[i].w = replaceCore(wk[i].w, chosen);
      setLine(el, wk.map(x => x.w).join(""));   // live: chỉ thay trong dòng, lưu khi kết phiên
    });
  });
}
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

// stop & close: rời màn ghi NGAY, chốt & lưu chạy nền (US-825)
$("recStop").onclick = stopLive;
$("recClose").onclick = () => { if (live) stopLive(); else showScreen("home"); };
function stopLive(){
  if (!live || live.stopping) return;
  live.stopping = true;
  stopAudio();
  const { conn, opfs } = live;
  endLive({ keepConn: true });   // WS giữ mở — bgSaveStart tiếp quản chờ "saved"
  showScreen("home");
  bgSaveStart(conn, opfs);
}
function stopAudio(){ if (!live) return; try{ live.stream.getTracks().forEach(t => t.stop()); }catch{} try{ live.ctx.close(); }catch{} }
// Dọn mọi timer/state của phiên ghi và giải phóng `live`.
function endLive(opts){
  if (live){ clearInterval(live.timer); if (!(opts && opts.keepConn)) try{ live.conn.close(); }catch{} }
  if (tw){ clearInterval(tw.timer); tw = null; }
  releaseWakeLock();
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

// ── Lưu nền sau Stop (US-825) ──────────────────────────────────────────────
// WS chạy nền chờ "saved" (shutdown server có thể chậm → deadline 60s). Mọi
// nhánh kết thúc (saved / deadline / WS chết / resume bị từ chối) đều chốt
// OPFS đúng 1 lần qua bgOpfsDone — audio trên máy không còn mồ côi.
function bgSaveStart(conn, opfs){
  const bg = { conn, opfs, opfsDone: null, saved: false, failed: false };
  bg.deadline = setTimeout(() => bgSaveFail(bg, true), 60000);
  conn.onMsg = (m) => bgSaveMsg(bg, m);
  conn.onState = (s) => {
    // Đứt ngay lúc Stop: LiveConn tự nối lại → gửi lại "stop"; hết cửa → fail.
    if (s === "resumed"){ conn.sendJSON({ type: "stop" }); return; }
    if (s === "dead") bgSaveFail(bg, false);
  };
  saveBadge("Đang lưu nền…", true, 0);
  conn.sendJSON({ type: "stop" });   // false = WS đang đứt → nhánh resumed/dead lo
}
// finish() chỉ gọi được 1 lần (worker terminate sau đó) → memoize promise.
function bgOpfsDone(bg){
  if (!bg.opfsDone) bg.opfsDone = bg.opfs ? bg.opfs.finish() : Promise.resolve(null);
  return bg.opfsDone;
}
function bgSaveMsg(bg, m){
  if (m.type === "saved" && !bg.saved){
    bg.saved = true;   // "saved" tới muộn sau deadline vẫn cập nhật được (WS còn mở)
    clearTimeout(bg.deadline);
    try{ bg.conn.close(); }catch{}
    bgOpfsDone(bg)
      .then(file => { if (file && m.transcript_id) rememberOpfs(m.transcript_id, file); })
      .then(loadHistory)
      .then(() => saveBadge("Đã lưu ✓", false, 5000));
  } else if (m.type === "error"){
    // Resume bị từ chối ("phiên đã kết thúc — đã tự lưu") → đóng WS để
    // LiveConn ngừng vòng nối lại, rồi chốt OPFS.
    try{ bg.conn.close(); }catch{}
    bgSaveFail(bg, false);
  }
  // partial/final/corrected/revise/speaker sau khi rời màn ghi: bỏ qua —
  // subtitles có thể đã thuộc phiên mới; server tự lưu các bản sửa muộn.
}
function bgSaveFail(bg, keepConn){   // keepConn: nhánh deadline — chờ "saved" muộn
  if (bg.saved || bg.failed) return;
  bg.failed = true;
  if (!keepConn){ clearTimeout(bg.deadline); try{ bg.conn.close(); }catch{} }
  bgOpfsDone(bg);   // chốt file PCM: audio giữ được trên máy (nghe/xuất từ Settings)
  saveBadge(bg.opfs ? "Mất kết nối — bản ghi audio đã giữ trên máy"
                    : "Mất kết nối — server sẽ tự lưu bản ghi trong ~1 phút", false, 8000);
}
// Badge trạng thái lưu nền: pill nổi giữa mép trên, không chặn thao tác.
let saveBadgeTimer = null;
function saveBadge(text, blink, hideMs){
  let el = $("saveBadge");
  if (!el){
    el = document.createElement("div");
    el.id = "saveBadge"; el.className = "pill";
    el.style.cssText = "position:fixed;top:calc(14px + env(safe-area-inset-top));" +
      "left:50%;transform:translateX(-50%);z-index:1300;box-shadow:0 6px 18px rgba(15,23,42,.18);";
    document.body.appendChild(el);
  }
  clearTimeout(saveBadgeTimer);
  el.innerHTML = `<span class="dot"></span> ${text}`;
  el.classList.toggle("live", !!blink);
  el.classList.remove("hidden");
  if (hideMs) saveBadgeTimer = setTimeout(() => el.classList.add("hidden"), hideMs);
}
window.addEventListener("beforeunload", () => { if (live){ try{ live.conn.close(); }catch{} } });

// ── Init ───────────────────────────────────────────────────────────────────
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
loadSettings();
loadServerSettings();
loadHistory();
// Deep-link tiện dụng (và để xem trước từng màn): #settings / #upload / #record.
if (location.hash === "#record") enterRecord();
else if (["#settings","#upload"].includes(location.hash)) showScreen(location.hash.slice(1));
