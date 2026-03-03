import os
import requests
from core.monitor import get_system_logger

log = get_system_logger(__name__)

def send_notification(message: str, channel: str = "#stock_report"):
    """
    Sends a message to a Discord or Slack webhook URL.
    Checks environment configurations for the endpoint.
    """
    # Assuming Discord or Slack webhook via .env
    webhook_url = os.getenv("SLACK_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL")
    
    if not webhook_url:
        log.warning(f"Webhook URL not configured. Skipping notification: {message}")
        return

    try:
        # Simple payload detection based on URL
        if "discord.com" in webhook_url:
            payload = {"content": message}
        else:
            payload = {"channel": channel, "text": message}
            
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
        log.info(f"Notification sent: {message}")

    except Exception as e:
        log.error(f"Failed to send webhook notification: {e}")
