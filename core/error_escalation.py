"""
Error Escalation Module
Provides escalate_error() to invoke Hermes agent for auto-analysis on critical failures.
Also sends a Discord notification as a side-channel.
"""
import subprocess
import traceback
import os
import threading
from core.notification import send_notification
from core.monitor import get_system_logger

log = get_system_logger(__name__)

# Prevent duplicate escalations for the same error within cooldown
_last_escalation = {"key": None, "time": 0}
_ESCALATION_COOLDOWN = 300  # 5 minutes between identical escalations
# Cycle failures can repeat — use shorter cooldown so we don't miss them
_CYCLE_COOLDOWN = 120  # 2 minutes for cycle-level failures


def _invoke_hermes(error_type: str, error_detail: str, traceback_str: str = ''):
    """Invoke Hermes agent in non-interactive mode to analyze and fix the error."""
    tb_preview = (traceback_str[:800] + '...') if len(traceback_str) > 800 else traceback_str

    prompt = (
        f"🚨 TradingAgent에서 실제 에러가 발생했다. 자동 분석하고 해결해라.\n\n"
        f"**에러 유형**: {error_type}\n"
        f"**상세**: {error_detail}\n"
        f"**Traceback**:\n```\n{tb_preview}\n```\n\n"
        f"절차:\n"
        f"1. 로그 파일(`/home/ubuntu/TradingAgent/logs/`)에서 관련 에러 확인\n"
        f"2. 원인 분석\n"
        f"3. 가능하면 직접 수정 (patch)\n"
        f"4. 수정 내용 요약을 Discord에 보고\n\n"
        f"주의: 경고(warning)가 아니라 실제 try/except로 잡힌 에러다. 신중하게 분석해라."
    )

    try:
        result = subprocess.run(
            ['hermes', 'chat', '-q', prompt, '-Q',
             '--skills', 'trading-agent-debug',
             '--max-turns', '5',
             '--yolo'],
            capture_output=True,
            text=True,
            timeout=180,  # 3 min max
            env={**os.environ, 'HERMES_NONINTERACTIVE': '1'}
        )
        if result.returncode == 0:
            log.info(f"[ErrorEscalation] Hermes analysis completed for: {error_type}")
            # Report Hermes result to Discord
            summary = result.stdout.strip()
            if summary:
                # Truncate to fit Discord message limit
                if len(summary) > 1800:
                    summary = summary[:1800] + '\n... (truncated)'
                try:
                    send_notification(
                        f"🔧 *Hermes 자동 수정 결과*: `{error_type}`\n```\n{summary}\n```",
                        source="ErrorEscalation",
                        category="error_escalation",
                    )
                except Exception as _ne:
                    log.error(f"[ErrorEscalation] Failed to send Hermes result: {_ne}")
        else:
            err_msg = result.stderr[:300] if result.stderr else 'unknown error'
            log.error(f"[ErrorEscalation] Hermes exited with code {result.returncode}: {err_msg}")
            try:
                send_notification(
                    f"⚠️ *Hermes 분석 실패*: `{error_type}` — exit code {result.returncode}",
                    source="ErrorEscalation",
                    category="error_escalation",
                )
            except Exception:
                pass
    except subprocess.TimeoutExpired:
        log.error(f"[ErrorEscalation] Hermes invocation timed out for: {error_type}")
        try:
            send_notification(
                f"⏱️ *Hermes 분석 타임아웃*: `{error_type}` (180s 초과)",
                source="ErrorEscalation",
                category="error_escalation",
            )
        except Exception:
            pass
    except Exception as e:
        log.error(f"[ErrorEscalation] Failed to invoke Hermes: {e}")


def escalate_error(error_type: str, error_detail: str, traceback_str: str = ''):
    """
    Escalate a critical error by:
    1. Sending a Discord notification
    2. Invoking Hermes agent in a background thread for auto-analysis

    Args:
        error_type: Short label (e.g. '압축 치명적 오류', 'LLM Error Final attempt failed').
        error_detail: Human-readable description.
        traceback_str: Optional traceback string for debugging.
    """
    import time

    # Cooldown: skip if same error_type was escalated recently
    now = time.time()
    cooldown = _CYCLE_COOLDOWN if 'Cycle' in error_type else _ESCALATION_COOLDOWN
    if error_type == _last_escalation["key"] and (now - _last_escalation["time"]) < cooldown:
        log.info(f"[ErrorEscalation] Cooldown active for: {error_type}, skipping")
        return
    _last_escalation["key"] = error_type
    _last_escalation["time"] = now

    # 1) Discord notification (quick, synchronous)
    msg = f"🚨 *Error Escalation*: `{error_type}`\n{error_detail}"
    if traceback_str:
        tb_preview = traceback_str[:500] + ('...' if len(traceback_str) > 500 else '')
        msg += f"\n```{tb_preview}```"

    try:
        send_notification(msg, source="ErrorEscalation", category="error_escalation")
        log.info(f"[ErrorEscalation] Notification sent for: {error_type}")
    except Exception as e:
        log.error(f"[ErrorEscalation] Failed to send notification: {e}")

    # 2) Hermes auto-analysis (background thread — non-blocking)
    thread = threading.Thread(
        target=_invoke_hermes,
        args=(error_type, error_detail, traceback_str),
        daemon=True,
        name=f"hermes-escalation-{error_type[:20]}"
    )
    thread.start()
    log.info(f"[ErrorEscalation] Hermes invocation started (thread) for: {error_type}")
