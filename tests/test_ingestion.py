from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src.ingestion import (
    excel_sheet_names, prepare_preview, read_csv, read_excel, read_pasted_text,
)

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_blanks_and_duplicates_preserved_and_original_not_changed():
    frame = pd.DataFrame({"VOC": [" 배송 지연 ", "", None, "배송 지연"]})
    result = prepare_preview(frame, "VOC")
    assert result.input_count == 4
    assert result.blank_count == 2
    assert result.duplicate_count == 1
    assert result.records["입력 행"].tolist() == [1, 2, 3, 4]
    assert result.records["VOC 원문"].tolist() == [" 배송 지연 ", "", "", "배송 지연"]


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp949"])
def test_csv_preserves_korean_and_literal_na(encoding):
    frame = read_csv('VOC,별점\n"배송은 늦었지만, 만족해요",3\nNA,2'.encode(encoding))
    assert frame["VOC"].tolist() == ["배송은 늦었지만, 만족해요", "NA"]


def test_excel_sheet_selection_preserves_text():
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"설명": ["다른 시트"]}).to_excel(writer, sheet_name="설명", index=False)
        pd.DataFrame({"VOC": ["좋아요", "NA"]}).to_excel(writer, sheet_name="리뷰", index=False)
    content = buffer.getvalue()
    assert excel_sheet_names(content) == ["설명", "리뷰"]
    assert read_excel(content, "리뷰")["VOC"].tolist() == ["좋아요", "NA"]


def test_empty_and_oversized_inputs_report_errors():
    with pytest.raises(ValueError, match="확인할 VOC가 없습니다"):
        prepare_preview(read_pasted_text(" \n\n"), "VOC")
    with pytest.raises(ValueError, match="500건"):
        prepare_preview(pd.DataFrame({"VOC": ["좋아요"] * 501}), "VOC")
    with pytest.raises(ValueError, match="5,000자"):
        prepare_preview(pd.DataFrame({"VOC": ["가" * 5001]}), "VOC")
    with pytest.raises(ValueError, match="10MB"):
        read_csv(b"a" * (10 * 1024 * 1024 + 1))


def test_pasted_input_changes_do_not_show_stale_results():
    app = AppTest.from_file(APP_PATH).run()
    assert not app.exception
    assert app.radio(key="input_mode").options == ["직접 붙여넣기", "파일 업로드"]
    assert app.text_area(key="voc_text").value == ""
    app.text_area(key="voc_text").set_value("배송이 늦어요\n\n친절해요").run()
    app.button(key="preview_button").click().run()
    assert any("원본 응답 2건 · 본문 있는 응답 2건 · 빈 본문·무응답 0건" in caption.value for caption in app.caption)
    assert len(app.dataframe) == 1 and not app.metric
    app.text_area(key="voc_text").set_value("배송이 늦어요").run()
    assert not app.metric
    assert not any("원본 응답 2건" in caption.value for caption in app.caption)
    assert not any(button.key == "save_input" for button in app.button)
    app.button(key="preview_button").click().run()
    assert any("원본 응답 1건 · 본문 있는 응답 1건" in caption.value for caption in app.caption)
    assert len(app.dataframe) == 1
    assert not app.exception


def test_old_sample_selection_and_preview_are_cleared():
    app = AppTest.from_file(APP_PATH)
    app.session_state["input_mode"] = "연습용 샘플"
    app.session_state["preview"] = ("old-sample", prepare_preview(pd.DataFrame({"VOC": ["연습용 의견"]}), "VOC"))
    app.run()
    assert not app.exception
    assert app.radio(key="input_mode").value == "직접 붙여넣기"
    assert "preview" not in app.session_state
    assert not any(button.key == "save_input" for button in app.button)


def test_empty_paste_shows_friendly_error():
    app = AppTest.from_file(APP_PATH).run()
    app.radio[0].set_value("직접 붙여넣기").run()
    app.button(key="preview_button").click().run()
    assert "확인할 VOC가 없습니다" in app.error[0].value
    assert not app.exception
