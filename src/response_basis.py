"""비율 계산의 응답 범위. 무응답 제외는 데이터 삭제가 아닌 조회 조건이다."""

ALL_BASIS = "전체 기준"
VALID_BASIS = "유효 기준"
NO_CONTENT = "없음·무응답·모름"


def basis_responses(originals, basis=ALL_BASIS):
    if basis not in (ALL_BASIS, VALID_BASIS):
        raise ValueError("집계 기준을 선택해주세요.")
    if basis == VALID_BASIS:
        return originals.loc[~originals["응답 상태"].eq(NO_CONTENT)].copy()
    return originals
