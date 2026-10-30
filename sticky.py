"""Repost a single Discord webhook embed only when new channel activity occurs.

Works as a short-lived GitHub Actions job. No Gateway connection or
MESSAGE_CONTENT intent is required: reads only the channel's last_message_id.
Sensitive credentials and the private announcement content belong in Actions
repository secrets, not in this public repository.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TARGET_CHANNEL_ID = "1518960183500476591"
EMBED_TITLE = "📚 코치 운영 가이드 바로가기"
EMBED_COLOR = int("E53935", 16)
EMBED_FOOTER = (
    "학생들의 꿈과 성장을 위해 늘 함께해 주시는 모든 코치님들께 진심으로 감사드립니다."
)
STATE_FILE = pathlib.Path(__file__).with_name("state.json")
API_ROOT = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/cow2rite/sga-sticky-bot, 1.0)"


class DiscordError(RuntimeError):
    """Safe-to-log Discord request failure (never includes token URLs)."""


def discord_request(
    method: str,
    url: str,
    *,
    bot_token: str | None = None,
    data: dict[str, Any] | None = None,
    allow_404: bool = False,
) -> dict[str, Any] | None:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if bot_token:
        headers["Authorization"] = "Bot " + bot_token
    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")

    for attempt in range(3):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_404:
                return None
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                err = json.loads(raw)
            except json.JSONDecodeError:
                err = {}
            # A rate-limited request was not executed, so retry is safe.
            if exc.code == 429 and attempt < 2:
                wait = min(float(err.get("retry_after", 2)), 15)
                time.sleep(max(wait, 1))
                continue
            code = err.get("code", "unknown")
            msg = err.get("message", "Discord API request failed")
            raise DiscordError(f"Discord API HTTP {exc.code}, code {code}: {msg}") from None
        except urllib.error.URLError:
            # Exceptions may contain the secret webhook URL: do not log it.
            raise DiscordError("Discord connection failed (network/timeout)") from None
    raise DiscordError("Discord API retry limit reached")


def normalize_webhook(raw_url: str) -> str:
    """Allow official Discord webhook URLs only; strip query params."""
    parsed = urllib.parse.urlsplit(raw_url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"discord.com", "discordapp.com"}:
        raise ValueError("Webhook URL must be an official https://discord.com/api/webhooks/... URL")
    if not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+", parsed.path):
        raise ValueError("Webhook URL format is invalid")
    return "https://discord.com" + parsed.path


def get_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {"sticky_message_id": ""}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"sticky_message_id": str(state.get("sticky_message_id") or "")}


def save_state(message_id: str) -> None:
    STATE_FILE.write_text(
        json.dumps({"sticky_message_id": message_id}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run() -> str:
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    description = os.environ.get("STICKY_DESCRIPTION", "").strip()
    if not bot_token or not webhook_url or not description:
        raise ValueError(
            "Missing Actions secrets: DISCORD_BOT_TOKEN, DISCORD_WEBHOOK_URL, STICKY_DESCRIPTION"
        )
    if len(description) > 4096:
        raise ValueError("STICKY_DESCRIPTION exceeds the Discord embed description limit (4096)")
    webhook_url = normalize_webhook(webhook_url)

    # Fail before publishing if the selected webhook belongs to another channel.
    webhook = discord_request("GET", webhook_url)
    if not webhook or str(webhook.get("channel_id")) != TARGET_CHANNEL_ID:
        raise ValueError("Webhook's configured channel does not match the target channel")

    channel = discord_request(
        "GET", f"{API_ROOT}/channels/{TARGET_CHANNEL_ID}", bot_token=bot_token
    )
    if channel is None:
        raise DiscordError("Could not read target channel")
    last_message_id = str(channel.get("last_message_id") or "")
    previous_id = get_state()["sticky_message_id"]

    if previous_id:
        if not last_message_id or int(last_message_id) <= int(previous_id):
            print("No new messages; sticky unchanged")
            return "unchanged"

    embed = {
        "title": EMBED_TITLE,
        "description": description,
        "color": EMBED_COLOR,
        "footer": {"text": EMBED_FOOTER},
    }
    new_sticky = discord_request(
        "POST", webhook_url + "?wait=true", data={
            "embeds": [embed], "allowed_mentions": {"parse": []},
        }
    )
    if not new_sticky or not new_sticky.get("id"):
        raise DiscordError("Webhook did not return a message ID (check wait=true)")
    new_id = str(new_sticky["id"])
    save_state(new_id)

    # Remove only a message ID created in an earlier successful run by this program.
    # Never search or delete other Discord announcements.
    if previous_id:
        try:
            discord_request(
                "DELETE", f"{webhook_url}/messages/{previous_id}", allow_404=True
            )
        except DiscordError as exc:
            print(f"Warning: previous managed sticky could not be deleted: {exc}")

    print("Sticky message posted; new message ID stored")
    return "posted"


if __name__ == "__main__":
    try:
        run()
    except (ValueError, DiscordError, json.JSONDecodeError) as error:
        print(f"SGA sticky error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
