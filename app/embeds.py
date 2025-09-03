import datetime as dt
import discord
from .config import COLOR_BLACK, SEPARATOR

def make_embed(title, desc="", color=COLOR_BLACK, fields=None):
    embed = discord.Embed(title=title, description=desc, color=color)
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    embed.timestamp = dt.datetime.utcnow()
    return embed

def build_channel_name(emoji, name):
    return f"{emoji}{SEPARATOR}{name}"[:100]
