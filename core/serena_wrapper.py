import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class SerenaWrapper:
    """
    Serena 라이브러리를 사용하여 대화 기록을 지능적으로 요약 및 보존하는 래퍼.
    핵심적인 트레이딩 정보(수익률, 지표, 주요 결정 사항)를 우선적으로 추출합니다.
    """
    
    def __init__(self, model_name: str = "gpt-4o-mini"): # Default to a capable summary model
        self.model_name = model_name
        try:
            import serena
            self.enabled = True
            logger.info("Serena library loaded successfully.")
        except ImportError:
            self.enabled = False
            logger.warning("Serena library not found. Falling back to basic summary.")

    async def summarize_trading_context(self, messages: List[Dict[str, Any]]) -> str:
        """
        트레이딩 맥락을 고려하여 대화 기록을 요약합니다.
        """
        if not self.enabled:
            print(f"[Serena] 🗜️ Fallback summarizer triggered for {len(messages)} messages.")
            return self._basic_fallback_summary(messages)
            
        try:
            print(f"[Serena] 🚀 Intelligent compressor (Serena) triggered for {len(messages)} messages.")
            from serena import SerenaCompressor
            # Serena를 사용한 고수준 압축 로직 (실제 라이브러리 인터페이스에 맞춰 조정 필요)
            # 여기서는 개념적인 흐름을 구현합니다.
            compressor = SerenaCompressor(strategy="trading_focused")
            
            # 텍스트 추출
            full_text = "\n".join([f"{m['role']}: {m.get('content', '')}" for m in messages])
            
            summary = compressor.compress(
                text=full_text,
                preserve_keywords=["수익률", "수량", "매수", "매도", "P/L", "익절", "손절"],
                max_length=500
            )
            return summary
        except Exception as e:
            logger.error(f"Serena compression error: {e}")
            return self._basic_fallback_summary(messages)

    def _basic_fallback_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Serena를 사용할 수 없을 경우의 기본 요약 로직"""
        # 라이브러리가 없는 경우를 대비한 최소한의 보존 텍스트
        last_msgs = messages[-3:] if len(messages) > 3 else messages
        content_lines = [m.get("content", "")[:100] for m in last_msgs]
        return f"대화 내용 압축됨 (최근 요점: {' / '.join(content_lines)}...)"

# 싱글톤 인스턴스
serena_engine = SerenaWrapper()
