import requests
from core.monitor import get_system_logger
from core.translator import translate_to_korean_if_needed
import config

log = get_system_logger(__name__)

def send_notification(message: str, channel: str = "#stock_report"):
    """
    Sends a message to a Discord or Slack webhook URL.
    Checks environment configurations for the endpoint.
    Translates English to Korean for Slack notification (user convenience).
    """
    # Try config first (ensures consistent loading via dotenv), fallback to os.getenv for Discord
    webhook_url = config.SLACK_WEBHOOK_URL or os.getenv("DISCORD_WEBHOOK_URL")

    if not webhook_url:
        log.warning(f"Webhook URL not configured. Skipping notification: {message}")
        return

    try:
        # Translate English to Korean for Slack notification
        # Store original message for fallback on translation failure
        original_message = message
        if "slack.com" in webhook_url:
            translated = translate_to_korean_if_needed(message)
            # Only use translation if it's different from original and not an error message
            if translated and translated != original_message and not translated.startswith("번역 실패"):
                message = translated
            else:
                # Translation failed - use original English message instead of error
                message = original_message

        # Simple payload detection based on URL
        if "discord.com" in webhook_url:
            payload = {"content": message}
        else:
            payload = {"channel": channel, "text": message}

        # Add proper headers to ensure UTF-8 encoding
        headers = {
            "Content-Type": "application/json; charset=utf-8"
        }
        response = requests.post(webhook_url, json=payload, headers=headers, timeout=5)
        response.raise_for_status()
        log.info(f"Notification sent: {message}")
        return True  # Return True on success

    except Exception as e:
        log.error(f"Failed to send webhook notification: {e}")
        return False
