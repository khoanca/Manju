#!/usr/bin/env python3
"""Benchmark chốt provider cloud STT cho FR-10 (docs/plan-cloud-stt.md).

So mlx-whisper (baseline production local) với các cloud STT giỏi code-switch
Việt–Anh trên cùng bản ghi để CHỐT provider cho tier cloud. Công cụ đo chạy
tay: đọc key từ .env, dùng lại `app.accuracy` (WER/CER) và bản ghi trong SQLite.

Engines cloud:
  soniox-rt  : ĐƯỜNG LIVE THẬT — chạy đúng class production
               `app.cloud_stt.SonioxLive` (WS stt-rt-v5), feed PCM16 mono 16k
               từng chunk 3200B như live.py. Số này mới là số của đường live.
  soniox     : async production — `app.cloud_stt.transcribe_file_async`
               (stt-async-v5, tự xóa file/transcription phía Soniox khi xong).
  gladia     : BATCH pre-recorded solaria-1 (code_switching vi+en).
  deepgram   : BATCH pre-recorded nova-3 — cận trên chất lượng, KHÔNG streaming.
  assemblyai : BATCH async universal-3-5-pro/universal-2 (language_code=vi) —
               cũng là cận trên chất lượng, KHÔNG streaming.

Key cần trong .env (leg thiếu key báo lỗi riêng, không chặn leg khác):
  SONIOX_API_KEY, DEEPGRAM_API_KEY, ASSEMBLYAI_API_KEY, GLADIA_API_KEY

Bản chuẩn (ref) để tính WER:
  --ref golden : edited_text của bản đã bật cờ golden (đáng tin nhất)
  --ref edited : edited_text nếu có (không đòi golden)
  --ref final  : text sau pass-2 — TẠM & LỆCH: ref này sinh từ chính Whisper nên
                 WER sẽ thiên vị mlx. Đọc số theo hướng "cloud khác baseline bao
                 nhiêu", không phải "cloud sai bao nhiêu".

Baseline mlx mặc định lấy `raw_text` đã lưu (output mlx-whisper thô); --redecode
để decode lại audio gốc (chậm, chiếm _decode_lock — chạy khi không họp).

Chạy:
  uv run python scripts/bench_cloud_stt.py --id 42 \
      --engines mlx,soniox-rt,soniox,deepgram,assemblyai,gladia --ref golden
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

from app import accuracy, cloud_stt, db  # noqa: E402

# Qwen3-ASR (open-weights, Apache 2.0) chạy local qua MLX trên Apple Silicon —
# đối trọng open-source với cloud, không rời local-first. Cài: uv sync --group bench.
QWEN3_MODELS = {"qwen3": "Qwen/Qwen3-ASR-0.6B", "qwen3-large": "Qwen/Qwen3-ASR-1.7B"}

# Whisper MLX: turbo (đang dùng, LIVE_MLX_MODEL mặc định) vs full large-v3 (chậm
# hơn nhưng thường chính xác hơn). Decode cả file để so turbo↔full công bằng.
WHISPER_MODELS = {
    "whisper-turbo": "mlx-community/whisper-large-v3-turbo",
    "whisper-v3": "mlx-community/whisper-large-v3-mlx",
}

GLADIA_BASE = "https://api.gladia.io/v2"
DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
AAI_BASE = "https://api.assemblyai.com/v2"
LANGS = ("vi", "en")  # code-switch Việt–Anh
RT_CHUNK_BYTES = 3200  # 100ms PCM16 mono 16k — cỡ chunk client live gửi
POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 300.0

# Token thuần ASCII (không dấu tiếng Việt) ≈ thuật ngữ tiếng Anh/kỹ thuật. Không
# hoàn hảo (vài từ Việt viết không dấu lọt vào) nhưng đủ để soi engine giữ được
# bao nhiêu thuật ngữ Anh — đúng phần Whisper hay phiên âm sai.
_ASCII_TERM_RE = re.compile(r"^[a-z][a-z0-9]*$")


@dataclass
class EngineResult:
    name: str
    text: str = ""
    wer: float | None = None
    cer: float | None = None
    term_hits: list[str] = field(default_factory=list)   # thuật ngữ Anh của ref mà engine bắt đúng
    term_missed: list[str] = field(default_factory=list)  # thuật ngữ Anh của ref bị engine bỏ/sai
    error: str = ""


def _audio_path(row: dict) -> Path | None:
    # read_transcript() không trả audio_file/audio_dir (chỉ cờ `audio`), nên
    # dùng helper của db tự phân giải audio_dir + kiểm tra tồn tại.
    path = db.transcript_audio_path(row["id"])
    return path if path and path.exists() else None


def _ascii_terms(text: str) -> list[str]:
    """Danh sách thuật ngữ ASCII (giữ thứ tự, bỏ trùng) — proxy cho từ tiếng Anh."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in accuracy.normalize(text):
        if len(tok) >= 2 and _ASCII_TERM_RE.match(tok) and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def run_mlx(row: dict, redecode: bool) -> str:
    """Baseline: raw_text đã lưu (mlx-whisper thô) hoặc decode lại audio gốc."""
    if not redecode and row.get("raw_text"):
        return row["raw_text"]
    path = _audio_path(row)
    if path is None:
        raise FileNotFoundError("thiếu raw_text và audio gốc — không có baseline mlx")
    from app import engines

    eng = engines.get_engine()
    spec = engines.DecodeSpec(row.get("language") or "vi", "", None)
    return eng.transcribe_file(path, spec, lambda *_: None).text


