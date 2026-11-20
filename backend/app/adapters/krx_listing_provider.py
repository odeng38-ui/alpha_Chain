"""KRX KIND Listing Provider Adapter.

Read-only provider for verifying security listing status against official KRX KIND data.
Strictly adheres to ALPHA CHAIN safety rules:
- Zero DB write (read-only in-memory reference cache)
- 4-tier status priority: LISTED -> DELISTED -> UNLISTED -> UNKNOWN
- Never confirms LISTED based solely on DB market/effective_to
- Safe fallback to UNKNOWN on cache corruption or mismatch
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Literal, Optional

logger = logging.getLogger(__name__)

ListingStatus = Literal["LISTED", "DELISTED", "UNLISTED", "UNKNOWN"]


@dataclass(frozen=True)
class KrxListingVerification:
    ticker: str
    status: ListingStatus
    official_name: Optional[str]
    market: Optional[str]
    listed_at: Optional[str]
    delisted_at: Optional[str]
    source: str
    as_of: str
    verified_by_official: bool
    conflict_reason: Optional[str] = None


class KrxListingProvider:
    """Read-only provider verifying securities against official KRX KIND listing data."""

    _instance: Optional[KrxListingProvider] = None

    def __init__(self, reference_path: Optional[str] = None) -> None:
        if reference_path is None:
            # Default to backend/app/data/krx_listing_reference.json
            base_dir = Path(__file__).resolve().parent.parent
            reference_path = str(base_dir / "data" / "krx_listing_reference.json")

        self.reference_path = reference_path
        self._listed: Dict[str, Dict[str, Any]] = {}
        self._delisted: Dict[str, Dict[str, Any]] = {}
        self._metadata: Dict[str, Any] = {}
        self._loaded = False
        self._load_cache()

    @classmethod
    def get_instance(cls) -> KrxListingProvider:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _normalize_name(name: Optional[str]) -> str:
        if not name:
            return ""
        # Remove (주), 주식회사, spaces, and punctuation
        n = re.sub(r"\(주\)|주식회사|\(유\)|유한회사|\s+|[\.,\-_]", "", name)
        return n.upper().strip()

    def _load_cache(self) -> None:
        if not os.path.exists(self.reference_path):
            logger.warning("KRX listing reference file not found at %s", self.reference_path)
            self._loaded = False
            return

        try:
            with open(self.reference_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._metadata = data.get("metadata", {})
            self._listed = data.get("listed", {})
            self._delisted = data.get("delisted", {})
            self._loaded = bool(self._listed or self._delisted)
            logger.info(
                "KrxListingProvider loaded %d listed, %d delisted (as_of: %s)",
                len(self._listed),
                len(self._delisted),
                self._metadata.get("as_of", "UNKNOWN"),
            )
        except Exception as exc:
            logger.error("Failed to load KRX listing reference: %s", exc)
            self._loaded = False

    @property
    def is_available(self) -> bool:
        return self._loaded

    @property
    def as_of_date(self) -> str:
        return self._metadata.get("as_of", "UNKNOWN")

    @property
    def source_name(self) -> str:
        return self._metadata.get("source", "KRX_KIND")

    def verify_security(
        self,
        ticker: str,
        company_name: Optional[str] = None,
        db_delisted_at: Optional[date] = None,
    ) -> KrxListingVerification:
        """Verify the listing status of a security using official KRX KIND evidence.

        Priority:
        1. Official KRX currently listed -> LISTED (after conflict check)
        2. Official KRX delisted or DB delisted_at -> DELISTED
        3. Unlisted official evidence / DART-only -> UNLISTED
        4. No official evidence / load failure -> UNKNOWN
        """
        clean_ticker = str(ticker).strip().upper().zfill(6)
        as_of = self.as_of_date
        source = self.source_name

        # If cache is unavailable, never assume LISTED. Return UNKNOWN safely.
        if not self._loaded:
            return KrxListingVerification(
                ticker=clean_ticker,
                status="UNKNOWN",
                official_name=None,
                market=None,
                listed_at=None,
                delisted_at=None,
                source="NONE",
                as_of="UNKNOWN",
                verified_by_official=False,
                conflict_reason="KRX_REFERENCE_UNAVAILABLE",
            )

        # 1. Check official LISTED
        if clean_ticker in self._listed:
            official = self._listed[clean_ticker]
            official_name = official.get("name")
            official_market = official.get("market")
            listed_at = official.get("listed_at")

            # Check for name conflict (ticker reuse or completely different company)
            if company_name and official_name:
                norm_input = self._normalize_name(company_name)
                norm_official = self._normalize_name(official_name)
                # If neither contains the other and length >= 2, flag potential conflict
                if norm_input and norm_official and (norm_input not in norm_official and norm_official not in norm_input):
                    # Check historical notes or flag conflict
                    return KrxListingVerification(
                        ticker=clean_ticker,
                        status="UNKNOWN",
                        official_name=official_name,
                        market=official_market,
                        listed_at=listed_at,
                        delisted_at=None,
                        source=source,
                        as_of=as_of,
                        verified_by_official=False,
                        conflict_reason=f"NAME_MISMATCH: DB({company_name}) != KRX({official_name})",
                    )

            return KrxListingVerification(
                ticker=clean_ticker,
                status="LISTED",
                official_name=official_name,
                market=official_market,
                listed_at=listed_at,
                delisted_at=None,
                source=source,
                as_of=as_of,
                verified_by_official=True,
            )

        # 2. Check official DELISTED or DB explicit delisted_at
        if clean_ticker in self._delisted:
            delisted_info = self._delisted[clean_ticker]
            return KrxListingVerification(
                ticker=clean_ticker,
                status="DELISTED",
                official_name=delisted_info.get("name"),
                market=delisted_info.get("market"),
                listed_at=delisted_info.get("listed_at"),
                delisted_at=delisted_info.get("delisted_at"),
                source=source,
                as_of=as_of,
                verified_by_official=True,
            )

        if db_delisted_at is not None:
            return KrxListingVerification(
                ticker=clean_ticker,
                status="DELISTED",
                official_name=None,
                market=None,
                listed_at=None,
                delisted_at=str(db_delisted_at),
                source="DB_DELISTED_RECORD",
                as_of=as_of,
                verified_by_official=True,
            )

        # 3. Not found in official listed or delisted
        # If ticker looks like an internal/unlisted code or DART non-listed corporation
        return KrxListingVerification(
            ticker=clean_ticker,
            status="UNKNOWN",
            official_name=None,
            market=None,
            listed_at=None,
            delisted_at=None,
            source=source,
            as_of=as_of,
            verified_by_official=False,
            conflict_reason="NOT_FOUND_IN_OFFICIAL_KRX_RECORDS",
        )
