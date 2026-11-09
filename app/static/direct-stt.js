// FR-11 Enterprise — browser nối THẲNG Soniox realtime bằng temp key (US-903).
// Port của app/cloud_stt.py SonioxLive: gửi config → PCM binary → nhận token,
// gom token final thành utterance (lặng >= GAP_MS, token "<end>", hoặc > MAX_MS),
// keepalive khi mic im. Server Manju không bao giờ thấy audio.
//
// Dùng: const s = new SonioxDirect({ fetchSession, onPartial, onFinal, onError, onFinished });
//   await s.start(); s.feed(arrayBuffer); s.stop();
// fetchSession(): Promise<{api_key, ws_url, config}> — gọi /api/cloud/session.

const SONIOX_GAP_MS = 700;
const SONIOX_MAX_UTT_MS = 28000;
const SONIOX_KEEPALIVE_MS = 15000;

class SonioxDirect {
  constructor(opts){
    this.opts = opts;
    this.ws = null; this.failed = false; this.finished = false; this.stopping = false;
    this.utt = { tokens: [], nonfinal: "", startMs: -1, endMs: -1, speaker: null };
    this.seq = 0;                 // số utterance đã chốt (server dùng làm khoá idempotent)
    this.lastFinalEndMs = 0;
    this.keepalive = null;
    this.pending = [];            // PCM tới trước khi WS mở
  }

  async start(){
    const sess = await this.opts.fetchSession();
    this.config = sess.config;
    await this._open(sess);
  }

  _open(sess){
    return new Promise((resolve, reject) => {
      // Browser không set được header Authorization → key đi qua subprotocol
      // (soniox.com/docs/guides/websocket-authentication). KHÔNG đưa api_key vào start.
      const ws = this.ws = new WebSocket(sess.ws_url, ["soniox-api-key", sess.api_key]);
      ws.binaryType = "arraybuffer";
      let opened = false;
      ws.onopen = () => {
        opened = true;
        ws.send(JSON.stringify(this.config));
        for (const buf of this.pending) ws.send(buf);
        this.pending = [];
        this._armKeepalive();
        resolve();
      };
      ws.onmessage = (ev) => this._onMessage(ev.data);
      ws.onerror = () => { if (!opened) reject(new Error("Không mở được kết nối Soniox")); };
      ws.onclose = () => {
        clearTimeout(this.keepalive);
        if (!this.finished && !this.failed) this._fail("Soniox đóng kết nối");
      };
    });
  }

  _armKeepalive(){
    clearTimeout(this.keepalive);
    this.keepalive = setTimeout(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN && !this.stopping){
        try{ this.ws.send(JSON.stringify({ type: "keepalive" })); }catch{}
      }
      this._armKeepalive();
    }, SONIOX_KEEPALIVE_MS);
  }

  feed(buf){
    if (this.failed || this.stopping) return;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN){ this.pending.push(buf); return; }
    this.ws.send(buf);
    this._armKeepalive();
  }

  // Hết audio: frame rỗng → server chốt nốt token rồi gửi finished.
  stop(){
    this.stopping = true;
    clearTimeout(this.keepalive);
    if (this.ws && this.ws.readyState === WebSocket.OPEN){
      try{ this.ws.send(new ArrayBuffer(0)); }catch{}
    } else { this._finish(); }
  }

  abort(){
    this.failed = true; this.finished = true;
    clearTimeout(this.keepalive);
    try{ this.ws && this.ws.close(); }catch{}
  }

  _onMessage(raw){
    let msg;
    try{ msg = JSON.parse(raw); }catch{ return; }
    if (msg.error_code != null){ this._fail(msg.error_message || msg.error_type || "Soniox lỗi"); return; }
    this._handleTokens(msg.tokens || []);
    if (msg.finished) this._finish();
  }

  _finish(){
    if (this.finished) return;
    this.finished = true;
    this._flush();
    clearTimeout(this.keepalive);
    try{ this.ws && this.ws.close(); }catch{}
    if (this.opts.onFinished) this.opts.onFinished();
  }

  _fail(message){
    if (this.failed || this.finished){ this.finished = true; return; }
    this.failed = true; this.finished = true;
    this._flush();   // token final là text Soniox đã xác nhận — không được mất
    clearTimeout(this.keepalive);
    try{ this.ws && this.ws.close(); }catch{}
    if (this.opts.onError) this.opts.onError(message);
  }

  _handleTokens(tokens){
    let changed = false;
    this.utt.nonfinal = "";
    for (const tok of tokens){
      if (tok.is_final) changed = this._takeFinal(tok) || changed;
      else this.utt.nonfinal += String(tok.text || "");
    }
    if (this.utt.nonfinal) changed = true;
    if (changed && (this.utt.tokens.length || this.utt.nonfinal)) this._emitPartial();
  }

  _takeFinal(tok){
    const text = String(tok.text || "");
    const start = Number(tok.start_ms || 0);
    const end = Number(tok.end_ms || start);
    this.lastFinalEndMs = Math.max(this.lastFinalEndMs, end);
    if (text.trim() === "<end>"){ this._flush(); return false; }
    const u = this.utt;
    const gapBreak = u.endMs >= 0 && start - u.endMs >= SONIOX_GAP_MS;
    const longBreak = u.startMs >= 0 && end - u.startMs >= SONIOX_MAX_UTT_MS;
    if (u.tokens.length && (gapBreak || longBreak)) this._flush();
    const cur = this.utt;
    if (!cur.tokens.length){ cur.startMs = start; cur.speaker = tok.speaker != null ? String(tok.speaker) : null; }
    cur.tokens.push(tok);
    cur.endMs = Math.max(cur.endMs, end);
    return !!text;
  }

  _raw(){ return this.utt.tokens.map(t => String(t.text || "")).join(""); }   // giữ khoảng trắng trong token

  _emitPartial(){
    const text = (this._raw() + this.utt.nonfinal).trim();
    if (!text) return;
    const start = this.utt.startMs >= 0 ? this.utt.startMs : this.lastFinalEndMs;
    try{ this.opts.onPartial(text, this.seq + 1, start); }catch{}
  }

  _flush(){
    const u = this.utt;
    this.utt = { tokens: [], nonfinal: "", startMs: -1, endMs: -1, speaker: null };
    const text = u.tokens.map(t => String(t.text || "")).join("").trim();
    if (!text) return;
    this.seq += 1;
    try{ this.opts.onFinal({ utt: this.seq, text, start_ms: Math.max(u.startMs, 0), end_ms: Math.max(u.endMs, 0), speaker: u.speaker }); }catch{}
  }
}

if (typeof module !== "undefined") module.exports = { SonioxDirect };
