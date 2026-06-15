"""Chat channels module with plugin architecture."""

from silver_research_bot.channels.base import BaseChannel
from silver_research_bot.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
