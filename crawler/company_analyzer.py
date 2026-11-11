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
        "price": 193200.0,
        "change_pct": "+2.38% (+4,500원)",
        "market_cap": "1,153조원",
        "per": 7.58,
        "pbr": 1.99,
        "eps": 25506.0,
        "bps": 96875.0,
        "dividend_yield": "2.65%",
        "quarterly_earnings": {
            "quarter": "2026 3Q",
            "revenue": "195.25조원",
            "operating_profit": "107.44조원 (YoY +783%, 글로벌 분기 1위)",
            "comment": "HBM4 및 고용량 서버 메모리 공급 확대로 사상 최대 분기 영업익 경신. 11/9 코스피 5,100선 공방 속 저가 매수세 유입 반등"
        },
        "earnings_news": [
            {"title": "외국인 7일째 매도에도 개미·기관 ETF 매수세에 삼전닉스 반등 (11/09)", "source": "머니투데이"},
            {"title": "삼성전자 3Q 영업익 107.4조원 달성... 엔비디아 제치고 글로벌 1위", "source": "조선비즈"},
            {"title": "엔비디아 AI 데이 서울 개막... 삼성전자 반도체 팹 디지털 트윈 협력 가속", "source": "연합뉴스"}
        ]
    },
    "000660": {
        "ticker": "000660",
        "name": "SK하이닉스",
        "currency": "KRW",
        "price": 1175000.0,
        "change_pct": "+3.80% (+43,000원)",
        "market_cap": "858.3조원",
        "per": 5.24,
        "pbr": 3.19,
        "eps": 224313.0,
        "bps": 368800.0,
        "dividend_yield": "2.80%",
        "quarterly_earnings": {
            "quarter": "2026 3Q",
            "revenue": "104.62조원 (YoY +328%)",
            "operating_profit": "83.33조원 (OPM 79.7% 어닝 서프라이즈)",
            "comment": "2029년까지 HBM4 및 차세대 서버 D램 생산 물량 LTA 완판. 11/9 기타법인 1,305억원 자사주 매입 및 기관 매수로 117만원선 탈환"
        },
        "earnings_news": [
            {"title": "SK하이닉스 1,175,000원(+3.80%) 급반등... 117만원선 탈환 (11/09)", "source": "매일경제"},
            {"title": "기타법인 1,305억원 자사주 매입 및 기관 매수세 유입", "source": "머니투데이"},
            {"title": "엔비디아 디푸 탈라 부사장, SK하이닉스 팹 옴니버스 협력 발표", "source": "연합뉴스"}
        ]
    },
    "NVDA": {
        "ticker": "NVDA",
        "name": "엔비디아 (NVIDIA)",
        "currency": "USD",
        "price": 186.64,
        "change_pct": "+2.78% (+$5.04)",
        "market_cap": "4.57조 달러",
        "per": 65.72,
        "pbr": 29.30,
        "eps": 2.84,
        "bps": 6.37,
        "dividend_yield": "0.02%",
        "quarterly_earnings": {
            "quarter": "FY2027 Q2",
            "revenue": "96.22B USD (YoY +106%)",
            "operating_profit": "63.73B USD (YoY +124%)",
            "comment": "뉴욕 $186.64 반등 마감. 9일 서울 코엑스 엔비디아 AI 데이 개막 및 피지컬 AI 동맹 선언"
        },
        "earnings_news": [
            {"title": "엔비디아 '피지컬 AI 황금시대' 선언... 삼성·SK 공장 디지털 트윈, 현대차·LG 로봇 협력", "source": "연합뉴스"},
            {"title": "엔비디아 AI 데이 서울 2026 개막... 카카오모빌리티 자율주행 MOU 체결", "source": "뉴스핌"},
            {"title": "부품 공급 약정 2,790억 달러로 급증... HBM 싹쓸이 품귀 지속", "source": "Bloomberg"}
        ]
    },
    "012450": {
        "ticker": "012450",
        "name": "한화에어로스페이스",
        "currency": "KRW",
        "price": 649000.0,
        "change_pct": "-1.67% (-11,000원)",
        "market_cap": "33.5조원",
        "per": 16.18,
        "pbr": 2.40,
        "eps": 40120.0,
        "bps": 270800.0,
        "dividend_yield": "0.85%",
        "quarterly_earnings": {
            "quarter": "2026 2Q/3Q",
            "revenue": "9.20조원 (어닝 서프라이즈)",
            "operating_profit": "1.37조원 (비폴란드 OPM 30%대 후반)",
            "comment": "K9 자주포·천무 2차 물량 수출 본격화. 11/9 반도체 반등 장세 속 649,000원선 소폭 숨고르기"
        },
        "earnings_news": [
            {"title": "한화에어로스페이스 649,000원(-1.67%) 거래... 방산 섹터 숨고르기 (11/09)", "source": "매일경제"},
            {"title": "K9 자주포 및 천무 2차 해외 양산 납품 정상 가동 확인", "source": "방사청"},
            {"title": "스페인·사우디·미국 등 해외 방산 파이프라인 50조원 달성 전망", "source": "다올투자증권"}
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

    def update_live_quotes(self, quote_updates: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, CompanyProfile]:
        """
        Updates stock prices and dynamically recalculates PER and PBR for tracked companies during interval sync.
        """
        updated_profiles = {}
        for ticker, data in self.db.items():
            if quote_updates and ticker in quote_updates:
                upd = quote_updates[ticker]
                if "price" in upd:
                    data["price"] = float(upd["price"])
                if "change_pct" in upd:
                    data["change_pct"] = str(upd["change_pct"])
                # Recalculate PER and PBR dynamically based on new live price
                if data.get("eps", 0) > 0:
                    data["per"] = round(data["price"] / data["eps"], 1)
                if data.get("bps", 0) > 0:
                    data["pbr"] = round(data["price"] / data["bps"], 2)
            updated_profiles[ticker] = self.search_company(ticker)
        return updated_profiles

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

    def register_company(self, company_dict: Dict[str, Any]) -> CompanyProfile:
        """
        Registers a new company profile to the database and returns its valuation profile.
        """
        ticker = company_dict.get("ticker", "UNKNOWN")
        self.db[ticker] = company_dict
        return self.search_company(ticker)

    def remove_company(self, ticker: str) -> bool:
        """
        Removes a company from the tracked watchlist database.
        """
        if ticker in self.db:
            del self.db[ticker]
            return True
        return False

