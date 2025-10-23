import datetime
import os

import requests

# 내부 모듈 import 경로 수정
from .. import config
from . import logger  # logger 모듈 import
from . import translator

log = logger.get_logger(__name__)


def send_notification(message: str, channel: str = "#stock_report"):
    """
    주어진 메시지를 (필요 시 한글로 번역하여) 지정된 Slack 채널로 보냅니다.
    """
    webhook_url = config.SLACK_WEBHOOK_URL

    try:
        if not webhook_url or "YOUR/SLACK/WEBHOOK_URL" in webhook_url:
            log.warning(
                f"Slack Webhook URL is not configured. Skipping notification for message: {message}"
            )
            return

        # --- ✨ 메시지 전송 전, 번역 함수를 호출하는 로직 추가 ✨ ---
        translated_message = translator.translate_to_korean_if_needed(message)

        payload = {"channel": channel, "text": translated_message}
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()

        log.info(f"Notification sent to Slack: {translated_message}")

    except Exception as e:
        log.error(f"Failed to send Slack notification: {e}")


def send_log_file_to_slack(channel: str = "#stock_report"):
    """
    거래 로그 파일을 지정된 Slack 채널로 업로드하고, 성공 시 파일을 삭제합니다.
    (Slack Bot Token과 files:write 권한 필요)
    """
    log_file_path = os.path.join(config.LOG_DIR, "trading_agent.log")
    bot_token = config.SLACK_BOT_TOKEN

    if not os.path.exists(log_file_path):
        log.info("전송할 로그 파일이 없습니다.")
        return

    if not bot_token or "xoxb-" not in bot_token:
        log.warning("Slack Bot Token이 설정되지 않아 로그 파일을 전송할 수 없습니다.")
        return

    try:
        # files.upload API 엔드포인트
        url = "https://slack.com/api/files.upload"

        # 헤더에 Bot Token 추가
        headers = {"Authorization": f"Bearer {bot_token}"}

        # multipart/form-data 페이로드 구성
        files = {
            "file": (
                os.path.basename(log_file_path),
                open(log_file_path, "rb"),
                "text/plain",
            )
        }
        data = {
            "channels": channel,
            "initial_comment": f"데일리 트레이딩 로그 파일 ({datetime.datetime.now().strftime('%Y-%m-%d')})",
            "title": os.path.basename(log_file_path),
        }

        # 파일 업로드 요청
        response = requests.post(
            url, headers=headers, files=files, data=data, timeout=30
        )
        response.raise_for_status()
        response_json = response.json()

        if response_json.get("ok"):
            log.info(f"로그 파일을 Slack 채널({channel})으로 성공적으로 전송했습니다.")
            # 전송 성공 후 로그 파일 삭제
            # 파일을 닫은 후에 삭제하기 위해 with 블록 밖으로 이동
        else:
            raise Exception(
                f"Slack API returned an error: {response_json.get('error')}"
            )

    except Exception as e:
        log.error(f"Slack으로 로그 파일을 보내는 데 실패했습니다: {e}")
        # 실패 시에는 파일을 삭제하지 않음
        return  # 함수 종료

    # try 블록이 성공적으로 완료되었을 때만 파일 삭제
    try:
        os.remove(log_file_path)
        log.info(f"로컬 로그 파일({log_file_path})을 삭제했습니다.")
    except Exception as e:
        log.error(f"로그 파일 삭제에 실패했습니다: {e}")
