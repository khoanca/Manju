"""T-005 (docs/plan-live-reliability.md): fixture regression 2 chiều cho bộ lọc
anti-loop trong app/engines.py — mọi chuỗi lặp đã lọt ra transcript (history-sweep
2026-07-26 trên data/manju.db, verbatim) phải bị cắt/drop; mọi stutter/nhấn mạnh
thật phải sống nguyên văn. Logic thuần, không load model Whisper."""
from __future__ import annotations

import pytest

from app import engines


def seg(text: str, avg_logprob: float, no_speech_prob: float = 0.0,
        compression_ratio: float = 1.0) -> dict:
    return {
        "text": text,
        "avg_logprob": avg_logprob,
        "no_speech_prob": no_speech_prob,
        "compression_ratio": compression_ratio,
    }


# ── PHẢI CẮT — chuỗi thực địa đã lọt mọi filter cũ ──────────────────────────────


def test_mlx_scored_suspect_collapses_dang_x3():
    # live-2313 (complaint gốc): "đăng"×3 trong segment lp −0.571 là bịa → suspect
    # path (lp < −0.5) hạ min_run period-1 xuống 3. "ôi trời ơi"×2 (chu kỳ p3 ×2)
    # là nói thật → phải giữ nguyên cả 2 lần.
    segments = [seg(
        " Ôi trời ơi, ôi trời ơi Đăng đăng đăng là cái gì không nội",
        avg_logprob=-0.571, no_speech_prob=0.0, compression_ratio=1.045,
    )]

    result = engines._mlx_scored(segments, with_words=False)

    assert result.text.casefold().count("đăng") == 1
    assert result.text.casefold().count("ôi trời ơi") == 2
    assert "là cái gì không nội" in result.text


@pytest.mark.parametrize("loop, unit", [
    # live-2307: chu kỳ p3 ×3 — dưới ngưỡng cũ (≥4), luật mới p≥3 cắt từ ×3.
    ("em môm cài em môm cài em môm cài", "em môm cài"),
    # live-0205: bias echo "hình dung, kubernetes" ×3 đã LƯU vào transcript thật.
    ("hình dung, kubernetes hình dung, kubernetes hình dung, kubernetes",
     "hình dung, kubernetes"),
    # live-1620: chu kỳ p4 ×3.
    ("tươi nào các chón tươi nào các chón tươi nào các chón", "tươi nào các chón"),
])
def test_collapse_loops_cuts_cycle_p3plus_x3(loop, unit):
    # Độc lập logprob — collapse_loops mặc định phải cắt, không cần suspect path.
    assert engines.collapse_loops(loop) == unit


def test_collapse_loops_y_plane_cycle():
    # sim-1523: residue của collapse cũ vẫn là rác "y plane plane"×3 — chu kỳ p3
    # ×3 thu về 1; đuôi "y plane" chưa đủ 1 chu kỳ trọn thì giữ.
    assert (engines.collapse_loops("y plane plane y plane plane y plane plane y plane")
            == "y plane plane y plane")


def test_collapse_loops_p5_cycle():
    # Upload 20260627: "tụi em có thể yêu, "×29 — biên period tối đa (p5).
    assert engines.collapse_loops(" ".join(["tụi em có thể yêu,"] * 29)) == "tụi em có thể yêu,"


def test_collapse_loops_p2_cycle_x4_unchanged_rule():
    # live-1620: p2 giữ ngưỡng ≥4 như cũ ("kết nối,"×4 cắt; ×3 là nói thật — xem
    # test_mlx_scored_suspect_still_keeps_p2_cycle_x3).
    assert engines.collapse_loops("kết nối, kết nối, kết nối, kết nối") == "kết nối,"


def test_collapse_loops_intra_token_whole_segment():
    # live-2138: token 223 ký tự "ņ" dính liền — cả segment chỉ là 1 token nên
    # _is_token_loop (cần ≥3 token) không bắt; _INTRA_LOOP_RE phải thu gọn.
    collapsed = engines.collapse_loops("ņ" * 223)
    assert collapsed != "ņ" * 223
    assert len(collapsed) <= 3


