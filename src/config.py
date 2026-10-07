"""로컬 .env 또는 명시적으로 활성화한 Cloud Secrets를 읽는다. 키는 출력하지 않는다."""

from pathlib import Path
from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _load_cloud_key():
    import streamlit as st

    try:
        if st.secrets.get("ENABLE_CLOUD_SECRETS", False) is not True:
            return ""
        return st.secrets.get("GEMINI_API_KEY", "")
    except FileNotFoundError:
        return ""


def load_gemini_key(path: Path | None = None) -> str:
    env_path = path if path is not None else ENV_PATH
    try:
        with env_path.open(encoding="utf-8-sig") as stream:
            values = dotenv_values(stream=stream, interpolate=False)
    except FileNotFoundError:
        values = {"GEMINI_API_KEY": _load_cloud_key() if path is None else ""}
    except (OSError, ValueError, UnicodeError):
        raise ValueError(".env 파일을 읽을 수 없습니다. 파일 형식과 읽기 권한을 확인해주세요.") from None

    key = values.get("GEMINI_API_KEY", "")
    if not isinstance(key, str):
        raise ValueError("GEMINI_API_KEY는 문자열로 설정해주세요.")
    key = key.strip()
    if key and (not key.isascii() or any(character.isspace() for character in key)):
        raise ValueError("키에 공백이나 한글이 포함되어 있습니다. 실제 키만 붙여넣어주세요.")
    return key
