// FR-11 Manju Enterprise — lớp UI/luồng chỉ bật khi server trả edition "enterprise".
// Nạp TRƯỚC app.js: cài fetch wrapper (401 → màn đăng nhập) và khai báo các hàm
// app.js gọi có điều kiện (startLiveDirect, stopLiveDirect, applyEdition).
// Audio không bao giờ lên server: PCM → Soniox (temp key) + OPFS; server chỉ nhận text.

let EDITION = "local";
let ME = null;   // user hiện tại {id, name, phone, role, status}

// ── 401/403 → cổng đăng nhập ───────────────────────────────────────────────
const _rawFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const r = await _rawFetch(input, init);
  const url = typeof input === "string" ? input : (input && input.url) || "";
  if (url.startsWith("/api/") && !url.startsWith("/api/auth/")){
    if (r.status === 401) showAuthGate("login");
    else if (r.status === 403){
      try{ const d = (await r.clone().json()).detail || ""; if (/chờ admin duyệt/i.test(d)) showAuthGate("pending"); }catch{}
    }
  }
  return r;
};

function showAuthGate(mode){
  const g = document.getElementById("authGate");
  if (!g) return;
  g.classList.remove("hidden");
  document.getElementById("authLogin").classList.toggle("hidden", mode !== "login");
  document.getElementById("authRegister").classList.toggle("hidden", mode !== "register");
  document.getElementById("authPending").classList.toggle("hidden", mode !== "pending");
  document.getElementById("authErr").textContent = "";
}
function hideAuthGate(){ document.getElementById("authGate").classList.add("hidden"); }

async function authPost(path, body){
  const r = await _rawFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || ("Lỗi " + r.status));
  return data;
}

async function afterAuth(user){
  ME = user;
  if (user.status === "pending"){ showAuthGate("pending"); return; }
  hideAuthGate();
  // Nạp lại dữ liệu đã bị 401 lúc khởi động.
  if (typeof loadServerSettings === "function") loadServerSettings();
  if (typeof loadHistory === "function") loadHistory();
}

function wireAuthGate(){
  const err = document.getElementById("authErr");
  document.getElementById("authLoginBtn").onclick = async () => {
    err.textContent = "";
    try{
      const u = await authPost("/api/auth/login", { phone: document.getElementById("authPhone").value, password: document.getElementById("authPass").value });
      await afterAuth(u);
    }catch(e){ err.textContent = e.message; }
  };
  document.getElementById("authRegBtn").onclick = async () => {
    err.textContent = "";
    try{
      const u = await authPost("/api/auth/register", {
        phone: document.getElementById("regPhone").value, name: document.getElementById("regName").value,
        password: document.getElementById("regPass").value,
      });
      await afterAuth(u);
    }catch(e){ err.textContent = e.message; }
  };
  document.getElementById("authToReg").onclick = () => showAuthGate("register");
  document.getElementById("authToLogin").onclick = () => showAuthGate("login");
  document.getElementById("authPendingLogout").onclick = logout;
  document.getElementById("authPass").addEventListener("keydown", e => { if (e.key === "Enter") document.getElementById("authLoginBtn").click(); });
}
async function logout(){
  try{ await _rawFetch("/api/auth/logout", { method: "POST" }); }catch{}
  ME = null; location.reload();
}

// ── Áp edition lên UI (gọi từ loadServerSettings sau khi có /api/settings) ──
async function applyEdition(s){
  EDITION = s.edition || "local";
  document.body.classList.toggle("enterprise", EDITION === "enterprise");
  if (EDITION !== "enterprise") return;
  if (!ME){ try{ const r = await _rawFetch("/api/auth/me"); if (r.ok) ME = await r.json(); }catch{} }
  const hero = document.querySelector("#screen-settings .hero-pro");
  if (hero){
    hero.querySelector("h3").innerHTML = ico("building") + " Manju Enterprise";
    hero.querySelector("p").textContent = "Audio ở lại máy bạn (trình duyệt), chỉ văn bản lưu trên server công ty. Nhận dạng qua Soniox.";
    hero.querySelector(".cta").textContent = ME ? `${ME.name} · ${ME.role === "admin" ? "Admin" : "Thành viên"}` : "Đã đăng nhập";
  }
  const foot = document.querySelector(".side-foot");
  if (foot) foot.innerHTML = "Enterprise · text lưu trên server công ty<br/>audio ở lại máy bạn.";
  const glossary = document.getElementById("prompt");
  if (glossary && ME && ME.role !== "admin"){ glossary.readOnly = true; glossary.placeholder = "Glossary công ty — admin chỉnh sửa"; }
  document.getElementById("entSection").classList.remove("hidden");
  document.getElementById("entUserName").textContent = ME ? `${ME.name} (${ME.phone})` : "";
  document.getElementById("usersSection").classList.toggle("hidden", !(ME && ME.role === "admin"));
  if (ME && ME.role === "admin") loadUsers();
  loadTokens();
}

