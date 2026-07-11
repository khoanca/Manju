// AudioWorklet: nhận Float32 ở sample rate gốc (44.1/48kHz), downsample về
// 16kHz mono (nội suy tuyến tính), đóng gói Int16 PCM và postMessage mỗi
// 2048 sample (128ms). Main thread chỉ việc ws.send(buffer).
const TARGET_RATE = 16000;
const CHUNK_SAMPLES = 2048;

class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE; // sampleRate: global của AudioWorklet
    this.readPos = 0; // con trỏ đọc (số thực) trên dòng input liên tục
    this.tail = new Float32Array(0); // phần input chưa tiêu thụ hết
    this.out = new Int16Array(CHUNK_SAMPLES);
    this.outLen = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;

    // Ghép đuôi cũ + block mới thành một dải liên tục.
    const buf = new Float32Array(this.tail.length + ch.length);
    buf.set(this.tail, 0);
    buf.set(ch, this.tail.length);

    let pos = this.readPos;
    while (pos + 1 < buf.length) {
      const i = Math.floor(pos);
      const frac = pos - i;
      const s = buf[i] * (1 - frac) + buf[i + 1] * frac;
      this.out[this.outLen++] = Math.max(-32768, Math.min(32767, Math.round(s * 32767)));
      if (this.outLen === CHUNK_SAMPLES) {
        this.port.postMessage(this.out.buffer, [this.out.buffer]);
        this.out = new Int16Array(CHUNK_SAMPLES);
        this.outLen = 0;
      }
      pos += this.ratio;
    }
    // Giữ lại sample cuối chưa nội suy xong cho block sau.
    const keepFrom = Math.floor(pos);
    this.tail = buf.slice(keepFrom);
    this.readPos = pos - keepFrom;
    return true;
  }
}

registerProcessor("pcm-worklet", PcmWorklet);
