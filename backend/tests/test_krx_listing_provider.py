from app.adapters.krx_listing_provider import KrxListingProvider

def test_krx_listing_provider_singleton():
    provider = KrxListingProvider.get_instance()
    assert provider.is_available is True
    assert provider.as_of_date == "2026-11-20"
    assert provider.source_name == "KRX_KIND"

def test_target_stocks_verification():
    provider = KrxListingProvider.get_instance()

    # 1. 삼성전자 005930 -> LISTED
    samsung = provider.verify_security("005930", company_name="삼성전자")
    assert samsung.status == "LISTED"
    assert samsung.market == "KOSPI"
    assert samsung.listed_at == "1975-06-11"
    assert samsung.verified_by_official is True

    # 2. 한미반도체 042700 -> LISTED
    hanmi = provider.verify_security("042700", company_name="한미반도체")
    assert hanmi.status == "LISTED"
    assert hanmi.market == "KOSPI"
    assert hanmi.listed_at == "2005-07-22"

    # 3. 가비아 079940 -> LISTED
    gabia = provider.verify_security("079940", company_name="가비아")
    assert gabia.status == "LISTED"
    assert gabia.market == "KOSDAQ"
    assert gabia.listed_at == "2005-10-19"

    # 4. 금강철강 053260 -> LISTED
    kgsteel = provider.verify_security("053260", company_name="금강철강")
    assert kgsteel.status == "LISTED"
    assert kgsteel.market == "KOSDAQ"
    assert kgsteel.listed_at == "2002-01-04"

    # 5. 원익피앤이 217820 -> LISTED (피앤이)
    pne = provider.verify_security("217820", company_name="원익피앤이")
    assert pne.status == "LISTED"
    assert pne.market == "KOSDAQ"
    assert pne.listed_at == "2015-12-07"

    # 6. 덕양에너젠 0001A0 -> LISTED (영숫자 6자리)
    dy = provider.verify_security("0001A0", company_name="덕양에너젠")
    assert dy.status == "LISTED"
    assert dy.market == "KOSDAQ"
    assert dy.listed_at == "2026-01-30"

    # 7. 원익피앤이 과거 종목 131390 -> DELISTED
    pne_old = provider.verify_security("131390", company_name="원익피앤이")
    assert pne_old.status == "DELISTED"
    assert pne_old.market == "KOSDAQ"
    assert pne_old.listed_at == "2011-09-27"

    # 8. 엔에스아이 053250 -> DELISTED
    nsi = provider.verify_security("053250", company_name="엔에스아이")
    assert nsi.status == "DELISTED"
    assert nsi.market == "KOSDAQ"
    assert nsi.listed_at == "2001-07-20"

def test_unregistered_fallback_to_unknown():
    provider = KrxListingProvider.get_instance()
    res = provider.verify_security("999999", company_name="미등록테스트기업")
    assert res.status == "UNKNOWN"
    assert res.verified_by_official is False

def test_cache_failure_safe_fallback():
    # Test with non-existent path
    dummy_provider = KrxListingProvider(reference_path="invalid_path.json")
    assert dummy_provider.is_available is False
    res = dummy_provider.verify_security("005930", company_name="삼성전자")
    assert res.status == "UNKNOWN"
    assert res.conflict_reason == "KRX_REFERENCE_UNAVAILABLE"