def test_collapse_loops_rescues_real_tail_after_run():
    # Run period-1 giữa câu phải collapse GIỮ từ thật phía sau, không drop cả
    # segment (lý do _is_token_loop không nới sang luật "token áp đảo ≥90%").
    assert engines.collapse_loops("để " * 10 + "bán") == "để bán"


def test_keep_segment_compression_ratio_gate():
    # cr>2.4 (đúng ngưỡng retry nội bộ của Whisper) là rác chắc chắn — đo thực
    # địa: degenerate 4.9–40.6 vs câu sạch ≤1.7.
    text = "và bên mình sẽ triển khai cái hệ thống đó"
    assert engines.keep_segment(text, 0.0, -0.8, compression_ratio=6.95) is False
    assert engines.keep_segment(text, 0.0, -0.8, compression_ratio=1.7) is True
    # Gate là >2.4, không phải ≥ — và default 0.0 giữ tương thích caller cũ.
    assert engines.keep_segment(text, 0.0, -0.8, compression_ratio=2.4) is True
    assert engines.keep_segment(text, 0.0, -0.8) is True


def test_keep_segment_drops_hai_loop_via_cr_gate():
    # sim-1523 tick 43: "Hải, "×74 + token đuôi lệch "H" phá luật whole-segment
    # của _is_token_loop — bắt bằng gate compression_ratio (window cr=25.95).
    text = "Hải, " * 74 + "H"
    assert engines._is_token_loop(text) is False  # chủ đích: không luật dominance
    assert engines.keep_segment(text, 0.0, -0.145, 25.95) is False

    result = engines._mlx_scored([seg(" " + text, -0.145, 0.0, 25.95)], with_words=False)
    assert result.text == ""


def test_cycle_x2_survives_collapse_but_cr_gate_drops():
    # sim-1523: "chủ yếu gì là chủ yếu gì" (×2) không phân biệt được với nói thật
    # → collapse giữ nguyên; thực địa nó chết ở gate cr thượng nguồn (cr=4.86).
    text = "chủ yếu gì là chủ yếu gì"
    assert engines.collapse_loops(text) == text
    assert engines.keep_segment(text, 0.0, -0.866, compression_ratio=4.86) is False


def test_collapse_loops_min_run_lowers_period1_threshold():
    # min_run=3 (suspect path) cắt run ×3 token ngắn giữa câu; mặc định 6 giữ.
    # (×4 trở lên đã bị _INTRA_LOOP_RE bắt từ trước — unit tính cả khoảng trắng.)
    text = "nói NYE NYE NYE xong"
    assert engines.collapse_loops(text) == text
    assert engines.collapse_loops(text, min_run=3) == "nói NYE xong"


# ── PHẢI GIỮ — stutter/nhấn mạnh thật (verbatim từ transcript đã lưu) ──────────

REAL_STUTTERS = [
    "nó nó nó",
    "Vâng, vâng, vâng",
    "thì thì thì",
    "tự tự tự",
    "là là là",
    "cái cái cái",
    "nè nè nè",
    "chuyện gì chuyện gì chuyện gì",  # p2 ×3 — dưới ngưỡng chu kỳ p2 (≥4)
    "bỏ ra bỏ ra bỏ ra",  # p2 ×3
    "không không không được đâu",  # token >4 ký tự — miễn luật period-1
]


@pytest.mark.parametrize("text", REAL_STUTTERS)
def test_collapse_loops_keeps_real_stutters(text):
    assert engines.collapse_loops(text) == text


@pytest.mark.parametrize("stutter", REAL_STUTTERS)
def test_mlx_scored_keeps_real_stutters_good_lp(stutter):
    # Nhúng trong câu (segment CHỈ là 1 token ngắn lặp thì luật whole-segment cũ
    # drop — đúng thiết kế, rác im lặng); lp tốt −0.3 → không vào suspect path.
    result = engines._mlx_scored([seg(f" à {stutter} nha", -0.3)], with_words=False)

    assert result.text == f"à {stutter} nha"


