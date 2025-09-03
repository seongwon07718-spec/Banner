import sqlite3
import datetime as dt
from discord.ext import tasks
from .config import DB_PATH, ROLE_ID
from .embeds import make_embed
from .config import COLOR_BLACK
import discord

async def send_expire_dm(user, guild_name, expired_at):
    try:
        fields=[("서버",guild_name,True),("만료일",expired_at,True),("조치","배너 채널 삭제 및 역할 회수",False)]
        await user.send(embed=make_embed("라이선스 만료 안내","라이선스가 만료되어 관련 리소스가 정리되었습니다.",COLOR_BLACK,fields))
    except Exception as e:
        print("send_expire_dm:", e)

async def cleanup_expired_licenses(bot):
    now = dt.datetime.utcnow()
    conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
    cur.execute("""
        SELECT l.user_id, l.expires_at
        FROM licenses l LEFT JOIN license_cleanup c ON c.user_id = l.user_id
        WHERE l.expires_at IS NOT NULL AND l.expires_at <= ? AND c.user_id IS NULL
    """, (now.isoformat(),))
    targets = cur.fetchall()
    if not targets:
        conn.close(); return
    for user_id, expires_at in targets:
        try:
            try:
                expired_at_fmt = dt.datetime.fromisoformat(expires_at).strftime("%Y-%m-%d %H:%M") if expires_at else "-"
            except Exception:
                expired_at_fmt = expires_at or "-"
            cur.execute("INSERT OR REPLACE INTO license_cleanup(user_id, cleaned_at) VALUES(?,?)",(user_id, now.isoformat()))
            cur.execute("DELETE FROM licenses WHERE user_id=?", (user_id,))
            cur.execute("SELECT guild_id, channel_id FROM banner_channels WHERE user_id=?", (user_id,))
            row = cur.fetchone()
            if row:
                guild_id, channel_id = row
                guild = bot.get_guild(int(guild_id)) if guild_id else None
                if guild:
                    member = guild.get_member(int(user_id))
                    role = guild.get_role(ROLE_ID)
                    if member and role:
                        try: await member.remove_roles(role, reason="라이선스 만료")
                        except Exception as e: print("remove_roles:", e)
                    channel = guild.get_channel(int(channel_id)) if channel_id else None
                    if channel:
                        try: await channel.delete(reason="라이선스 만료로 채널 삭제")
                        except Exception as e: print("channel.delete:", e)
                    if member:
                        await send_expire_dm(member, guild.name, expired_at_fmt)
                cur.execute("DELETE FROM banner_channels WHERE user_id=?", (user_id,))
            conn.commit()
        except Exception as e:
            print("cleanup_expired_licenses:", e)
            conn.rollback()
    conn.close()

def setup_cleanup_loop(bot):
    @tasks.loop(minutes=5)
    async def loop():
        await cleanup_expired_licenses(bot)

    @loop.before_loop
    async def before():
        await bot.wait_until_ready()

    return loop
