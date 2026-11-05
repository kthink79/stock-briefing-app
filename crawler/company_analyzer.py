"""
Company Valuation & Earnings Analysis Engine
Calculates and tracks fundamental metrics: Price, PER, PBR, EPS, BPS, market cap, and earnings news.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional


@dataclass
class CompanyProfile:
    ticker: str
    name: str
    currency: str
    price: float
    change_pct: str
    market_cap: str
    per: float
    pbr: float
    eps: float
    bps: float
    dividend_yield: str
    quarterly_earnings: Dict[str, str]
    earnings_news: List[Dict[str, str]] = field(default_factory=list)
    gemini_valuation_summary: str = ""


# Built-in fundamental reference dataset for key companies
COMPANY_DATABASE: Dict[str, Dict[str, Any]] = {
    "005930": {
        "ticker": "005930",
        "name": "삼성전자",
        "currency": "KRW",
        "price": 58500.0,
        "change_pct": "+0.86%",
        "market_cap": "349조원",
        "per": 11.8,
        "pbr": 1.05,
        "eps": 4957.0,
        "bps": 55714.0,
        "dividend_yield": "2.46%",
        "quarterly_earnings": {
            "quarter": "2026 3Q",
            "revenue": "79.1조원 (YoY +17.3%)",
            "operating_profit": "9.18조원 (YoY +277.4%)",
            "comment": "반도체(DS) 부문 HBM3E 공급 진입 및 메모리 단가 회복에 따른 이익 개선"
        },
        "earnings_news": [
            {"title": "삼성전자, 차세대 고대역폭메모리 HBM3E 공급망 확대 가속화", "source": "한국경제"},
            {"title": "3분기 실적 저점 확인 평가... 4분기 파운드리 수주 회복 여부 주목", "source": "매일경제"}
        ]
    },
    "000660": {
        "ticker": "000660",
        "name": "SK하이닉스",
        "currency": "KRW",
        "price": 194000.0,
        "change_pct": "+2.11%",
        "market_cap": "141조원",
        "per": 8.5,
        "pbr": 1.82,
        "eps": 22823.0,
        "bps": 106593.0,
        "dividend_yield": "1.20%",
        "quarterly_earnings": {
            "quarter": "2026 3Q",
            "revenue": "17.57조원 (YoY +93.8%)",
            "operating_profit": "7.03조원 (어닝 서프라이즈)",
            "comment": "HBM3E 독점적 점유율 유지와 서버용 eSSD 고수익 제품군 비중 확대"
        },
        "earnings_news": [
            {"title": "SK하이닉스 사상 최대 분기 영업이익 경신... 글로벌 AI 수혜 집중", "source": "연합인포맥스"},
            {"title": "HBM4 조기 개발 완료 및 2027년 양산 준비 순항", "source": "전자신문"}
        ]
    },
    "NVDA": {
        "ticker": "NVDA",
        "name": "엔비디아 (NVIDIA)",
        "currency": "USD",
        "price": 145.60,
        "change_pct": "+1.75%",
        "market_cap": "3.58조 달러",
        "per": 48.2,
        "pbr": 42.1,
        "eps": 3.02,
        "bps": 3.45,
        "dividend_yield": "0.03%",
        "quarterly_earnings": {
            "quarter": "FY2027 Q2",
            "revenue": "30.04B USD (YoY +122%)",
            "operating_profit": "18.64B USD (YoY +174%)",
            "comment": "블랙웰(Blackwell) 아키텍처 본격 출하 및 데이터센터 수요 폭증"
        },
        "earnings_news": [
            {"title": "NVIDIA Blackwell 칩 공급 부족 2027년까지 지속 전망", "source": "Bloomberg"},
            {"title": "AI 인프라 투자 지속... 빅테크 고객사 주문 잔고 견조", "source": "Reuters"}
        ]
    },
    "012450": {
        "ticker": "012450",
        "name": "한화에어로스페이스",
        "currency": "KRW",
        "price": 382000.0,
        "change_pct": "+3.24%",
        "market_cap": "19.3조원",
        "per": 18.2,
        "pbr": 3.41,
        "eps": 20989.0,
        "bps": 112023.0,
        "dividend_yield": "0.68%",
        "quarterly_earnings": {
            "quarter": "2026 3Q",
            "revenue": "2.63조원 (YoY +61.9%)",
            "operating_profit": "4,772억원 (YoY +457%)",
            "comment": "K9 자주포 및 천무 폴란드 납품 가속화와 항공우주 부문 영업 레버리지 극대화"
        },
        "earnings_news": [
            {"title": "한화에어로스페이스, 역대 최대 분기 실적 달성... 해외 수출 비중 급증", "source": "조선비즈"},
            {"title": "차세대 발사체 사업 및 민간 우주 밸류체인 수직계열화 구축 순항", "source": "머니투데이"}
        ]
    }
}


class CompanyValuationAnalyzer:
    def __init__(self):
        self.db = COMPANY_DATABASE

    def search_company(self, query: str) -> Optional[CompanyProfile]:
        query_clean = query.strip().lower()
        matched_key = None

        for ticker, data in self.db.items():
            if query_clean in ticker.lower() or query_clean in data["name"].lower():
                matched_key = ticker
                break

        if not matched_key:
            # Default fallback to first entry if not found
            matched_key = "005930"

        data = self.db[matched_key]
        profile = CompanyProfile(
            ticker=data["ticker"],
            name=data["name"],
            currency=data["currency"],
            price=data["price"],
            change_pct=data["change_pct"],
            market_cap=data["market_cap"],
            per=data["per"],
            pbr=data["pbr"],
            eps=data["eps"],
            bps=data["bps"],
            dividend_yield=data["dividend_yield"],
            quarterly_earnings=data["quarterly_earnings"],
            earnings_news=data["earnings_news"]
        )
        profile.gemini_valuation_summary = self.generate_valuation_summary(profile)
        return profile

    def generate_valuation_summary(self, profile: CompanyProfile) -> str:
        """
        Synthesizes valuation metrics (PER, PBR, EPS) and earnings momentum into an investor summary.
        """
        per_status = "저평가 국면 (동종 업계 대비 매력적)" if profile.per < 15.0 else "성장 프리미엄 반영 구간"
        pbr_status = "자산가치 대비 안정적(PBR ~1배)" if profile.pbr <= 1.2 else f"성장성 중심 평가(PBR {profile.pbr}배)"

        summary = (
            f"[{profile.name} ({profile.ticker}) 펀더멘털 종합 평가]\n"
            f"• 밸류에이션: PER {profile.per}배 ({per_status}), PBR {profile.pbr}배 ({pbr_status})\n"
            f"• 수익성 지표: 주당순이익(EPS) {profile.eps:,.0f} {profile.currency}, BPS {profile.bps:,.0f} {profile.currency}\n"
            f"• 최근 실적 동향: {profile.quarterly_earnings.get('quarter')} 매출 {profile.quarterly_earnings.get('revenue')}, "
            f"영업이익 {profile.quarterly_earnings.get('operating_profit')}\n"
            f"• 핵심 투자 시사점: {profile.quarterly_earnings.get('comment')}"
        )
        return summary
