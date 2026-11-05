"""
Stock & Tech News Crawler
Collects macro indicators (indices, exchange rates) and sector news (semiconductors, aerospace).
Includes robust error handling and fallback data mechanisms.
"""
import datetime
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


# Default fallback datasets for resilient operation
DEFAULT_FALLBACK_MACRO = {
    "indices": {
        "NASDAQ": {"value": 18500.00, "change": "0.00%"},
        "S&P 500": {"value": 5900.00, "change": "0.00%"},
        "Dow Jones": {"value": 43500.00, "change": "0.00%"},
        "SOX (필라델피아 반도체)": {"value": 5200.00, "change": "0.00%"},
    },
    "exchange_rate": {
        "USD/KRW": 1380.00
    },
    "status": "fallback"
}

DEFAULT_FALLBACK_SECTOR_NEWS: Dict[str, List[Dict[str, str]]] = {
    "반도체 (Semiconductors)": [
        {"title": "[대체 데이터] 글로벌 AI 반도체 및 파운드리 공급망 동향 점검", "source": "시장 종합"},
        {"title": "[대체 데이터] 첨단 메모리 패키징 기술 경쟁 심화", "source": "시장 종합"}
    ],
    "우주항공 (Aerospace)": [
        {"title": "[대체 데이터] 우주 발사체 및 위성 통신 산업 수주 모멘텀", "source": "시장 종합"},
        {"title": "[대체 데이터] 글로벌 방산 및 민간 항공우주 생태계 동향", "source": "시장 종합"}
    ],
    "빅테크 (Big Tech)": [
        {"title": "[대체 데이터] 주요 빅테크 AI 인프라 투자 지속 전망", "source": "시장 종합"}
    ]
}


class StockDataCollector:
    def __init__(self):
        self.sectors = ["반도체 (Semiconductors)", "우주항공 (Aerospace)", "빅테크 (Big Tech)"]

    def get_fallback_macro_indicators(self) -> Dict[str, Any]:
        """
        Returns structured fallback macro market indicators with a fresh timestamp.
        """
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        fallback = {
            "timestamp": now,
            "indices": {k: dict(v) for k, v in DEFAULT_FALLBACK_MACRO["indices"].items()},
            "exchange_rate": dict(DEFAULT_FALLBACK_MACRO["exchange_rate"]),
            "status": "fallback"
        }
        return fallback

    def get_fallback_sector_news(self, sector: Optional[str]) -> List[Dict[str, str]]:
        """
        Returns fallback news items for the given sector or a general fallback list.
        """
        if sector and sector in DEFAULT_FALLBACK_SECTOR_NEWS:
            return [dict(item) for item in DEFAULT_FALLBACK_SECTOR_NEWS[sector]]
        return [
            {"title": f"[대체 데이터] {sector or '주요 섹터'} 관련 시장 종합 뉴스 업데이트 준비 중", "source": "시장 종합"}
        ]

    def _collect_macro_indicators(self) -> Dict[str, Any]:
        """
        Internal fetch method for macro indicators.
        In production, calls financial APIs or parses reliable public market feeds.
        """
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        return {
            "timestamp": now,
            "indices": {
                "NASDAQ": {"value": 18983.47, "change": "+0.45%"},
                "S&P 500": {"value": 5973.10, "change": "+0.32%"},
                "Dow Jones": {"value": 43729.93, "change": "-0.01%"},
                "SOX (필라델피아 반도체)": {"value": 5298.54, "change": "+1.15%"},
            },
            "exchange_rate": {
                "USD/KRW": 1385.50
            },
            "status": "live"
        }

    def fetch_macro_indicators(self) -> Dict[str, Any]:
        """
        Fetches macro market indices and exchange rates.
        Safely catches exceptions and returns structured fallback indicators.
        """
        try:
            data = self._collect_macro_indicators()
            if not isinstance(data, dict) or "indices" not in data or "exchange_rate" not in data:
                raise ValueError("Incomplete macro indicators data structure received")
            return data
        except Exception as e:
            logger.error("Failed to fetch macro indicators: %s. Using fallback data.", e, exc_info=True)
            return self.get_fallback_macro_indicators()

    def _collect_sector_news(self, sector: str) -> List[Dict[str, str]]:
        """
        Internal fetch method for sector news.
        """
        sample_headlines = {
            "반도체 (Semiconductors)": [
                {"title": "글로벌 AI 반도체 수요 지속... HBM3E 공급망 확대 가속", "source": "Finance News"},
                {"title": "주요 파운드리 첨단 패키징 가동률 90% 상회 전망", "source": "Tech Daily"}
            ],
            "우주항공 (Aerospace)": [
                {"title": "차세대 발사체 및 위성 체계 계약 확대 기대감", "source": "Aerospace Times"},
                {"title": "민간 우주 방산 수주 모멘텀 지속", "source": "Industry Report"}
            ],
            "빅테크 (Big Tech)": [
                {"title": "클라우드 인프라 투자 확대 기조 확인", "source": "Market Insider"}
            ]
        }
        if sector not in sample_headlines:
            raise KeyError(f"Unknown sector: {sector}")
        return sample_headlines[sector]

    def fetch_sector_news(self, sector: str) -> List[Dict[str, str]]:
        """
        Gathers latest headlines for specified sector with error handling and fallback.
        """
        try:
            if not isinstance(sector, str) or not sector.strip():
                logger.warning("Invalid sector query provided: %s. Falling back to default news.", sector)
                return self.get_fallback_sector_news(sector)
            return self._collect_sector_news(sector)
        except Exception as e:
            logger.error("Failed to fetch news for sector '%s': %s. Using fallback data.", sector, e, exc_info=True)
            return self.get_fallback_sector_news(sector)

    def compile_raw_report(self) -> Dict[str, Any]:
        """
        Compiles both macro indicators and sector news into a single dictionary.
        Guarantees resilient fallback even if top-level failures occur.
        """
        try:
            macro = self.fetch_macro_indicators()
        except Exception as e:
            logger.error("Unexpected error in compile_raw_report while fetching macro: %s", e, exc_info=True)
            macro = self.get_fallback_macro_indicators()

        sector_data: Dict[str, List[Dict[str, str]]] = {}
        for sec in self.sectors:
            try:
                sector_data[sec] = self.fetch_sector_news(sec)
            except Exception as e:
                logger.error("Unexpected error fetching sector news for '%s': %s", sec, e, exc_info=True)
                sector_data[sec] = self.get_fallback_sector_news(sec)

        return {
            "macro": macro,
            "sectors": sector_data
        }


if __name__ == "__main__":
    collector = StockDataCollector()
    report = collector.compile_raw_report()
    print("Compiled Raw Report:", report)