// ── Quản lý user (admin) ───────────────────────────────────────────────────
const STATUS_VI = { pending: "Chờ duyệt", active: "Hoạt động", blocked: "Đã khoá" };
async function loadUsers(){
  const box = document.getElementById("userList");
  let users = [];
  try{ users = await (await fetch("/api/users")).json(); }catch{ return; }
  box.innerHTML = "";
  for (const u of users){
    const row = document.createElement("div"); row.className = "set-row";
    const main = document.createElement("div"); main.className = "set-main";
    main.innerHTML = `<b></b><small></small>`;
    main.querySelector("b").textContent = `${u.name} · ${u.phone}`;
    main.querySelector("small").textContent = `${u.role === "admin" ? "Admin" : "Thành viên"} · ${STATUS_VI[u.status] || u.status}`;
    const act = document.createElement("div"); act.style.display = "flex"; act.style.gap = "6px";
    const mk = (label, patch, pri) => {
      const b = document.createElement("button"); b.className = "mini" + (pri ? " pri" : ""); b.textContent = label;
      b.onclick = async () => {
        const r = await fetch(`/api/users/${u.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
        if (!r.ok){ alert((await r.json().catch(() => ({}))).detail || "Lỗi"); return; }
        loadUsers();
      };
      return b;
    };
    if (u.status === "pending") act.appendChild(mk("Duyệt", { status: "active" }, true));
    if (u.status === "active" && u.id !== (ME && ME.id)) act.appendChild(mk("Khoá", { status: "blocked" }));
    if (u.status === "blocked") act.appendChild(mk("Mở khoá", { status: "active" }, true));
    if (u.role === "member") act.appendChild(mk("→ Admin", { role: "admin" }));
    else if (u.id !== (ME && ME.id)) act.appendChild(mk("→ Thành viên", { role: "member" }));
    row.append(main, act); box.appendChild(row);
  }
  if (!users.length) box.innerHTML = '<small style="color:var(--faint)">Chưa có user.</small>';
}

// ── API token cho LLM/MCP cá nhân ─────────────────────────────────────────
async function loadTokens(){
  const box = document.getElementById("tokenList");
  let toks = [];
  try{ toks = await (await fetch("/api/auth/tokens")).json(); }catch{ return; }
  box.innerHTML = "";
  for (const t of toks){
    const row = document.createElement("div"); row.className = "set-row";
    row.innerHTML = `<div class="set-ico">${ico("key")}</div><div class="set-main"><b></b><small></small></div>`;
    row.querySelector("b").textContent = t.label || "(không tên)";
    row.querySelector("small").textContent = "Tạo " + new Date(t.created_at).toLocaleDateString("vi-VN");
    const del = document.createElement("button"); del.className = "mini"; del.textContent = "Xoá";
    del.onclick = async () => { if (!confirm("Xoá token này? LLM đang dùng sẽ mất quyền.")) return; await fetch(`/api/auth/tokens/${t.id}`, { method: "DELETE" }); loadTokens(); };
    row.appendChild(del); box.appendChild(row);
  }
  if (!toks.length) box.innerHTML = '<small style="color:var(--faint)">Chưa có token. Tạo một token rồi dán vào cấu hình MCP của LLM bạn dùng.</small>';
}
function wireEnterpriseSettings(){
  document.getElementById("tokenCreate").onclick = async () => {
    const label = prompt("Tên gợi nhớ cho token (vd: Claude trên laptop):", "") ;
    if (label === null) return;
    const r = await fetch("/api/auth/tokens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label }) });
    if (!r.ok) return;
    const t = await r.json();
    const out = document.getElementById("tokenOnce");
    out.classList.remove("hidden");
    out.querySelector("code").textContent = t.token;
    out.querySelector("small").textContent = `Token chỉ hiện MỘT lần — sao chép ngay. MCP: MANJU_URL=${location.origin} MANJU_TOKEN=<token>`;
    loadTokens();
  };
  document.getElementById("tokenCopy").onclick = () => { navigator.clipboard.writeText(document.querySelector("#tokenOnce code").textContent).catch(() => {}); };
  document.getElementById("entLogout").onclick = logout;
}

// ── Live direct: mic → Soniox (temp key) + OPFS; text → server ─────────────
async function startLiveDirect(meta){
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e){
    setPill("MIC BỊ CHẶN", false); setWave("off");
    recHint.textContent = e.name === "NotAllowedError" ? "Bạn chưa cho phép dùng micro (kiểm tra quyền trình duyệt)." : e.message;
    return;
  }
  const ctx = new AudioContext();
  await ctx.audioWorklet.addModule("/static/pcm-worklet.js");
  const node = new AudioWorkletNode(ctx, "pcm-worklet");
  ctx.createMediaStreamSource(stream).connect(node);

  const useOpfs = HAS_OPFS;   // enterprise: luôn giữ audio trên thiết bị
  const startMsg = { type: "start", mode: "direct", language: $("lang").value, correct: $("correct").checked, title: (meta && meta.title) || "" };
  const conn = new LiveConn(startMsg, handleLiveMsg, (state) => {
    if (!live) return;
    if (state === "reconnecting"){ setPill("MẤT MẠNG — ĐANG NỐI LẠI…", false); return; }
    if (state === "resumed"){ setPill("RECORDING", true); replayPendingUtts(); return; }
    if (state === "dead") cleanupLive();
  });
  const soniox = new SonioxDirect({
    fetchSession: async () => {
      const r = await fetch("/api/cloud/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ language: $("lang").value, glossary: $("prompt").value }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Không lấy được phiên Soniox");
      return r.json();
    },
    onPartial: (text, utt) => handleLiveMsg({ type: "partial", utt, text }),
    onFinal: (u) => {
      handleLiveMsg({ type: "final", utt: u.utt, text: u.text });
      if (!live) return;
      live.pendingUtts.set(u.utt, u);
      live.conn.sendJSON({ type: "utt", ...u });
    },
    onError: (message) => {
      if (!live) return;
      setPill("LỖI SONIOX", false); setWave("off");
      recHint.textContent = message + " — phần đã ghi vẫn lưu trên máy (OPFS); bấm Stop để lưu text đã nhận.";
    },
    onFinished: () => { if (live && live.onSonioxDone) live.onSonioxDone(); },
  });
  live = { conn, ctx, stream, soniox, opfs: useOpfs ? startOpfsWriter() : null, pendingUtts: new Map(),
           stopping: false, paused: false, base: 0, resumeAt: Date.now(), requestedMode: "direct",
           timer: setInterval(tickTimer, 300) };
  subs.innerHTML = ""; recTimer.textContent = "00:00:00";
  $("modeBadge").innerHTML = ico("cloud") + " Soniox"; $("modeBadge").classList.remove("hidden");
  setPill("KẾT NỐI…", true); setWave("on");
  $("recPause").innerHTML = ico("pause") + " Pause";
  recHint.textContent = "Audio lưu trên thiết bị này — server công ty chỉ nhận văn bản.";
  acquireWakeLock();
  try{ await soniox.start(); }
  catch(e){ setPill("LỖI", false); recHint.textContent = e.message; return; }
  node.port.onmessage = (ev) => {
    if (!live || live.paused) return;
    live.soniox.feed(ev.data);
    if (live.opfs) live.opfs.write(ev.data);
  };
}
function replayPendingUtts(){
  if (!live) return;
  for (const u of live.pendingUtts.values()) live.conn.sendJSON({ type: "utt", ...u });
}
// Stop: chờ Soniox chốt nốt token (tối đa 6s) → gửi hết utt còn thiếu → "stop" cho server.
function stopLiveDirect(){
  if (!live || live.stopping) return;
  live.stopping = true;
  stopAudio();
  const { conn, opfs, soniox, pendingUtts } = live;
  let done = false;
  const finishAll = () => {
    if (done) return; done = true;
    for (const u of pendingUtts.values()) conn.sendJSON({ type: "utt", ...u });
    bgSaveStart(conn, opfs);
  };
  live.onSonioxDone = finishAll;
  soniox.stop();
  setTimeout(finishAll, 6000);
  endLive({ keepConn: true });
  showScreen("home");
}

document.addEventListener("DOMContentLoaded", () => { wireAuthGate(); wireEnterpriseSettings(); });
