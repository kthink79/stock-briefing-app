"""
Gemini Stock Analysis Engine
Synthesizes raw market and sector data into actionable 8 AM morning briefs for investors.
"""
from typing import Dict, Any


class GeminiBriefingGenerator:
    def __init__(self, model_name: str = "gemini-2.5-pro"):
        self.model_name = model_name

    def build_analysis_prompt(self, raw_data: Dict[str, Any]) -> str:
        """
        Builds a structured prompt for Gemini to generate investor morning briefings.
        """
        macro = raw_data.get("macro", {})
        sectors = raw_data.get("sectors", {})

        prompt = f"""
당신은 최고의 글로벌 주식 투자 분석가입니다.
아래 수집된 야간 글로벌 시장 데이터와 주요 기술주(반도체, 우주항공 등) 소식을 바탕으로,
오늘 아침 한국 증시 개장 전(오전 8시) 투자자가 반드시 확인해야 할 '모닝 브리핑 리포트'를 작성하세요.

[수집된 시장 데이터]
- 기준 시각: {macro.get('timestamp')}
- 주요 지수: {macro.get('indices')}
- 환율: {macro.get('exchange_rate')}

[주요 섹터 소식]
- 반도체: {sectors.get('반도체 (Semiconductors)', [])}
- 우주항공: {sectors.get('우주항공 (Aerospace)', [])}
- 빅테크: {sectors.get('빅테크 (Big Tech)', [])}

[작성 가이드라인]
1. 한 줄 시장 총평: 밤사이 뉴욕 증시 흐름과 국내 증시 예상 분위기
2. 핵심 거시 지표 요약: 나스닥 및 필라델피아 반도체 지수(SOX), 환율 변동 포인트
3. 섹터별 관전 포인트:
   - 반도체: 수혜 종목 및 공급망 영향
   - 우주항공: 방산/우주 모멘텀 분석
4. 오늘 장 개장 전 실전 투자 체크포인트 3가지
가독성 높고 명확한 구조로 작성해 주세요.
"""
        return prompt.strip()

    def generate_briefing(self, raw_data: Dict[str, Any]) -> str:
        prompt = self.build_analysis_prompt(raw_data)
        # Mock or API execution placeholder
        return f"[Gemini Analysis Output based on Prompt]\n{prompt[:200]}..."