def run_whisper(path: Path, repo: str, language: str) -> str:
    """mlx-whisper decode cả file — cùng cách gọi như MlxEngine (engines.py)."""
    import mlx_whisper  # lazy: đã có trong deps runtime

    return mlx_whisper.transcribe(
        str(path), path_or_hf_repo=repo, language=language,
        condition_on_previous_text=False,
    )["text"]


def run_qwen3(path: Path, repo: str) -> str:
    """Qwen3-ASR qua MLX. language=None để tự nhận diện → cho code-switch Việt–Anh
    phát huy (ép 1 ngôn ngữ sẽ chèn phần còn lại). context để trống cho công bằng."""
    from mlx_qwen3_asr import transcribe  # lazy: chỉ cần khi chạy engine này

    return transcribe(str(path), model=repo).text


def _poll(client: httpx.Client, url: str, headers: dict, done: str, err: str, pick) -> str:
    """Poll một job cloud tới khi status == done; pick(data) → text cuối."""
    deadline = time.time() + POLL_TIMEOUT_S
    while time.time() < deadline:
        r = client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status == done:
            return pick(data)
        if status == err:
            raise RuntimeError(f"job lỗi: {data}")
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"quá {POLL_TIMEOUT_S:.0f}s chưa xong: {url}")


def run_gladia(path: Path, key: str) -> str:
    """Upload → tạo job solaria-1 (code_switching) → poll → full_transcript."""
    headers = {"x-gladia-key": key}
    with httpx.Client(timeout=60.0) as c:
        with path.open("rb") as f:
            up = c.post(f"{GLADIA_BASE}/upload", headers=headers,
                        files={"audio": (path.name, f, "audio/wav")})
        up.raise_for_status()
        init = c.post(
            f"{GLADIA_BASE}/pre-recorded", headers=headers,
            json={"audio_url": up.json()["audio_url"],
                  "language_config": {"languages": list(LANGS), "code_switching": True}},
        )
        init.raise_for_status()
        return _poll(c, init.json()["result_url"], headers, "done", "error",
                     lambda d: d["result"]["transcription"]["full_transcript"])


