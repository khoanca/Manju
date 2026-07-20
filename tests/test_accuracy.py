"""Test bộ đo độ chính xác (US-820/822)."""
from app import accuracy


def test_normalize_gop_hoa_thuong_va_bo_dau_cau():
    assert accuracy.normalize("Triển khai Kubernetes, rồi deploy.") == [
        "triển",
        "khai",
        "kubernetes",
        "rồi",
        "deploy",
    ]


def test_normalize_giu_dau_thanh_va_so():
    # Sai thanh điệu LÀ lỗi phiên âm — không được chuẩn hoá cho biến mất.
    assert accuracy.normalize("để") != accuracy.normalize("de")
    assert accuracy.normalize("quý 4") == ["quý", "4"]


def test_wer_khop_hoan_toan():
    assert accuracy.wer("triển khai Kubernetes", "triển khai Kubernetes") == 0.0


def test_wer_bo_qua_khac_biet_dau_cau_hoa_thuong():
    assert accuracy.wer("Triển khai Kubernetes.", "triển khai kubernetes") == 0.0


def test_wer_dem_dung_tung_loai_loi():
    c = accuracy.edit_counts(["a", "b", "c"], ["a", "x", "c"])
    assert (c.subs, c.dels, c.ins, c.hits) == (1, 0, 0, 2)
    assert accuracy.edit_counts(["a", "b"], ["a"]) == accuracy.EditCounts(hits=1, dels=1)
    assert accuracy.edit_counts(["a"], ["a", "b"]) == accuracy.EditCounts(hits=1, ins=1)


def test_wer_ca_rong():
    assert accuracy.wer("", "") == 0.0
    assert accuracy.wer("", "bịa ra chữ") == 1.0  # ref rỗng mà hyp có chữ = sai hết
    assert accuracy.wer("có nội dung", "") == 1.0


def test_wer_vuot_1_khi_asr_bia_them():
    # Loop hallucination thực địa: ref ngắn, hyp phình ra → rate > 1.0, KHÔNG cắt trần.
    ref = "là em bán"
    hyp = "là em bán " + "để " * 20
    assert accuracy.wer(ref, hyp) > 1.0


def test_cer_nhay_hon_wer_voi_sai_mot_am_tiet():
    ref, hyp = "triển khai", "triển khay"
    assert accuracy.wer(ref, hyp) == 1.0 / 2  # cả từ bị tính sai
    assert accuracy.cer(ref, hyp) < accuracy.wer(ref, hyp)


def test_cross_segment_repeat_bat_chong_lan():
    # Đệm đuôi nuốt sang câu sau → cụm cuối segment A lặp lại ở đầu segment B.
    assert accuracy.cross_segment_repeat("mình đi là đáng chí", "đáng chí dạng tới tên") == 2
    assert accuracy.cross_segment_repeat("hoàn toàn khác", "không liên quan") == 0


def test_cross_segment_repeat_bo_qua_dau_cau():
    assert accuracy.cross_segment_repeat("giải pháp workflow.", "Workflow cho mình") == 1


def test_max_cross_repeat_lay_cap_te_nhat():
    segs = ["một hai ba", "ba bốn năm", "năm sáu bảy tám"]
    assert accuracy.max_cross_repeat(segs) == 1
    assert accuracy.max_cross_repeat(["chỉ một segment"]) == 0
    assert accuracy.max_cross_repeat([]) == 0
