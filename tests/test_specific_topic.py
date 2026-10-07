import pytest
from src.models import Code, CodingResult, Issue, validate_specific_topic

@pytest.mark.parametrize('text',['할인해서 산거라 걍 씁니다 정가였으면 좀 빡쳤을듯.','보통이에요 가격만큼인듯요','세일할때 10만원대로 샀는데 이정도면 만족'])
def test_explicit_price_cannot_be_general_satisfaction(text):
    codes=[Code(id='G',category='일반 평가',name='전반적 만족도',definition='종합 평가',reason='검사')]
    result=CodingResult(voc_id='V1',response_type='opinions',issues=[Issue(code_id='G',sentiment='긍정',evidence_text=text)])
    with pytest.raises(ValueError,match='가격'):validate_specific_topic(result,codes)
    result.issues[0].code_id=None
    result.issues[0].missing_code='가격 및 가성비'
    validate_specific_topic(result,codes)

def test_unspecified_satisfaction_and_specific_price_are_allowed():
    codes=[Code(id='G',category='일반 평가',name='전반적 만족도',definition='종합 평가',reason='검사'),Code(id='P',category='가격',name='가격 및 가성비',definition='가격 평가',reason='검사')]
    for code,text in [('G','좋아요 잘쓸게요'),('P','할인해서 산거라 걍 씁니다')]:
        validate_specific_topic(CodingResult(voc_id='V1',response_type='opinions',issues=[Issue(code_id=code,sentiment='중립',evidence_text=text)]),codes)
