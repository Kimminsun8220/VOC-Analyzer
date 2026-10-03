"""입력 데이터를 읽고, 분석에 사용할 VOC를 확인하는 함수들."""

from dataclasses import dataclass
from io import BytesIO

import pandas as pd

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_VOCS = 500
MAX_TEXT_LENGTH = 5_000


@dataclass
class Preview:
    """입력 확인 결과. 원문을 임의로 삭제하거나 잘라내지 않는다."""

    records: pd.DataFrame
    input_count: int
    blank_count: int
    duplicate_count: int


def check_file_size(content: bytes) -> None:
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("파일은 10MB 이하로 올려주세요.")
    if not content:
        raise ValueError("파일이 비어 있습니다. VOC가 들어 있는 파일을 선택해주세요.")


def read_csv(content: bytes) -> pd.DataFrame:
    """한국어 CSV에서 자주 사용하는 UTF-8과 CP949를 지원한다."""
    check_file_size(content)
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return pd.read_csv(
                BytesIO(content), encoding=encoding, keep_default_na=False,
                skip_blank_lines=True, dtype=str,
            )
        except UnicodeDecodeError:
            continue
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            raise ValueError("CSV 형식을 읽을 수 없습니다. 열 제목과 구분자를 확인해주세요.") from exc
    raise ValueError("한글 인코딩을 읽을 수 없습니다. CSV UTF-8 형식으로 저장해주세요.")


def excel_sheet_names(content: bytes) -> list[str]:
    check_file_size(content)
    try:
        with pd.ExcelFile(BytesIO(content), engine="openpyxl") as workbook:
            return workbook.sheet_names
    except Exception as exc:
        raise ValueError("Excel 파일을 읽을 수 없습니다. 암호가 없는 .xlsx 파일을 선택해주세요.") from exc


def read_excel(content: bytes, sheet: str) -> pd.DataFrame:
    check_file_size(content)
    try:
        return pd.read_excel(
            BytesIO(content), sheet_name=sheet, engine="openpyxl",
            keep_default_na=False, dtype=str,
        )
    except Exception as exc:
        raise ValueError("선택한 시트를 읽을 수 없습니다. Excel 파일 내용을 확인해주세요.") from exc


def read_pasted_text(text: str) -> pd.DataFrame:
    """줄바꿈을 기준으로 한 줄을 한 건의 VOC로 읽는다."""
    return pd.DataFrame({"VOC": [line for line in text.splitlines() if line.strip()]})


def prepare_preview(frame: pd.DataFrame, text_column: str) -> Preview:
    if text_column not in frame.columns:
        raise ValueError("VOC 본문이 있는 열을 선택해주세요.")

    original = frame[text_column].fillna("").astype(str)
    normalized = original.str.strip()
    valid = normalized.ne("")
    count = len(frame)
    if count == 0:
        raise ValueError("확인할 VOC가 없습니다. 고객 의견을 한 줄 이상 입력해주세요.")
    if count > MAX_VOCS:
        raise ValueError(f"현재는 한 번에 {MAX_VOCS}건까지 확인할 수 있습니다. 입력을 나누어주세요.")
    if original.str.len().gt(MAX_TEXT_LENGTH).any():
        raise ValueError(f"{MAX_TEXT_LENGTH:,}자를 넘는 VOC가 있습니다. 해당 원문을 확인해주세요.")

    # 행 번호를 유지하므로 같은 문장이 여러 번 있어도 각각 별개 VOC가 된다.
    records = pd.DataFrame({
        "입력 행": range(1, len(frame) + 1),
        "VOC 원문": original.to_numpy(),
    })
    records["VOC ID"] = [f"V{row:04d}" for row in range(1, count + 1)]
    records["빈 본문"] = (~valid).to_numpy()

    return Preview(
        records=records,
        input_count=len(frame),
        blank_count=int((~valid).sum()),
        duplicate_count=int(normalized[valid].duplicated().sum()),
    )