def _wav_pcm16_mono_16k(path: Path) -> bytes:
    """PCM16 mono 16k cho đường realtime. WAV đúng format đọc thẳng bằng `wave`;
    format khác → resample qua ffmpeg (pattern app/denoise._load_audio)."""
    try:
        with wave.open(str(path), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (
                1, 2, cloud_stt.SAMPLE_RATE,
            ):
                return w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        pass  # không phải WAV chuẩn — để ffmpeg decode
    cmd = [
        "ffmpeg", "-nostdin", "-threads", "0", "-i", str(path),
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le",
        "-ar", str(cloud_stt.SAMPLE_RATE), "-",
    ]
    try:
        return subprocess.run(cmd, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"audio không phải WAV PCM16 mono 16k và resample ffmpeg thất bại: {exc}"
        ) from exc


def run_soniox_rt(path: Path, _key: str) -> str:
    """ĐƯỜNG LIVE THẬT: class production `app.cloud_stt.SonioxLive` (WS stt-rt-v5),
    connect mặc định (SonioxLive tự đọc SONIOX_API_KEY). Feed PCM từng chunk
    RT_CHUNK_BYTES rồi stop(); text gom từ on_final. Các leg khác là batch —
    chỉ leg này đo đúng chất lượng đường live."""
    pcm = _wav_pcm16_mono_16k(path)
    finals: list[str] = []
    errors: list[str] = []
    spec = cloud_stt.LiveCloudSpec(
        language="vi",
        context=None,
        on_partial=lambda _text, _start: None,
        on_final=lambda text, _start, _speaker: finals.append(text),
        on_error=errors.append,
    )
    live = cloud_stt.SonioxLive(spec)
    live.start()
    for i in range(0, len(pcm), RT_CHUNK_BYTES):
        live.feed(pcm[i:i + RT_CHUNK_BYTES])
    live.stop(timeout=POLL_TIMEOUT_S)
    if errors:
        raise RuntimeError(f"Soniox realtime lỗi: {errors[0]}")
    if not finals:
        raise RuntimeError("Soniox realtime không trả final nào (timeout/audio rỗng?)")
    return " ".join(finals)


def run_soniox_async(path: Path, _key: str) -> str:
    """Async production: `app.cloud_stt.transcribe_file_async` (stt-async-v5) —
    upload → poll → text, LUÔN xóa file/transcription phía Soniox khi xong."""
    spec = cloud_stt.AsyncJobSpec(language="vi", timeout_s=POLL_TIMEOUT_S)
    return cloud_stt.transcribe_file_async(path, spec)


def run_deepgram(path: Path, key: str) -> str:
    """BATCH pre-recorded nova-3 — cận trên chất lượng Deepgram, KHÔNG phải
    streaming (đường live Deepgram là WS riêng, không đo ở đây)."""
    with httpx.Client(timeout=POLL_TIMEOUT_S) as c:
        r = c.post(
            DEEPGRAM_URL,
            params={"model": "nova-3", "language": "vi", "smart_format": "true"},
            headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"},
            content=path.read_bytes(),
        )
        r.raise_for_status()
        return str(r.json()["results"]["channels"][0]["alternatives"][0]["transcript"])


def run_assemblyai(path: Path, key: str) -> str:
    """BATCH async AssemblyAI — cận trên chất lượng, KHÔNG phải streaming.

    Tiếng Việt: language_code="vi"; `speech_model` (số ít) đã deprecated → dùng
    speech_models=["universal-3-5-pro", "universal-2"], cả hai hỗ trợ vi và
    route model đứng trước (docs/speech-to-text/pre-recorded-audio/supported-languages).
    """
    headers = {"authorization": key}
    with httpx.Client(timeout=60.0) as c:
        up = c.post(f"{AAI_BASE}/upload", headers=headers, content=path.read_bytes())
        up.raise_for_status()
        job = c.post(
            f"{AAI_BASE}/transcript", headers=headers,
            json={"audio_url": up.json()["upload_url"], "language_code": "vi",
                  "speech_models": ["universal-3-5-pro", "universal-2"]},
        )
        job.raise_for_status()
        return _poll(c, f"{AAI_BASE}/transcript/{job.json()['id']}", headers,
                     "completed", "error", lambda d: str(d.get("text") or ""))


CLOUD_RUNNERS = {
    "gladia": (run_gladia, "GLADIA_API_KEY"),
    "soniox": (run_soniox_async, "SONIOX_API_KEY"),
    "soniox-rt": (run_soniox_rt, "SONIOX_API_KEY"),
    "deepgram": (run_deepgram, "DEEPGRAM_API_KEY"),
    "assemblyai": (run_assemblyai, "ASSEMBLYAI_API_KEY"),
}


def evaluate(name: str, text: str, ref: str | None, ref_terms: set[str]) -> EngineResult:
    res = EngineResult(name=name, text=text)
    hyp_terms = set(_ascii_terms(text))
    if ref_terms:
        res.term_hits = sorted(ref_terms & hyp_terms)
        res.term_missed = sorted(ref_terms - hyp_terms)
    if ref:
        res.wer = accuracy.wer(ref, text)
        res.cer = accuracy.cer(ref, text)
    return res


def run_engines(row: dict, names: list[str], ref: str | None, redecode: bool) -> list[EngineResult]:
    ref_terms = set(_ascii_terms(ref)) if ref else set()
    path = _audio_path(row)
    results: list[EngineResult] = []
    for name in names:
        try:
            if name == "mlx":
                text = run_mlx(row, redecode)
            elif name in WHISPER_MODELS:
                if path is None:
                    results.append(EngineResult(name=name, error="thiếu audio gốc để decode"))
                    continue
                print(f"  … chạy {name} ({WHISPER_MODELS[name]}, MLX local)…", file=sys.stderr)
                text = run_whisper(path, WHISPER_MODELS[name], row.get("language") or "vi")
            elif name in QWEN3_MODELS:
                if path is None:
                    results.append(EngineResult(name=name, error="thiếu audio gốc để decode"))
                    continue
                print(f"  … chạy {name} ({QWEN3_MODELS[name]}, MLX local)…", file=sys.stderr)
                text = run_qwen3(path, QWEN3_MODELS[name])
            elif name in CLOUD_RUNNERS:
                runner, env_key = CLOUD_RUNNERS[name]
                key = os.environ.get(env_key)
                if not key:
                    results.append(EngineResult(name=name, error=f"thiếu {env_key} trong .env"))
                    continue
                if path is None:
                    results.append(EngineResult(name=name, error="thiếu audio gốc để gửi cloud"))
                    continue
                print(f"  … chạy {name} (upload + chờ job)…", file=sys.stderr)
                text = runner(path, key)
            else:
                results.append(EngineResult(name=name, error="engine không nhận dạng được"))
                continue
            results.append(evaluate(name, text, ref, ref_terms))
        except Exception as exc:  # noqa: BLE001 — 1 engine hỏng không chặn engine khác
            results.append(EngineResult(name=name, error=f"{type(exc).__name__}: {exc}"))
    return results


def reference_text(row: dict, source: str) -> str | None:
    if source in ("golden", "edited"):
        return row.get("edited_text")
    return row.get("text")


def print_report(row: dict, source: str, ref: str | None, results: list[EngineResult]) -> None:
    n_terms = len(set(_ascii_terms(ref))) if ref else 0
    ref_note = f"ref={source}, {len(accuracy.normalize(ref))} từ, {n_terms} thuật ngữ Anh" if ref \
        else f"ref={source}: KHÔNG có → chỉ so thuật ngữ, không WER"
    print(f"\n─ Bản ghi {row['id']} — {row.get('title', '')}   ({ref_note})")
    if source == "final" and ref:
        print("  ⚠ ref=final sinh từ Whisper → WER thiên vị mlx; đọc là 'lệch baseline', không phải 'sai'.")
    print(f"\n  {'engine':<8} {'WER':>8} {'CER':>8} {'thuật ngữ Anh':>16}")
    for r in results:
        if r.error:
            print(f"  {r.name:<8} {'—':>8} {'—':>8}   ({r.error})")
            continue
        wer = f"{r.wer:.3f}" if r.wer is not None else "—"
        cer = f"{r.cer:.3f}" if r.cer is not None else "—"
        term = f"{len(r.term_hits)}/{len(r.term_hits) + len(r.term_missed)}" if r.term_hits or r.term_missed else "—"
        print(f"  {r.name:<8} {wer:>8} {cer:>8} {term:>16}")
    for r in results:
        if r.term_missed:
            print(f"    {r.name} bỏ/sai thuật ngữ Anh: {', '.join(r.term_missed)}")
    print("\n  (WER thấp = gần ref hơn; thuật ngữ Anh cao = giữ code-switch tốt hơn)")


def main() -> int:
    ap = argparse.ArgumentParser(description="So mlx-whisper vs cloud STT trên bản ghi Việt–Anh")
    ap.add_argument("--id", required=True, help="id bản ghi trong SQLite")
    ap.add_argument("--engines", default="mlx,qwen3",
                    help="engine phẩy ngăn cách: mlx, qwen3, qwen3-large, whisper-turbo, "
                         "whisper-v3 (local); soniox-rt (live thật), soniox (async), "
                         "deepgram, assemblyai, gladia (batch) — cloud cần key")
    ap.add_argument("--ref", choices=("golden", "edited", "final"), default="edited",
                    help="nguồn bản chuẩn để tính WER (mặc định edited)")
    ap.add_argument("--redecode", action="store_true",
                    help="decode lại audio bằng mlx thay vì dùng raw_text đã lưu")
    args = ap.parse_args()

    db.init()
    row = db.read_transcript(args.id)
    if row is None:
        print(f"Không thấy bản ghi id={args.id}.", file=sys.stderr)
        return 1
    if args.ref == "golden" and not row.get("golden"):
        print(f"Bản ghi {args.id} chưa bật cờ golden. Dùng --ref edited/final, "
              "hoặc mở bản ghi → 'Dùng làm chuẩn đo'.", file=sys.stderr)
        return 1

    ref = reference_text(row, args.ref)
    if ref is None:
        print(f"Không có bản chuẩn (--ref {args.ref}) — sẽ chỉ so thuật ngữ, không có WER.",
              file=sys.stderr)

    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    results = run_engines(row, names, ref, args.redecode)
    print_report(row, args.ref, ref, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