def test_mlx_scored_suspect_still_keeps_p2_cycle_x3():
    # Suspect path CHỈ hạ ngưỡng period-1 — "chuyện gì"×3 (p2) sống dù lp −0.6.
    result = engines._mlx_scored(
        [seg(" chuyện gì chuyện gì chuyện gì", -0.6)], with_words=False
    )

    assert result.text == "chuyện gì chuyện gì chuyện gì"


def test_mlx_scored_p1_stutter_cut_only_when_suspect():
    # Cùng 1 câu: lp tốt giữ nguyên stutter, lp đáng ngờ (<−0.5) cắt run ×3 —
    # đo 2026-07-26: mọi stutter thật đều lp tốt hơn −0.5 → 0 false positive.
    good = engines._mlx_scored([seg(" à nó nó nó nha", -0.3)], with_words=False)
    suspect = engines._mlx_scored([seg(" à nó nó nó nha", -0.6)], with_words=False)

    assert good.text == "à nó nó nó nha"
    assert suspect.text == "à nó nha"


def test_mlx_scored_suspect_is_per_segment():
    # _collapse_suspect chạy TRƯỚC khi nối utterance, theo lp của từng segment.
    segments = [
        seg(" à nó nó nó nha", -0.3),
        seg(" đăng đăng đăng là cái gì", -0.571),
    ]

    result = engines._mlx_scored(segments, with_words=False)

    assert result.text == "à nó nó nó nha đăng là cái gì"


@pytest.mark.parametrize("text", [
    "gần đây gần đây",  # live-2318 — p2 ×2
    "kết thử, kết thử",  # live-0116 — p2 ×2, đầu transcript
    "Ôi trời ơi, ôi trời ơi",  # live-2313 — p3 ×2
    "như thế nào, phòng ht như thế nào, phòng ht",  # live-0020 — p5 ×2
    "Tạm biệt, tạm biệt",  # live-1202 — chào thật lúc kết thúc họp
])
def test_collapse_loops_keeps_x2_cycles(text):
    # Chu kỳ ×2 không phân biệt được với nói thật → collapse giữ; rác ×2 thực
    # thụ phải chết ở gate cr thượng nguồn (xem fixture "chủ yếu gì").
    assert engines.collapse_loops(text) == text


def test_collapse_loops_exempts_long_tokens_period1():
    # live-1613: "element"×4 — token >4 ký tự miễn luật period-1 kể cả min_run
    # thấp. Leak ĐÃ BIẾT, chấp nhận để khỏi cắt nhấn mạnh từ dài thật.
    text = "element element element element"
    assert engines.collapse_loops(text) == text
    assert engines.collapse_loops(text, min_run=3) == text


# ── Legacy A/B (app.reanalyze) — hành vi cũ phải đóng băng, KHÔNG ăn luật mới ──


def test_legacy_collapse_has_no_new_rules():
    for text in (
        "em môm cài em môm cài em môm cài",  # chu kỳ p3 — bản mới cắt
        "y plane plane y plane plane y plane plane y plane",
        "Đăng đăng đăng là cái gì không nội",  # p1 ×3 < 6, không có min_run thấp
        "ņ" * 223,  # intra-token — legacy không có _INTRA_LOOP_RE
    ):
        assert engines.collapse_loops_legacy(text) == text


def test_legacy_collapse_still_cuts_old_period1_runs():
    # Positive control: hành vi CŨ (run ≥6, token ≤4 ký tự) phải còn nguyên vẹn.
    assert engines.collapse_loops_legacy("là em bán " + "để " * 224) == "là em bán để "


def test_legacy_keep_segment_has_no_cr_gate():
    with pytest.raises(TypeError):
        engines.keep_segment_legacy(
            "câu bình thường", 0.0, -0.8, compression_ratio=6.95  # type: ignore[call-arg]
        )


def test_legacy_keep_segment_keeps_hai_loop():
    # Legacy không có cr gate → "Hải, "×74+"H" vẫn lọt (đúng hành vi cũ để so
    # A/B); nếu _is_token_loop bị nới sang dominance thì test này đỏ.
    assert engines.keep_segment_legacy("Hải, " * 74 + "H", 0.0, -0.145) is True
