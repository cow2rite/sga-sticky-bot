"""Keep one SGA Discord sticky announcement at the bottom of a channel.

Read the latest rich Discohook embed from a dedicated source webhook.
The editable message content stays inside Discord, not this repository.

Uses short-lived GitHub Actions polling, not a persistent Discord bot process.
"""

from __future__ import annotations

import hashlib
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
STATE_FILE = pathlib.Path(__file__).with_name("state.json")
API_ROOT = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/cow2rite/sga-sticky-bot, 1.0)"


class DiscordError(RuntimeError):
    """Safe-to-log Discord request failure (never includes secret URLs)."""


def discord_request(
    method: str,
    url: str,
    *,
    bot_token: str | None = None,
    data: dict[str, Any] | None = None,
    allow_404: bool = False,
) -> Any:
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
            if exc.code == 429 and attempt < 2:
                wait = min(float(err.get("retry_after", 2)), 15)
                time.sleep(max(wait, 1))
                continue
            code = err.get("code", "unknown")
            msg = err.get("message", "Discord API request failed")
            raise DiscordError(f"Discord API HTTP {exc.code}, code {code}: {msg}") from None
        except urllib.error.URLError:
            # URLError can include sensitive webhook tokens; do not log it.
            raise DiscordError("Discord connection failed (network/timeout)") from None
    raise DiscordError("Discord API retry limit reached")


def normalize_webhook(raw_url: str) -> str:
    """Accept official Discord webhook URLs only; discard query parameters."""
    parsed = urllib.parse.urlsplit(raw_url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"discord.com", "discordapp.com"}:
        raise ValueError("Webhook URL must be an official Discord webhook URL")
    if not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+", parsed.path):
        raise ValueError("Webhook URL format is invalid")
    return "https://discord.com" + parsed.path


def clean_embed(embed: dict[str, Any]) -> dict[str, Any]:
    """Retain only writable embed fields; remove Discord response-only fields."""
    if embed.get("type", "rich") != "rich":
        raise ValueError("Only rich embeds from Discohook can be copied")
    clean = {
        key: embed[key]
        for key in ("title", "description", "url", "timestamp", "color")
        if embed.get(key) is not None
    }
    for key, allowed in (
        ("footer", ("text", "icon_url")),
        ("author", ("name", "url", "icon_url")),
        ("image", ("url",)),
        ("thumbnail", ("url",)),
    ):
        if isinstance(embed.get(key), dict):
            values = {
                prop: embed[key][prop]
                for prop in allowed
                if embed[key].get(prop) is not None
            }
            if values:
                clean[key] = values
    if isinstance(embed.get("fields"), list):
        clean["fields"] = [
            {k: field[k] for k in ("name", "value", "inline") if k in field}
            for field in embed["fields"]
            if isinstance(field, dict)
        ]
    return clean


def source_payload(bot_token: str, raw_source_url: str) -> dict[str, Any]:
    """Fetch the latest Discohook-created message via the source webhook token.

    Bot reads metadata only to locate the message; source webhook's own token
    reads the full original. MESSAGE_CONTENT privileged intent is unnecessary.
    """
    source_url = normalize_webhook(raw_source_url)
    source_webhook = discord_request("GET", source_url)
    if not isinstance(source_webhook, dict):
        raise DiscordError("Source webhook could not be loaded")
    source_channel = str(source_webhook.get("channel_id") or "")
    source_webhook_id = str(source_webhook.get("id") or "")
    if not source_channel or not source_webhook_id:
        raise ValueError("Source webhook does not have a valid channel")
    if source_channel == TARGET_CHANNEL_ID:
        raise ValueError("Source webhook must belong to a different channel")

    messages = discord_request(
        "GET", f"{API_ROOT}/channels/{source_channel}/messages?limit=100",
        bot_token=bot_token,
    )
    if not isinstance(messages, list):
        raise DiscordError("Could not list original source channel messages")
    source_message = next(
        (m for m in messages if str(m.get("webhook_id") or "") == source_webhook_id),
        None,
    )
    if not source_message:
        raise ValueError(
            "No Discohook original found in source channel. Check that the bot "
            "has View Channel and Read Message History permissions, and that "
            "Discohook posted at least one message using SOURCE_WEBHOOK_URL."
        )

    original = discord_request("GET", f"{source_url}/messages/{source_message['id']}")
    if not isinstance(original, dict):
        raise DiscordError("Could not read original Discohook webhook message")
    embeds = original.get("embeds") or []
    if not isinstance(embeds, list) or not 1 <= len(embeds) <= 10:
        raise ValueError("Discohook original must contain 1 to 10 rich embeds")
    payload: dict[str, Any] = {
        "embeds": [clean_embed(embed) for embed in embeds],
        "allowed_mentions": {"parse": []},
    }
    if original.get("content"):
        payload["content"] = original["content"]
    return payload


def get_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {"sticky_message_id": "", "content_hash": ""}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "sticky_message_id": str(state.get("sticky_message_id") or ""),
        "content_hash": str(state.get("content_hash") or ""),
    }


def save_state(message_id: str, content_hash: str) -> None:
    STATE_FILE.write_text(
        json.dumps(
            {"sticky_message_id": message_id, "content_hash": content_hash},
            ensure_ascii=False, indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def run() -> str:
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    target_webhook_raw = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    source_webhook_raw = os.environ.get("SOURCE_WEBHOOK_URL", "").strip()
    if not bot_token or not target_webhook_raw or not source_webhook_raw:
        raise ValueError(
            "Missing DISCORD_BOT_TOKEN, DISCORD_WEBHOOK_URL, or SOURCE_WEBHOOK_URL"
        )
    target_webhook_url = normalize_webhook(target_webhook_raw)
    webhook = discord_request("GET", target_webhook_url)
    if not isinstance(webhook, dict) or str(webhook.get("channel_id")) != TARGET_CHANNEL_ID:
        raise ValueError("Target webhook's configured channel does not match target channel")

    payload = source_payload(bot_token, source_webhook_raw)

    content_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    channel = discord_request(
        "GET", f"{API_ROOT}/channels/{TARGET_CHANNEL_ID}", bot_token=bot_token
    )
    if not isinstance(channel, dict):
        raise DiscordError("Could not read target channel")
    last_message_id = str(channel.get("last_message_id") or "")
    state = get_state()
    previous_id = state["sticky_message_id"]

    if previous_id and state["content_hash"] == content_hash:
        if not last_message_id or int(last_message_id) <= int(previous_id):
            # Also recover when someone manually deleted the managed sticky.
            existing = discord_request(
                "GET", f"{API_ROOT}/channels/{TARGET_CHANNEL_ID}/messages/{previous_id}",
                bot_token=bot_token, allow_404=True,
            )
            if existing is not None:
                print("No newer chat or Discohook edit; sticky unchanged")
                return "unchanged"

    published = discord_request(
        "POST", target_webhook_url + "?wait=true", data=payload,
    )
    if not isinstance(published, dict) or not published.get("id"):
        raise DiscordError("Target webhook did not return the new message ID")
    new_id = str(published["id"])
    save_state(new_id, content_hash)

    # Only delete the exact earlier managed message, never the source original.
    if previous_id:
        try:
            discord_request(
                "DELETE", f"{target_webhook_url}/messages/{previous_id}",
                allow_404=True,
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
