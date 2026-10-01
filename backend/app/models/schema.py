from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.orm import relationship as orm_relationship

from app.db.session import Base


class Company(Base):
    __tablename__ = "company"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), unique=True, nullable=True, index=True, comment="DART 고유번호")

    name = Column(String(200), nullable=False, index=True, comment="법인명/기업명")
    status = Column(String(20), default="ACTIVE", comment="상태: ACTIVE, DELISTED, MERGED")
    industry_id = Column(String(50), nullable=True, comment="업종 분류 ID")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    securities = orm_relationship("Security", back_populates="company")
    filings = orm_relationship("Filing", back_populates="company")
    financial_facts = orm_relationship("FinancialFact", back_populates="company")
    disclosure_events = orm_relationship("DisclosureEvent", back_populates="company")
    identifier_maps = orm_relationship("IdentifierMap", back_populates="company")


class Security(Base):
    __tablename__ = "security"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("company.id"), nullable=False, index=True)
    market = Column(String(20), nullable=False, comment="KOSPI, KOSDAQ")
    ticker = Column(String(20), nullable=False, index=True, comment="종목코드 (예: 005930)")
    isin = Column(String(20), nullable=True, index=True, comment="ISIN 표준코드")
    security_type = Column(String(20), default="COMMON", comment="COMMON(보통주), PREFERRED(우선주), ETF, ETN, SPAC")
    listed_at = Column(Date, nullable=True)
    delisted_at = Column(Date, nullable=True)
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)

    # Relationships
    company = orm_relationship("Company", back_populates="securities")
    daily_prices = orm_relationship("DailyPrice", back_populates="security")
    identifier_maps = orm_relationship("IdentifierMap", back_populates="security")


class IdentifierMap(Base):
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    __tablename__ = "identifier_map"
    __table_args__ = (
        Index("idx_identifier_source_val", "source", "source_id_value"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("company.id"), nullable=True, index=True)
    security_id = Column(Integer, ForeignKey("security.id"), nullable=True, index=True)
    source = Column(String(50), nullable=False, index=True, comment="DART_CORP_CODE, KRX_TICKER, ISIN, ENG_TICKER, NAVER_CODE")
    source_id_value = Column(String(100), nullable=False, index=True, comment="외부 식별자 값")
    is_primary = Column(Boolean, default=True, comment="대표 식별자 여부")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    company = orm_relationship("Company", back_populates="identifier_maps")
    security = orm_relationship("Security", back_populates="identifier_maps")



class DailyPrice(Base):
    __tablename__ = "daily_price"
    __table_args__ = (
        PrimaryKeyConstraint("security_id", "trade_date"),
        Index("idx_daily_price_date", "trade_date"),
    )

    security_id = Column(Integer, ForeignKey("security.id"), nullable=False)
    trade_date = Column(Date, nullable=False)
    open = Column(Numeric(15, 2), nullable=True)
    high = Column(Numeric(15, 2), nullable=True)
    low = Column(Numeric(15, 2), nullable=True)
    close = Column(Numeric(15, 2), nullable=True)
    volume = Column(BigInteger, nullable=True)
    value = Column(Numeric(20, 2), nullable=True, comment="거래대금")
    adjusted_close = Column(Numeric(15, 2), nullable=True, comment="수정주가")
    raw_hash = Column(String(64), nullable=True, comment="원본 응답 SHA-256 해시 (64자 hex). 재현성 보존용.")

    # Relationships
    security = orm_relationship("Security", back_populates="daily_prices")


class CollectionCheckpoint(Base):
    __tablename__ = 'collection_checkpoint'
    __table_args__ = (PrimaryKeyConstraint('job_name', 'security_id'),)

    job_name = Column(String(50), nullable=False)
    security_id = Column(Integer, ForeignKey('security.id'), nullable=False)
    last_success_date = Column(Date, nullable=True)
    status = Column(String(20), nullable=False, default='PENDING')
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Filing(Base):
    __tablename__ = "filing"

    rcept_no = Column(String(14), primary_key=True, comment="DART 공시 접수번호")
    company_id = Column(Integer, ForeignKey("company.id"), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    filed_at = Column(DateTime, nullable=False, index=True)
    report_code = Column(String(50), nullable=True)
    correction_of = Column(String(14), nullable=True, comment="정정공시 대상 원공시 rcept_no")
    raw_ref = Column(String(500), nullable=True, comment="원문 XML/문서 경로 참조")
    report_name = Column(String(500), nullable=True)
    available_at = Column(DateTime, nullable=False, index=True, default=datetime.utcnow)
    raw_hash = Column(String(64), nullable=True)
    parser_version = Column(String(30), nullable=False, default="dart_v1")

    # Relationships
    company = orm_relationship("Company", back_populates="filings")
    events = orm_relationship("DisclosureEvent", back_populates="filing")
    evidences = orm_relationship("RelationshipEvidence", back_populates="filing")


class FinancialFact(Base):
    __table_args__ = (
        Index("uq_financial_fact_row", "company_id", "filing_id", "fs_div", "row_key", unique=True),
    )
    __tablename__ = "financial_fact"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("company.id"), nullable=False, index=True)
    filing_id = Column(String(14), ForeignKey("filing.rcept_no"), nullable=True, index=True)
    business_year = Column(String(4), nullable=False)
    report_code = Column(String(5), nullable=False)
    fs_div = Column(String(3), nullable=False, comment="CFS or OFS")
    statement_div = Column(String(10), nullable=True)
    account_detail = Column(String(500), nullable=True)
    row_key = Column(String(64), nullable=False)
    period = Column(String(20), nullable=False, comment="보고서 기간 (예: 2023_1Q, 2023_4Q)")
    account_id = Column(String(500), nullable=False, comment="재무 계정 ID")
    account_name = Column(String(200), nullable=False)
    value = Column(Numeric(20, 2), nullable=True)
    unit = Column(String(20), default="KRW")
    consolidated = Column(Boolean, default=True, comment="연결 여부 (True: 연결, False: 별도)")
    filed_at = Column(DateTime, nullable=False, comment="공시/공개시점")

    # Relationships
    company = orm_relationship("Company", back_populates="financial_facts")


class DartSyncState(Base):
    __tablename__ = "dart_sync_state"

    company_id = Column(Integer, ForeignKey("company.id"), primary_key=True)
    last_filing_date = Column(Date, nullable=True)
    status = Column(String(20), nullable=False, default="PENDING")
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DisclosureEvent(Base):
    __tablename__ = "disclosure_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filing_id = Column(String(14), ForeignKey("filing.rcept_no"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("company.id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, comment="SUPPLY_CONTRACT, INVESTMENT, CAPITAL_INCREASE, CB_BW, etc.")
    amount = Column(Numeric(20, 2), nullable=True, comment="계약/투자 금액")
    ratio = Column(Float, nullable=True, comment="매출/자본 대비 비율")
    sentiment = Column(String(20), default="NEUTRAL", comment="POSITIVE, NEGATIVE, NEUTRAL")
    rule_version = Column(String(20), default="1.0")
    event_date = Column(DateTime, nullable=True)
    financial_basis_filed_at = Column(DateTime, nullable=True)
    raw_data = Column(JSON, nullable=True)

    # Relationships
    filing = orm_relationship("Filing", back_populates="events")
    company = orm_relationship("Company", back_populates="disclosure_events")


class MacroSeries(Base):
    __tablename__ = "macro_series"

    series_id = Column(String(50), primary_key=True)
    title = Column(String(500), nullable=False)
    frequency = Column(String(50), nullable=False)
    frequency_short = Column(String(10), nullable=False)
    units = Column(String(200), nullable=False)
    units_short = Column(String(50), nullable=False)
    seasonal_adjustment = Column(String(100), nullable=False)
    seasonal_adjustment_short = Column(String(20), nullable=False)
    last_updated = Column(DateTime, nullable=True)
    metadata_hash = Column(String(64), nullable=False)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MacroObservation(Base):
    __tablename__ = "macro_observation"
    __table_args__ = (
        PrimaryKeyConstraint("series_id", "observation_date", "vintage_date"),
        Index("idx_macro_obs_date", "observation_date"),
    )

    series_id = Column(String(50), nullable=False, comment="FRED 시리즈 ID (FEDFUNDS, CPIAUCSL, etc.)")
    observation_date = Column(Date, nullable=False, comment="관측 기준일")
    vintage_date = Column(Date, nullable=False, comment="수정 빈티지 일자")
    available_at = Column(DateTime, nullable=False, comment="실제 공개 시각 (룩어헤드 방지용)")
    value = Column(Numeric(15, 4), nullable=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_initial_release = Column(Boolean, nullable=False, default=False)


class Relationship(Base):
    __tablename__ = "relationship"
    __table_args__ = (
        Index("uq_relationship_edge", "source_id", "target_id", "type", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey("company.id"), nullable=False, index=True, comment="공급사/주체 기업 ID")
    target_id = Column(Integer, ForeignKey("company.id"), nullable=False, index=True, comment="고객사/대상 기업 ID")
    type = Column(String(50), nullable=False, comment="SUPPLIES_TO, CUSTOMER_OF, PARTNERS_WITH, COMPETES_WITH, OWNS, BELONGS_TO")
    status = Column(String(20), default="proposed", index=True, comment="proposed, verified, rejected, expired")
    valid_from = Column(Date, nullable=True)
    valid_to = Column(Date, nullable=True)
    confidence = Column(Float, default=1.0, comment="신뢰도 점수 (0.0~1.0)")

    # Relationships
    extractor_version = Column(String(50), nullable=False, default="rule_v1")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    evidences = orm_relationship("RelationshipEvidence", back_populates="relationship")


class RelationshipEvidence(Base):
    __tablename__ = "relationship_evidence"
    __table_args__ = (
        Index("uq_relationship_evidence_hash", "relationship_id", "evidence_hash", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    relationship_id = Column(Integer, ForeignKey("relationship.id"), nullable=False, index=True)
    filing_id = Column(String(14), ForeignKey("filing.rcept_no"), nullable=True, index=True)
    excerpt = Column(Text, nullable=False, comment="관계 증거 원문 발췌 텍스트")
    location = Column(String(200), nullable=True, comment="문서 내 위치 (표/단락)")
    published_at = Column(DateTime, nullable=False)
    extractor_version = Column(String(50), default="rule_v1")

    # Relationships
    source_document = Column(String(500), nullable=False)
    source_url = Column(String(1000), nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    evidence_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    relationship = orm_relationship("Relationship", back_populates="evidences")
    filing = orm_relationship("Filing", back_populates="evidences")


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshot"
    __table_args__ = (
        Index("uq_feature_snapshot", "security_id", "as_of_date", "feature_name", "feature_version", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    security_id = Column(Integer, ForeignKey("security.id"), nullable=False, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    feature_name = Column(String(100), nullable=False)
    value = Column(Numeric(15, 4), nullable=True)
    feature_version = Column(String(20), default="v1.0")
    formula = Column(String(500), nullable=False, default="")
    inputs = Column(JSON, nullable=False, default=dict)
    source_available_at = Column(DateTime, nullable=True)
    missing_reason = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshot"
    __table_args__ = (
        Index("uq_score_snapshot", "security_id", "as_of_date", "horizon", "version", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    security_id = Column(Integer, ForeignKey("security.id"), nullable=False, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    horizon = Column(String(20), nullable=False, comment="5d, 20d, 60d")
    component = Column(JSON, nullable=False, comment="하위 6개 점수 breakdown (Macro, Industry, Fundamental, Momentum, Disclosure, Chain)")
    total = Column(Float, nullable=False, comment="Alpha Score (0~100)")
    confidence = Column(Float, default=1.0, comment="점수 신뢰도 (0~1.0)")
    version = Column(String(20), default="v1.0")
    weights = Column(JSON, nullable=False, default=dict)
    explanations = Column(JSON, nullable=False, default=dict)
    completeness = Column(Float, nullable=False, default=0.0)
    config_hash = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AlphaWeightConfig(Base):
    __tablename__ = "alpha_weight_config"

    version = Column(String(20), primary_key=True)
    weights = Column(JSON, nullable=False)
    changed_by = Column(String(100), nullable=False)
    reason = Column(String(500), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    score_version = Column(String(20), nullable=False)
    horizon = Column(String(20), nullable=False)
    config = Column(JSON, nullable=False)
    dataset_hash = Column(String(64), nullable=False, index=True)
    parameter_adjustments = Column(Integer, nullable=False, default=0)
    train_start = Column(Date, nullable=True)
    train_end = Column(Date, nullable=True)
    validation_start = Column(Date, nullable=True)
    validation_end = Column(Date, nullable=True)
    test_start = Column(Date, nullable=True)
    test_end = Column(Date, nullable=True)
    status = Column(String(20), nullable=False, default="COMPLETED")
    report = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class Prediction(Base):
    __tablename__ = "prediction"

    id = Column(Integer, primary_key=True, autoincrement=True)
    security_id = Column(Integer, ForeignKey("security.id"), nullable=False, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    horizon = Column(String(20), nullable=False, comment="5d, 20d, 60d")
    probability = Column(Float, nullable=False, comment="상승 확률 (0.0~1.0)")
    model_version = Column(String(50), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_resource", "resource_type", "resource_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor = Column(String(200), nullable=False, index=True)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(String(100), nullable=True)
    before_state = Column(JSON, nullable=True)
    after_state = Column(JSON, nullable=True)
    request_id = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

class GlobalEvent(Base):
    """A point-in-time external event that may propagate into Korean equities."""

    __tablename__ = "global_event"
    __table_args__ = (
        Index("ix_global_event_occurred_kind", "occurred_at", "event_kind"),
        Index("ix_global_event_symbol_occurred", "symbol", "occurred_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(250), nullable=False, unique=True, index=True)
    source = Column(String(50), nullable=False)
    origin_country = Column(String(2), nullable=False, default="US")
    event_kind = Column(String(50), nullable=False, index=True)
    symbol = Column(String(50), nullable=True, index=True)
    title = Column(String(500), nullable=False)
    summary = Column(Text, nullable=True)
    direction = Column(String(20), nullable=True)
    occurred_at = Column(DateTime, nullable=False, index=True)
    available_at = Column(DateTime, nullable=False, index=True)
    return_1d = Column(Float, nullable=True)
    zscore_20d = Column(Float, nullable=True)
    shock_score = Column(Float, nullable=False)
    event_metadata = Column("metadata", JSON, nullable=False, default=dict)
    raw_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
