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
        headers = {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json;charset=utf-8"}

        def _resolve_channel_id(name_or_id: str) -> str:
            chan = name_or_id.lstrip("#") if name_or_id.startswith("#") else name_or_id
            if chan.startswith("C") or chan.startswith("G"):
                return chan
            next_cursor = None
            while True:
                params = {"types": "public_channel,private_channel", "limit": 200}
                if next_cursor:
                    params["cursor"] = next_cursor
                resp = requests.get(
                    "https://slack.com/api/conversations.list",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    params=params,
                    timeout=10,
                )
                resp.raise_for_status()
                data_list = resp.json()
                if not data_list.get("ok"):
                    break
                for ch in data_list.get("channels", []):
                    if ch.get("name") == chan:
                        return ch.get("id")
                next_cursor = data_list.get("response_metadata", {}).get("next_cursor")
                if not next_cursor:
                    break
            return chan

        def _try_join(channel_id: str):
            try:
                join_resp = requests.post(
                    "https://slack.com/api/conversations.join",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    data={"channel": channel_id},
                    timeout=10,
                )
                join_resp.raise_for_status()
            except Exception:
                pass

        # 1) 사전 업로드 URL 발급
        file_size = os.path.getsize(log_file_path)
        filename = os.path.basename(log_file_path)
        preupload = requests.post(
            "https://slack.com/api/files.getUploadURLExternal",
            headers=headers,
            json={"filename": filename, "length": file_size},
            timeout=15,
        )
        preupload.raise_for_status()
        pre = preupload.json()
        if not pre.get("ok"):
            raise Exception(f"getUploadURLExternal failed: {pre.get('error')}")
        upload_url = pre.get("upload_url")
        file_id = pre.get("file_id") or pre.get("id")
        if not upload_url or not file_id:
            raise Exception("Invalid preupload response: missing upload_url or file_id")

        # 2) 파일 바이트 업로드 (PUT)
        with open(log_file_path, "rb") as f:
            put_resp = requests.put(upload_url, data=f, headers={"Content-Type": "application/octet-stream"}, timeout=60)
            put_resp.raise_for_status()

        # 3) 채널 후보 목록: 지정 채널 -> #stock_report
        candidates = [channel, "#stock_report"]
        last_error = None
        for cand in candidates:
            try:
                channel_id = _resolve_channel_id(cand)
                _try_join(channel_id)
                complete = requests.post(
                    "https://slack.com/api/files.completeUploadExternal",
                    headers=headers,
                    json={
                        "files": [{"id": file_id, "title": filename}],
                        "channel_id": channel_id,
                        "initial_comment": f"데일리 트레이딩 로그 파일 ({datetime.datetime.now().strftime('%Y-%m-%d')})",
                    },
                    timeout=15,
                )
                complete.raise_for_status()
                comp = complete.json()
                if comp.get("ok"):
                    log.info(f"로그 파일을 Slack 채널({cand})으로 성공적으로 전송했습니다.")
                    return
                else:
                    last_error = comp.get("error")
            except Exception as e:
                last_error = str(e)
                continue

        raise Exception(f"Slack external upload failed for all channels. last_error={last_error}")
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
