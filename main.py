# -*- coding: utf-8 -*-
import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import datetime as dt
import random
import string

# ========================
# 환경설정
# ========================
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # 선택: 길드 동기화. 없으면 글로벌(전파 수 분)
DB_PATH = "licenses.db"

# 고정 ID
ROLE_ID = 1406434529198997586          # 배너 설정 완료 시 부여/회수할 역할
TARGET_ID = 1406431469664206963        # 텍스트 채널 또는 카테고리 ID(자동 판별)

SEPARATOR = "┃"  # 채널명 구분자(굵은 세로바)

# ========================
# 디스코드 설정
# ========================
intents = discord.Intents.default()
intents.members = True  # 역할 부여/회수 위해 필요(개발자 포털에서 멤버 인텐트 허용 필요)
bot = commands.Bot(command_prefix="!", intents=intents)
BTN_STYLE = discord.ButtonStyle.secondary  # 회색 버튼(호환 안정)

# ========================
# 임베드 유틸 (검정색 통일)
# ========================
COLOR_BLACK = discord.Color.from_rgb(0, 0, 0)

def make_embed(title, desc="", color=COLOR_BLACK, fields=None):
    embed = discord.Embed(title=title, description=desc, color=color)
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    embed.timestamp = dt.datetime.utcnow()
    return embed

def build_channel_name(emoji, name):
    ch = f"{emoji}{SEPARATOR}{name}"
    return ch[:100]  # 채널명 최대 100자

