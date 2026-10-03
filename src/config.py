"""이 프로젝트의 .env만 읽는다. 키 값은 화면이나 로그에 출력하지 않는다."""

from pathlib import Path
from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_gemini_key(path: Path | None = None) -> str:
    env_path = path if path is not None else ENV_PATH
    if not env_path.exists():
        return ""
    try:
        with env_path.open(encoding="utf-8-sig") as stream:
            values = dotenv_values(stream=stream, interpolate=False)
    except FileNotFoundError:
        return ""
    except (OSError, ValueError, UnicodeError):
        raise ValueError(".env 파일을 읽을 수 없습니다. 파일 형식과 읽기 권한을 확인해주세요.") from None

    key = values.get("GEMINI_API_KEY", "")
    if not isinstance(key, str):
        raise ValueError(".env에 GEMINI_API_KEY=키값 형식으로 입력해주세요.")
    key = key.strip()
    if key and (not key.isascii() or any(character.isspace() for character in key)):
        raise ValueError("키에 공백이나 한글이 포함되어 있습니다. 실제 키만 붙여넣어주세요.")
    return key
