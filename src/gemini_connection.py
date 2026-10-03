"""Gemini API 키의 모델 목록 조회 권한을 확인한다. VOC는 전송하지 않는다."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"


def check_gemini_connection(api_key: str) -> None:
    if not api_key:
        raise ValueError("설정 파일에 Gemini API 키를 먼저 입력해주세요.")
    try:
        request = Request(MODELS_URL, headers={"x-goog-api-key": api_key})
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise ValueError("Gemini에서 모델 목록을 확인하지 못했습니다. 잠시 후 다시 시도해주세요.")
    except HTTPError as exc:
        if exc.code in (400, 401):
            message = "키 인증에 실패했습니다. 키가 정확하고 유효한지 Google AI Studio에서 확인해주세요."
        elif exc.code == 403:
            message = "이 키로 접근할 수 없습니다. Gemini API 권한과 키의 사용 제한을 확인해주세요."
        elif exc.code == 429:
            message = "API 요청 한도에 도달했습니다. 잠시 후 다시 시도하거나 사용량을 확인해주세요."
        else:
            message = "Gemini 연결에 실패했습니다. 잠시 후 다시 시도해주세요."
        raise ValueError(message) from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Gemini에 연결하지 못했습니다. 인터넷 연결을 확인하고 다시 시도해주세요.") from None
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("Gemini 응답을 읽을 수 없습니다. 잠시 후 다시 시도해주세요.") from None
