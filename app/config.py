import os
import discord

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # 선택

ROLE_ID = 1406434529198997586
TARGET_ID = 1406431469664206963

DB_PATH = "licenses.db"
SEPARATOR = "┃"

COLOR_BLACK = discord.Color.from_rgb(0, 0, 0)
BTN_STYLE = discord.ButtonStyle.secondary