# ========================
# DB 초기화
# ========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS license_codes (
        code TEXT PRIMARY KEY,
        type TEXT,
        created_at TEXT,
        used_by INTEGER,
        used_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS licenses (
        user_id INTEGER PRIMARY KEY,
        code TEXT,
        type TEXT,
        activated_at TEXT,
        expires_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS banner_settings (
        user_id INTEGER PRIMARY KEY,
        emoji TEXT,
        banner_name TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS banner_channels (
        user_id INTEGER PRIMARY KEY,
        guild_id INTEGER,
        channel_id INTEGER,
        UNIQUE(user_id, guild_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS license_cleanup (
        user_id INTEGER PRIMARY KEY,
        cleaned_at TEXT
    )
    """)
    conn.commit()
    conn.close()

# ========================
# 공용 로직
# ========================
def generate_license(lic_type):
    random_part = "-".join(
        ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        for _ in range(3)
    )
    return f"Wind-Banner-{random_part}-{lic_type}"

def get_license_row(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT type, activated_at, expires_at FROM licenses WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def has_active_license(user_id):
    row = get_license_row(user_id)
    if not row:
        return False, None, None
    lic_type, _, expires_at = row
    if lic_type == "영구":
        return True, lic_type, None
    if not expires_at:
        return False, lic_type, None
    try:
        exp = dt.datetime.fromisoformat(expires_at)
        return (exp > dt.datetime.utcnow()), lic_type, exp
    except Exception:
        return False, lic_type, None

async def resolve_category_and_announce(guild: discord.Guild):
    """
    TARGET_ID가 카테고리면: (category, None)
    TARGET_ID가 텍스트채널이면: (그 채널의 상위 카테고리, 그 텍스트채널)
    둘 다 아니면: (None, None)
    """
    target = guild.get_channel(TARGET_ID)
    if target is None:
        return None, None
    if isinstance(target, discord.CategoryChannel):
        return target, None
    if isinstance(target, discord.TextChannel):
        return target.category, target
    return None, None

async def send_expire_dm(user, guild_name, expired_at):
    try:
        fields = [
            ("서버", guild_name, True),
            ("만료일", expired_at, True),
            ("조치", "배너 채널 삭제 및 역할 회수", False),
        ]
        embed = make_embed("라이선스 만료 안내", "라이선스가 만료되어 관련 리소스가 정리되었습니다.", COLOR_BLACK, fields)
        await user.send(embed=embed)
    except Exception as e:
        print(f"send_expire_dm error user={user.id}:", e)

async def cleanup_expired_licenses():
    now = dt.datetime.utcnow()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT l.user_id, l.expires_at
        FROM licenses AS l
        LEFT JOIN license_cleanup AS c ON c.user_id = l.user_id
        WHERE l.expires_at IS NOT NULL
          AND l.expires_at <= ?
          AND c.user_id IS NULL
    """, (now.isoformat(),))
    targets = cur.fetchall()

    if not targets:
        conn.close()
        return

    for user_id, expires_at in targets:
        try:
            try:
                expired_at_fmt = dt.datetime.fromisoformat(expires_at).strftime("%Y-%m-%d %H:%M") if expires_at else "-"
            except Exception:
                expired_at_fmt = expires_at or "-"

            # 처리 로그/라이선스 제거
            cur.execute("INSERT OR REPLACE INTO license_cleanup (user_id, cleaned_at) VALUES (?, ?)",
                        (user_id, now.isoformat()))
            cur.execute("DELETE FROM licenses WHERE user_id=?", (user_id,))

            # 배너 채널/역할 정리
            cur.execute("SELECT guild_id, channel_id FROM banner_channels WHERE user_id=?", (user_id,))
            row = cur.fetchone()
            if row:
                guild_id, channel_id = row
                guild = bot.get_guild(int(guild_id)) if guild_id else None
                if guild:
                    member = guild.get_member(int(user_id))
                    role = guild.get_role(ROLE_ID)
                    if member and role:
                        try:
                            await member.remove_roles(role, reason="라이선스 만료")
                        except Exception as e:
                            print(f"remove_roles error user={user_id}:", e)
                    channel = guild.get_channel(int(channel_id)) if channel_id else None
                    if channel:
                        try:
                            await channel.delete(reason="라이선스 만료로 채널 삭제")
                        except Exception as e:
                            print(f"channel.delete error user={user_id}:", e)
                    if member:
                        await send_expire_dm(member, guild.name, expired_at_fmt)
                # 매핑 삭제
                cur.execute("DELETE FROM banner_channels WHERE user_id=?", (user_id,))
            conn.commit()
        except Exception as e:
            print("cleanup_expired_licenses error:", e)
            conn.rollback()
    conn.close()

@tasks.loop(minutes=5)
async def license_cleanup_loop():
    await cleanup_expired_licenses()

@license_cleanup_loop.before_loop
async def before_license_cleanup_loop():
    await bot.wait_until_ready()

# ========================
# 모달: 라이선스 등록
# ========================
class LicenseModal(discord.ui.Modal, title="라이선스 등록"):
    code = discord.ui.TextInput(label="라이선스 코드", placeholder="Wind-Banner-XXXXX-XXXXX-XXXXX-7D")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            code = str(self.code).strip()
            user_id = interaction.user.id
            now = dt.datetime.utcnow()

            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT type, used_by FROM license_codes WHERE code=?", (code,))
            row = cur.fetchone()

            if not row:
                conn.close()
                embed = make_embed("라이선스 등록 실패", "존재하지 않는 코드입니다.")
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            lic_type, used_by = row
            if used_by is not None:
                conn.close()
                embed = make_embed("라이선스 등록 실패", "이미 사용된 코드입니다.")
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            # 기간 설정
            if lic_type == "7D":
                expires = now + dt.timedelta(days=7)
                lic_label = "7일"
            elif lic_type == "30D":
                expires = now + dt.timedelta(days=30)
                lic_label = "30일"
            elif lic_type == "PERM":
                expires = None
                lic_label = "영구"
            else:
                expires = now + dt.timedelta(days=1)
                lic_label = "기타"

            # 등록
            cur.execute(
                "REPLACE INTO licenses (user_id, code, type, activated_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, code, lic_label, now.isoformat(), expires.isoformat() if expires else None)
            )
            cur.execute("UPDATE license_codes SET used_by=?, used_at=? WHERE code=?",
                        (user_id, now.isoformat(), code))
            conn.commit()
            conn.close()

            fields = [
                ("종류", lic_label, True),
                ("등록일", now.strftime("%Y-%m-%d %H:%M"), True),
                ("만료일", expires.strftime("%Y-%m-%d %H:%M") if expires else "해당 없음", True),
            ]
            embed = make_embed("라이선스 등록 완료", "", COLOR_BLACK, fields)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print("LicenseModal on_submit error:", e)
            if not interaction.response.is_done():
                embed = make_embed("오류", "등록 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
                await interaction.response.send_message(embed=embed, ephemeral=True)

# ========================
# 모달: 배너 설정(이모지┃배너명) + 채널 생성/역할 부여
#  - 모달 제출 시에도 라이선스 유효성 재검증(우회 방지)
# ========================
class BannerSettingModal(discord.ui.Modal, title="배너 설정"):
    emoji = discord.ui.TextInput(label="이모지", placeholder="예) EMOJI_0  또는  <:custom:1234567890>", max_length=50, required=True)
    banner_name = discord.ui.TextInput(label="배너명", placeholder="배너에 표시할 이름", max_length=50, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ok, _, _ = has_active_license(interaction.user.id)
            if not ok:
                embed = make_embed("권한 없음", "유효한 라이선스가 있어야 배너를 설정할 수 있어요.")
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            guild = interaction.guild
            if guild is None:
                embed = make_embed("실행 불가", "길드에서만 사용할 수 있어요.")
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            raw_emoji = str(self.emoji).strip()
            name = str(self.banner_name).strip()
            if not name:
                embed = make_embed("입력 오류", "배너명은 비울 수 없어요.")
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            user = interaction.user
            now_iso = dt.datetime.utcnow().isoformat()

            category, announce_ch = await resolve_category_and_announce(guild)
            if category is None:
                embed = make_embed("설정 오류", "지정한 ID에서 카테고리를 찾지 못했어요. 대상이 텍스트 채널이면 상위 카테고리가 있어야 해요.")
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "REPLACE INTO banner_settings (user_id, emoji, banner_name, updated_at) VALUES (?, ?, ?, ?)",
                (user.id, raw_emoji, name, now_iso)
            )
            conn.commit()

            # 기존 채널 조회
            cur.execute("SELECT channel_id FROM banner_channels WHERE user_id=? AND guild_id=?", (user.id, guild.id))
            row = cur.fetchone()

            channel_name = build_channel_name(raw_emoji, name)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            }

            channel = None
            if row:
                ch_id = row[0]
                channel = guild.get_channel(ch_id)
                if channel is None:
                    channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
                    cur.execute("REPLACE INTO banner_channels (user_id, guild_id, channel_id) VALUES (?, ?, ?)",
                                (user.id, guild.id, channel.id))
                    conn.commit()
                else:
                    try:
                        await channel.edit(name=channel_name, category=category, overwrites=overwrites)
                    except Exception as e:
                        print("channel.edit error:", e)
                        await channel.edit(overwrites=overwrites)
            else:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
                cur.execute("INSERT OR REPLACE INTO banner_channels (user_id, guild_id, channel_id) VALUES (?, ?, ?)",
                            (user.id, guild.id, channel.id))
                conn.commit()

            # 역할 부여
            role = guild.get_role(ROLE_ID)
            role_msg = "역할 부여 실패(역할 확인 필요)"
            if role:
                try:
                    await user.add_roles(role, reason="배너 설정 완료")
                    role_msg = f"역할 부여 완료: {role.name}"
                except discord.Forbidden:
                    role_msg = "역할 부여 실패(봇 권한/역할 순서 문제)"
                except discord.HTTPException as e:
                    print("add_roles http error:", e)
                    role_msg = "역할 부여 실패(HTTP 오류)"

            conn.close()

            # 안내 채널 공지(선택)
            if announce_ch and isinstance(announce_ch, discord.TextChannel):
                try:
                    pub = make_embed("배너 채널 생성/갱신", "", COLOR_BLACK,
                                     [("사용자", user.mention, True), ("채널", f"#{channel.name}", True)])
                    await announce_ch.send(embed=pub)
                except Exception as e:
                    print("announce send error:", e)

            fields = [("채널", f"#{channel.name}", True), ("역할 처리", role_msg, True), ("입력값", f"{raw_emoji} / {name}", False)]
            embed = make_embed("배너 설정 저장 완료", "", COLOR_BLACK, fields)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except discord.Forbidden:
            embed = make_embed("권한 부족", "채널/역할을 관리할 권한이 없어요. 봇에 '채널 관리'와 '역할 관리' 권한을 부여해줘.")
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print("BannerSettingModal on_submit error:", e)
            if not interaction.response.is_done():
                embed = make_embed("오류", "저장/채널/역할 처리 중 오류가 발생했어요. 잠시 후 다시 시도해줘.")
                await interaction.response.send_message(embed=embed, ephemeral=True)

# ========================
# 버튼 뷰(등록하기 - 설정하기 - 내정보)
#  - 설정하기: 유효 라이선스 보유자만 가능(콜백에서 즉시 검증)
# ========================
class SimpleBannerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # 퍼시스턴트 뷰

    @discord.ui.button(label="등록하기", style=BTN_STYLE, custom_id="register", row=0)
    async def register_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LicenseModal())

    @discord.ui.button(label="설정하기", style=BTN_STYLE, custom_id="setting", row=0)
    async def setting_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, lic_type, exp = has_active_license(interaction.user.id)
        if not ok:
            msg = "유효한 라이선스가 있어야 설정할 수 있어요."
            fields = []
            if lic_type:
                fields.append(("보유 라이선스", lic_type, True))
            if exp:
                try:
                    fields.append(("만료일", exp.strftime("%Y-%m-%d %H:%M"), True))
                except Exception:
                    pass
            embed = make_embed("설정 불가", msg, COLOR_BLACK, fields)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.send_modal(BannerSettingModal())

    @discord.ui.button(label="내정보", style=BTN_STYLE, custom_id="info", row=0)
    async def info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            row = get_license_row(interaction.user.id)
            if not row:
                embed = make_embed("라이선스 없음", "등록된 라이선스가 없습니다.")
                return await interaction.followup.send(embed=embed, ephemeral=True)

            lic_type, activated_at, expires_at = row
            activated_at_fmt = dt.datetime.fromisoformat(activated_at).strftime("%Y-%m-%d %H:%M")

            if lic_type == "영구":
                fields = [("종류", "영구", True), ("등록일", activated_at_fmt, True)]
                embed = make_embed("라이선스 정보", "", COLOR_BLACK, fields)
            else:
                exp = dt.datetime.fromisoformat(expires_at)
                now = dt.datetime.utcnow()
                remaining = exp - now
                if remaining.total_seconds() <= 0:
                    fields = [("등록일", activated_at_fmt, True), ("만료일", exp.strftime("%Y-%m-%d %H:%M"), True)]
                    embed = make_embed("라이선스 만료", "라이선스가 만료되었습니다.", COLOR_BLACK, fields)
                else:
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    fields = [
                        ("종류", lic_type, True),
                        ("등록일", activated_at_fmt, True),
                        ("만료일", exp.strftime("%Y-%m-%d %H:%M"), True),
                        ("남은 기간", f"{days}일 {hours}시간", True),
                    ]
                    embed = make_embed("라이선스 활성화됨", "", COLOR_BLACK, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print("info_button error:", e)
            embed = make_embed("오류", "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
            await interaction.followup.send(embed=embed, ephemeral=True)

# ========================
# 슬래시 명령어
# ========================
@bot.tree.command(name="배너등록", description="상단 배너 등록하기")
async def 배너등록(interaction: discord.Interaction):
    embed = make_embed("상단 배너 등록하기", "아래 버튼을 사용하세요.", COLOR_BLACK)
    await interaction.response.send_message(embed=embed, view=SimpleBannerView())

# 관리자 전용 코드생성(유지)
@app_commands.command(name="코드생성", description="(관리자 전용) 라이선스 코드를 생성합니다")
@app_commands.describe(기간="라이선스 기간을 선택하세요")
@app_commands.choices(기간=[
    app_commands.Choice(name="7일", value="7D"),
    app_commands.Choice(name="30일", value="30D"),
    app_commands.Choice(name="영구", value="PERM"),
])
async def 코드생성(interaction: discord.Interaction, 기간: app_commands.Choice[str]):
    if not interaction.user.guild_permissions.administrator:
        embed = make_embed("권한 부족", "관리자만 사용할 수 있는 명령어입니다.")
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    try:
        lic_type = 기간.value
        code = generate_license(lic_type)
        now = dt.datetime.utcnow()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO license_codes (code, type, created_at, used_by, used_at) VALUES (?, ?, ?, NULL, NULL)",
            (code, lic_type, now.isoformat())
        )
        conn.commit()
        conn.close()
        label = "7일" if lic_type == "7D" else ("30일" if lic_type == "30D" else "영구")
        fields = [("기간", label, True), ("코드", f"`{code}`", False)]
        embed = make_embed("라이선스 코드 생성", "", COLOR_BLACK, fields)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print("코드생성 error:", e)
        embed = make_embed("오류", "코드 생성 중 오류가 발생했습니다.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

# 트리 등록
if GUILD_ID:
    guild_obj = discord.Object(id=int(GUILD_ID))
    bot.tree.add_command(코드생성, guild=guild_obj)
else:
    bot.tree.add_command(코드생성)

# ========================
# 실행
# ========================
@bot.event
async def on_ready():
    init_db()
    bot.add_view(SimpleBannerView())  # 퍼시스턴트 뷰 등록

    try:
        if GUILD_ID:
            synced = await bot.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            print(f"길드 슬래시 명령 동기화 완료: {len(synced)}개 (GUILD_ID={GUILD_ID})")
        else:
            synced = await bot.tree.sync()
            print(f"글로벌 슬래시 명령 동기화 완료: {len(synced)}개 (전파에 수 분 소요될 수 있음)")
    except Exception as e:
        print(f"슬래시 명령어 동기화 실패: {e}")

    if not license_cleanup_loop.is_running():
        license_cleanup_loop.start()

    print(f"로그인 성공: {bot.user}")

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("환경변수 DISCORD_TOKEN 이 설정되지 않았습니다.")
    bot.run(TOKEN)
