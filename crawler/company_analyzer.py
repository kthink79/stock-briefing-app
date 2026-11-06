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
        "price": 201500.0,
        "change_pct": "+1.25%",
        "market_cap": "1,203조원",
        "per": 7.9,
        "pbr": 2.08,
        "eps": 25506.0,
        "bps": 96875.0,
        "dividend_yield": "2.50%",
        "quarterly_earnings": {
            "quarter": "2026 3Q",
            "revenue": "195.25조원",
            "operating_profit": "107.44조원 (YoY +783%, 글로벌 분기 1위)",
            "comment": "HBM4 및 고용량 서버 메모리 공급 확대로 사상 최대 분기 영업익 경신. 선행 PER 4배 수준 극단적 저평가"
        },
        "earnings_news": [
            {"title": "삼성전자 3Q 영업익 107.4조원 달성... 엔비디아 제치고 글로벌 1위", "source": "조선비즈"},
            {"title": "KB증권 '세계 1위 이익 기업에 선행 PER 4배 할인은 과도... 극단적 저평가'", "source": "KB증권"},
            {"title": "HBM4 공급망 주도 및 연간 주주환원 100조원 규모 확대 추진", "source": "연합뉴스"}
        ]
    },
    "000660": {
        "ticker": "000660",
        "name": "SK하이닉스",
        "currency": "KRW",
        "price": 1449000.0,
        "change_pct": "+8.13%",
        "market_cap": "1,050조원",
        "per": 6.8,
        "pbr": 2.95,
        "eps": 213000.0,
        "bps": 491200.0,
        "dividend_yield": "2.80%",
        "quarterly_earnings": {
            "quarter": "2026 3Q",
            "revenue": "104.62조원 (YoY +328%)",
            "operating_profit": "83.33조원 (OPM 79.7% 어닝 서프라이즈)",
            "comment": "2029년까지 HBM4 및 차세대 서버 D램 생산 물량 LTA 완판. 꿈의 이익률 80% 달성"
        },
        "earnings_news": [
            {"title": "SK하이닉스 3Q 매출 104.6조·영업익 83.3조... '꿈의 이익률 80%'", "source": "뉴스1"},
            {"title": "주주환원 100조원 푼다... 2029년까지 D램·HBM 물량 완판 공시", "source": "파이낸셜뉴스"},
            {"title": "HBM4 본격 출하 및 7세대 HBM4e 고객사 샘플 공급 순항", "source": "전자신문"}
        ]
    },
    "NVDA": {
        "ticker": "NVDA",
        "name": "엔비디아 (NVIDIA)",
        "currency": "USD",
        "price": 184.20,
        "change_pct": "+2.15%",
        "market_cap": "4.52조 달러",
        "per": 38.5,
        "pbr": 35.2,
        "eps": 4.78,
        "bps": 5.23,
        "dividend_yield": "0.02%",
        "quarterly_earnings": {
            "quarter": "FY2027 Q2",
            "revenue": "96.22B USD (YoY +106%)",
            "operating_profit": "63.73B USD (YoY +124%)",
            "comment": "데이터센터 매출 $89B. 블랙웰(Blackwell) 아키텍처 본격 출하 및 부품 공급 약정 $2,790억으로 급증"
        },
        "earnings_news": [
            {"title": "엔비디아 2분기 매출 962억 달러·영업익 637억 달러... 역대 최대 기록", "source": "서울경제"},
            {"title": "부품 공급 약정 2,790억 달러로 급증... HBM 싹쓸이 품귀 지속", "source": "Bloomberg"},
            {"title": "젠슨 황 'AI 수요는 100%에 가까우나 공급 제약이 유일한 변수'", "source": "Reuters"}
        ]
    },
    "012450": {
        "ticker": "012450",
        "name": "한화에어로스페이스",
        "currency": "KRW",
        "price": 1840000.0,
        "change_pct": "+3.85%",
        "market_cap": "93조원",
        "per": 14.2,
        "pbr": 3.12,
        "eps": 129500.0,
        "bps": 589000.0,
        "dividend_yield": "0.85%",
        "quarterly_earnings": {
            "quarter": "2026 2Q/3Q",
            "revenue": "9.20조원 (어닝 서프라이즈)",
            "operating_profit": "1.37조원 (비폴란드 OPM 30%대 후반)",
            "comment": "K9 자주포·천무 2차 물량 수출 본격화. 하반기 파이프라인 수주 잔고 50조원 가시화"
        },
        "earnings_news": [
            {"title": "한화에어로스페이스 분기 영업익 1.37조원 서프라이즈... 목표가 214만원 상향", "source": "매일경제"},
            {"title": "스페인·사우디·미국 등 해외 방산 파이프라인 50조원 달성 전망", "source": "다올투자증권"},
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
