"""What every chat platform adapter looks like to the rest of the system.

The agents never know which platform a message came from. Adding Slack or Google Chat means
writing one more class with these two members; nothing else changes.
"""

from typing import Protocol

from app.models import User


class Channel(Protocol):
    name: str

    async def send(self, user: User, text: str) -> bool:
        """Deliver a message. Returns False if this user cannot be reached on this channel."""
        ...


async def deliver(channels: list[Channel], user: User, text: str) -> bool:
    """Send through the first channel that can reach the user."""
    for channel in channels:
        if await channel.send(user, text):
            return True
    return False


def split_message(text: str, limit: int) -> list[str]:
    """Break a long message into parts under the platform's size limit, at line breaks where possible."""
    parts = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts
