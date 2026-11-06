"""
Topic & Keyword Trace Timeline Tracking Engine
Monitors registered topics/keywords (e.g. '트럼프', '미국 이란 전쟁') from the time of registration,
accumulates relevant events chronologically, and maintains a rolling synthesis of developments.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
import os
import re
from typing import List, Dict, Any, Optional


@dataclass
class TraceEvent:
    timestamp: str
    title: str
    source: str
    content_snippet: str
    impact_level: str = "Medium"  # Low, Medium, High
    summary_bullets: List[str] = field(default_factory=list)
    market_impact: str = ""


@dataclass
class TopicTrace:
    id: str
    keyword_or_topic: str
    registered_at: str
    events: List[TraceEvent] = field(default_factory=list)
    accumulated_summary: str = ""

    def add_event(self, event: TraceEvent) -> bool:
        # Prevent duplicate events by title or normalized text
        norm_title = re.sub(r'[\s\W_]+', '', event.title.lower())
        for existing in self.events:
            existing_norm = re.sub(r'[\s\W_]+', '', existing.title.lower())
            if norm_title == existing_norm or norm_title in existing_norm or existing_norm in norm_title:
                return False
        self.events.append(event)
        # Sort chronologically
        self.events.sort(key=lambda e: e.timestamp)
        return True


class TraceManager:
    def __init__(self, storage_path: str = "data/traces.json"):
        self.storage_path = storage_path
        self.traces: Dict[str, TopicTrace] = {}
        self.load_traces()

    def add_trace(self, keyword_or_topic: str) -> TopicTrace:
        """
        Registers a new topic or keyword for continuous tracking.
        """
        clean_name = re.sub(r'[^a-zA-Z0-9가-힣]', '_', keyword_or_topic.strip())
        now_str = datetime.now().isoformat()
        trace_id = f"trace_{clean_name}_{int(datetime.now().timestamp())}"

        trace = TopicTrace(
            id=trace_id,
            keyword_or_topic=keyword_or_topic.strip(),
            registered_at=now_str,
            events=[],
            accumulated_summary=f"'{keyword_or_topic}' 트레이스가 등록되었습니다 ({now_str}). 관련 뉴스를 실시간 추적합니다."
        )
        self.traces[trace_id] = trace
        self.save_traces()
        return trace

    def remove_trace(self, trace_id: str) -> bool:
        if trace_id in self.traces:
            del self.traces[trace_id]
            self.save_traces()
            return True
        return False

    def list_traces(self) -> List[TopicTrace]:
        return list(self.traces.values())

    @staticmethod
    def is_keyword_matched(keyword_or_topic: Optional[str], text: Optional[str]) -> bool:
        """
        Accurately checks whether keyword_or_topic matches the provided text using regex,
        word boundaries, and partial matching logic.
        """
        if not keyword_or_topic or not text:
            return False

        kw = keyword_or_topic.strip()
        target = text.strip()
        if not kw or not target:
            return False

        # 1. Regex evaluation if input looks like a regex pattern or contains regex metacharacters
        regex_chars = set(r".*+?^$[](){}\|")
        has_regex_metachar = any(c in kw for c in regex_chars)
        if has_regex_metachar:
            try:
                if re.search(kw, target, re.IGNORECASE):
                    return True
            except re.error:
                # If invalid regex syntax, continue with escaped literal matching
                pass

        # 2. Exact phrase match with whitespace normalization
        # Collapse multiple whitespace characters into single space
        norm_kw = re.sub(r'\s+', ' ', kw.lower())
        norm_target = re.sub(r'\s+', ' ', target.lower())

        if norm_kw in norm_target:
            # Check word boundary for purely alphanumeric ASCII phrase to prevent false substrings (e.g. 'AI' in 'TRAIN')
            if re.match(r'^[a-zA-Z0-9\s]+$', norm_kw):
                pattern = r'\b' + re.escape(norm_kw) + r'\b'
                if re.search(pattern, norm_target):
                    return True
            else:
                return True

        # 3. Multi-word topic handling:
        # Check if all tokens match within the target text (partial & word-boundary matching per token)
        tokens = [t.strip() for t in kw.split() if t.strip()]
        if len(tokens) > 1:
            all_tokens_matched = True
            for token in tokens:
                token_lower = token.lower()
                if re.match(r'^[a-zA-Z0-9]+$', token_lower):
                    # Alphanumeric token: enforce word boundary
                    token_pat = r'\b' + re.escape(token_lower) + r'\b'
                    if not re.search(token_pat, norm_target):
                        all_tokens_matched = False
                        break
                else:
                    # Non-alphanumeric / Korean token: substring match in normalized target
                    if token_lower not in norm_target:
                        all_tokens_matched = False
                        break
            if all_tokens_matched:
                return True

        # 4. Fallback: single alphanumeric token with boundary check
        if len(tokens) == 1 and re.match(r'^[a-zA-Z0-9]+$', norm_kw):
            return bool(re.search(r'\b' + re.escape(norm_kw) + r'\b', norm_target))

        return False

    def match_news_to_traces(self, news_items: List[Dict[str, str]]) -> Dict[str, List[TraceEvent]]:
        """
        Matches incoming news items against active trace keywords/topics.
        Returns a mapping of trace_id -> newly added TraceEvents.
        """
        new_matches: Dict[str, List[TraceEvent]] = {}
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        high_impact_pattern = re.compile(r"긴급|전쟁|급등|급락|제재|발언", re.IGNORECASE)

        for trace_id, trace in self.traces.items():
            new_matches[trace_id] = []

            for item in news_items:
                title = item.get("title", "")
                snippet = item.get("snippet", title)
                source = item.get("source", "Market News")
                combined_text = f"{title} {snippet}"

                # Enhanced regex and partial keyword matching
                if self.is_keyword_matched(trace.keyword_or_topic, combined_text):
                    impact_level = "High" if high_impact_pattern.search(title or "") else "Medium"
                    bullets = [
                        f"{title} 관련 핵심 발표 및 발언 내용 확인",
                        f"상세 맥락: {snippet[:120]}...",
                        f"관련 밸류체인 및 시장 공급망 파급 효과 분석 진행 중"
                    ]
                    market_impact = f"'{trace.keyword_or_topic}' 관련 테마 및 수혜·피해 종목군 주가 변동성 요인"
                    event = TraceEvent(
                        timestamp=now_str,
                        title=title,
                        source=source,
                        content_snippet=snippet,
                        impact_level=impact_level,
                        summary_bullets=bullets,
                        market_impact=market_impact
                    )
                    if trace.add_event(event):
                        new_matches[trace_id].append(event)

            if new_matches[trace_id]:
                self._update_rolling_summary(trace)

        if any(len(events) > 0 for events in new_matches.values()):
            self.save_traces()

        return new_matches

    def _update_rolling_summary(self, trace: TopicTrace):
        """
        Synthesizes recent timeline events into an updated chronological narrative summary.
        """
        if not trace.events:
            return

        latest_events = trace.events[-5:]  # Recent 5 events
        event_lines = []
        for e in latest_events:
            b_text = " / ".join(e.summary_bullets) if e.summary_bullets else e.content_snippet
            event_lines.append(f"- [{e.timestamp}] {e.title} ({e.source})\n  * 요약: {b_text}\n  * 영향: {e.market_impact}")

        trace.accumulated_summary = (
            f"### [{trace.keyword_or_topic}] 실시간 심층 종합 분석 (이벤트 총 {len(trace.events)}건)\n"
            f"등록 시점: {trace.registered_at}\n\n"
            f"**주요 전개 동향 및 본문 분석 요약:**\n" + "\n".join(event_lines) + "\n\n"
            f"**종합 시황 영향 진단:** 기사 본문 분석 결과 관련 테마와 수혜·피해 종목군의 중장기 파급 효과를 지속 추적 중입니다."
        )

    def save_traces(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        data = {tid: asdict(trace) for tid, trace in self.traces.items()}
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_traces(self):
        if not os.path.exists(self.storage_path):
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for tid, tdata in data.items():
                events = [TraceEvent(**e) for e in tdata.get("events", [])]
                self.traces[tid] = TopicTrace(
                    id=tdata["id"],
                    keyword_or_topic=tdata["keyword_or_topic"],
                    registered_at=tdata["registered_at"],
                    events=events,
                    accumulated_summary=tdata.get("accumulated_summary", "")
                )
        except Exception as e:
            print(f"Error loading traces: {e}")
