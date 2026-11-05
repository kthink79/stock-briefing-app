"""
Stock & Tech News Crawler
Collects macro indicators (indices, exchange rates) and sector news (semiconductors, aerospace).
"""
import datetime
from typing import Dict, List, Any


class StockDataCollector:
    def __init__(self):
        self.sectors = ["반도체 (Semiconductors)", "우주항공 (Aerospace)", "빅테크 (Big Tech)"]

    def fetch_macro_indicators(self) -> Dict[str, Any]:
        """
        Fetches macro market indices and exchange rates.
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
            }
        }

    def fetch_sector_news(self, sector: str) -> List[Dict[str, str]]:
        """
        Gathers latest headlines for specified sector.
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
        return sample_headlines.get(sector, [])

    def compile_raw_report(self) -> Dict[str, Any]:
        macro = self.fetch_macro_indicators()
        sector_data = {sec: self.fetch_sector_news(sec) for sec in self.sectors}
        return {
            "macro": macro,
            "sectors": sector_data
        }


if __name__ == "__main__":
    collector = StockDataCollector()
    report = collector.compile_raw_report()
    print("Compiled Raw Report:", report)
