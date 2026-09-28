from app.services.industry_service import classify_industry, normalize_industry_code


def test_normalize_industry_code():
    assert normalize_industry_code(" 264 ") == "264"
    assert normalize_industry_code(None) is None


def test_classify_major_industries():
    assert classify_industry("264") == "SEMICONDUCTORS_ELECTRONICS"
    assert classify_industry("211") == "PHARMA_BIOTECH"
    assert classify_industry("641") == "FINANCIALS"
    assert classify_industry("620") == "SOFTWARE_IT"
    assert classify_industry(301) == "AUTOMOBILES"
    assert classify_industry("") == "UNCLASSIFIED"
    assert classify_industry("999") == "OTHER"