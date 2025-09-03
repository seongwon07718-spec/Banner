# -*- coding: utf-8 -*-
import os
import discord
from discord.ext import commands
from discord import app_commands

from app.config import DISCORD_TOKEN, GUILD_ID, COLOR_BLACK
from app.db import init_db
from app.embeds import make_embed
from app.views import SimpleBannerView
from app.licenses import generate_license
from app.cleanup import setup_cleanup_loop

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 슬래시: 배너등록
@bot.tree.command(name="배너등록", description="상단 배너 등록하기")
async def 배너등록(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=make_embed("상단 배너 등록하기","아래 버튼을 사용하세요.", COLOR_BLACK),
        view=SimpleBannerView()
    )

# 슬래시: 코드생성(관리자)
@app_commands.command(name="코드생성", description="(관리자 전용) 라이선스 코드를 생성합니다")
@app_commands.describe(기간="라이선스 기간을 선택하세요")
@app_commands.choices(기간=[
    app_commands.Choice(name="7일", value="7D"),
    app_commands.Choice(name="30일", value="30D"),
    app_commands.Choice(name="영구", value="PERM"),
])
async def 코드생성(interaction: discord.Interaction, 기간: app_commands.Choice[str]):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            embed=make_embed("권한 부족","관리자만 사용할 수 있는 명령어입니다."), ephemeral=True
        )
    try:
        lic_type = 기간.value
        code = generate_license(lic_type)
        # DB 저장
        import sqlite3, datetime as dt
        from app.config import DB_PATH
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO license_codes(code,type,created_at,used_by,used_at) VALUES(?,?,?,NULL,NULL)",
            (code, lic_type, dt.datetime.utcnow().isoformat())
        )
        conn.commit(); conn.close()
        label = "7일" if lic_type=="7D" else ("30일" if lic_type=="30D" else "영구")
        fields=[("기간",label,True),("코드",f"`{code}`",False)]
        await interaction.response.send_message(embed=make_embed("라이선스 코드 생성","",COLOR_BLACK,fields), ephemeral=True)
    except Exception as e:
        print("코드생성:", e)
        await interaction.response.send_message(embed=make_embed("오류","코드 생성 중 오류가 발생했습니다."), ephemeral=True)

# 트리 등록
if GUILD_ID:
    guild_obj = discord.Object(id=int(GUILD_ID))
    bot.tree.add_command(코드생성, guild=guild_obj)
else:
    bot.tree.add_command(코드생성)

@bot.event
async def on_ready():
    init_db()
    bot.add_view(SimpleBannerView())  # 퍼시스턴트 뷰
    try:
        if GUILD_ID:
            synced = await bot.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            print(f"길드 슬래시 동기화: {len(synced)}개")
        else:
            synced = await bot.tree.sync()
            print(f"글로벌 슬래시 동기화: {len(synced)}개(전파 수 분 소요)")
    except Exception as e:
        print("슬래시 동기화 실패:", e)

    cleanup_loop = setup_cleanup_loop(bot)
    if not cleanup_loop.is_running():
        cleanup_loop.start()

    print(f"로그인 성공: {bot.user}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("환경변수 DISCORD_TOKEN 이 설정되지 않았습니다.")
    bot.run(DISCORD_TOKEN)
