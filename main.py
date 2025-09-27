import os, sys, asyncio, traceback, logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from fastapi import FastAPI
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.sql import func
import aiohttp

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
if not os.getenv("BOOT_LOG_ONCE"):
    print(">>> main.py booting...")
    os.environ["BOOT_LOG_ONCE"] = "1"

# ===== 환경 변수 =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
SECURE_CHANNEL_ID = int(os.getenv("SECURE_CHANNEL_ID", "0") or 0)
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID", "")
REVIEW_WEBHOOK_URL = os.getenv("REVIEW_WEBHOOK_URL", "")
BUYLOG_WEBHOOK_URL = os.getenv("BUYLOG_WEBHOOK_URL", "")
DB_PATH = os.getenv("DB_PATH", "/data/data.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# ===== DB =====
Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class User(Base):
    __tablename__ = "users"
    discord_id = Column(String, primary_key=True)
    balance = Column(Integer, default=0)
    total_spent = Column(Integer, default=0)
    tier = Column(String, default="브론즈")
    created_at = Column(DateTime, server_default=func.now())

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_id = Column(String, index=True)
    roblox_nick = Column(String)
    method = Column(String)
    amount_rbx = Column(Integer, default=0)
    status = Column(String, default="requested")
    created_at = Column(DateTime, server_default=func.now())

class Topup(Base):
    __tablename__ = "topups"
    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_id = Column(String, index=True)
    depositor_name = Column(String)
    amount = Column(Integer, default=0)
    status = Column(String, default="waiting")
    created_at = Column(DateTime, server_default=func.now())

class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    bank_name = Column(String)
    account_number = Column(String)
    holder = Column(String)
    panel_channel_id = Column(String)
    panel_message_id = Column(String)
    secure_channel_id = Column(String)
    review_webhook_url = Column(Text)
    buylog_webhook_url = Column(Text)
    total_stock_rbx = Column(Integer, default=0)

# ===== DB 초기화 =====
def init_db():
    try: os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    except: pass
    Base.metadata.create_all(bind=engine)

def db() -> Session: return SessionLocal()

def ensure_user(s: Session, did: str) -> User:
    u = s.get(User, did)
    if not u:
        u = User(discord_id=did)
        s.add(u); s.commit(); s.refresh(u)
    return u

def get_settings(s: Session) -> Setting:
    x = s.get(Setting, 1)
    if not x:
        x = Setting(id=1, bank_name="미설정", account_number="미설정", holder="미설정", total_stock_rbx=0)
        s.add(x); s.commit(); s.refresh(x)
    return x

def stock_text(s: Session) -> str:
    st = get_settings(s)
    return f"총 재고: {st.total_stock_rbx} R$"

def set_or_inc_stock(s: Session, value: int, mode: str = "set"):
    st = get_settings(s)
    if mode == "set": st.total_stock_rbx = max(0, value)
    elif mode == "inc": st.total_stock_rbx = max(0, (st.total_stock_rbx or 0) + value)
    elif mode == "dec": st.total_stock_rbx = max(0, (st.total_stock_rbx or 0) - value)
    s.commit()

def emb(title: str, desc: str, color: int = 0x2b6cb0) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=color)

async def send_webhook(url: str, content: str):
    if not url: return
    async with aiohttp.ClientSession() as session:
        try: await session.post(url, json={"content": content})
        except: traceback.print_exc()

# ===== FastAPI =====
api = FastAPI()
@api.get("/health")
def health(): return {"ok": True}

# ===== Discord =====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
guild_obj = discord.Object(id=GUILD_ID)

def is_admin(inter: discord.Interaction) -> bool:
    try:
        if inter.user.guild_permissions.manage_guild: return True
        if ADMIN_ROLE_ID:
            rid = int(ADMIN_ROLE_ID)
            return any(getattr(r, "id", None)==rid for r in getattr(inter.user, "roles", []))
    except: pass
    return False

async def safe_ack(inter: discord.Interaction, ephemeral: bool = True):
    try:
        if not inter.response.is_done(): await inter.response.defer(thinking=False, ephemeral=ephemeral)
    except: pass

async def send_embed(inter: discord.Interaction, title: str, desc: str, ephemeral: bool = True, color: int = 0x2b6cb0):
    e = emb(title, desc, color)
    try:
        if inter.response.is_done(): await inter.followup.send(embed=e, ephemeral=ephemeral)
        else: await inter.response.send_message(embed=e, ephemeral=ephemeral)
    except:
        try: await inter.followup.send(embed=e, ephemeral=ephemeral)
        except: traceback.print_exc()

# ===== 패널 =====
def panel_embed():
    s = db()
    try:
        return emb("[ 24 ] 로벅스 자판기", f"재고 안내\n```{stock_text(s)}```\n아래 버튼으로 이용해줘.")
    finally: s.close()

def build_panel_view():
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(label="로벅스 구매", style=discord.ButtonStyle.secondary, custom_id="buy"))
    v.add_item(discord.ui.Button(label="충전", style=discord.ButtonStyle.secondary, custom_id="topup"))
    v.add_item(discord.ui.Button(label="내 정보", style=discord.ButtonStyle.secondary, custom_id="myinfo"))
    return v

# ===== 자동 패널 갱신 =====
@tasks.loop(seconds=60)
async def refresh_task():
    s = db()
    try:
        st = get_settings(s)
        if not st.panel_channel_id or not st.panel_message_id: return
        ch = bot.get_channel(int(st.panel_channel_id)) or await bot.fetch_channel(int(st.panel_channel_id))
        msg = await ch.fetch_message(int(st.panel_message_id))
        await msg.edit(embed=panel_embed(), view=build_panel_view())
    finally: s.close()

# ===== 슬래시 명령어 =====
@bot.tree.command(name="버튼패널", description="로벅스 패널 게시 (관리자 전용)", guild=guild_obj)
@app_commands.check(lambda i: is_admin(i))
async def 버튼패널(inter: discord.Interaction):
    await safe_ack(inter)
    msg = await inter.channel.send(embed=panel_embed(), view=build_panel_view())
    s = db()
    try:
        st = get_settings(s)
        st.panel_channel_id = str(msg.channel.id)
        st.panel_message_id = str(msg.id)
        if SECURE_CHANNEL_ID and not st.secure_channel_id:
            st.secure_channel_id = str(SECURE_CHANNEL_ID)
        if REVIEW_WEBHOOK_URL and not st.review_webhook_url:
            st.review_webhook_url = REVIEW_WEBHOOK_URL
        if BUYLOG_WEBHOOK_URL and not st.buylog_webhook_url:
            st.buylog_webhook_url = BUYLOG_WEBHOOK_URL
        s.commit()
    finally: s.close()
    await send_embed(inter, "완료", "패널이 게시됐어.")

@bot.tree.command(name="재고추가", description="총 재고 설정/증가/감소 (관리자 전용)", guild=guild_obj)
@app_commands.describe(수량="변경 수량(R$)", 모드="set=설정, inc=증가, dec=감소")
@app_commands.choices(모드=[
    app_commands.Choice(name="설정", value="set"),
    app_commands.Choice(name="증가", value="inc"),
    app_commands.Choice(name="감소", value="dec"),
])
@app_commands.check(lambda i: is_admin(i))
async def 재고추가(inter: discord.Interaction, 수량: int, 모드: app_commands.Choice[str]):
    await safe_ack(inter)
    s = db()
    try:
        set_or_inc_stock(s, 수량, mode=모드.value)
        st = get_settings(s)
        if st.panel_channel_id and st.panel_message_id:
            ch = bot.get_channel(int(st.panel_channel_id)) or await bot.fetch_channel(int(st.panel_channel_id))
            msg = await ch.fetch_message(int(st.panel_message_id))
            await msg.edit(embed=panel_embed(), view=build_panel_view())
        cur = stock_text(s)
    finally: s.close()
    await send_embed(inter, "재고 변경", f"모드: {모드.value}\n수량: {수량}\n현재 {cur}")

@bot.tree.command(name="유저정보", description="특정 유저 정보 조회 (관리자 전용)", guild=guild_obj)
@app_commands.describe(유저="대상 유저")
@app_commands.check(lambda i: is_admin(i))
async def 유저정보(inter: discord.Interaction, 유저: discord.User):
    await safe_ack(inter)
    s = db()
    try:
        u = ensure_user(s, str(유저.id))
        desc = f"유저: {유저.mention}\n잔액: {u.balance:,}원\n누적: {u.total_spent:,}원\n등급: {u.tier}"
    finally: s.close()
    await send_embed(inter, "유저 정보", desc)

@bot.tree.command(name="잔액차감", description="유저 잔액 차감 (관리자 전용)", guild=guild_obj)
@app_commands.describe(유저="대상 유저", 차감금액="차감할 금액(원)")
@app_commands.check(lambda i: is_admin(i))
async def 잔액차감(inter: discord.Interaction, 유저: discord.User, 차감금액: int):
    await safe_ack(inter)
    if 차감금액 <= 0: return await send_embed(inter, "오류", "차감금액은 1 이상이어야 해.", ephemeral=True, color=0xff5555)
    s = db()
    try:
        u = ensure_user(s, str(유저.id))
        if u.balance < 차감금액:
            return await send_embed(inter, "오류", f"유저 잔액 부족. 현재 {u.balance:,}원", ephemeral=True, color=0xff5555)
        u.balance -= 차감금액
        s.commit()
        desc = f"유저: {유저.mention}\n차감: {차감금액:,}원\n잔액: {u.balance:,}원"
    finally: s.close()
    await send_embed(inter, "잔액 차감 완료", desc)

# ===== on_ready =====
@bot.event
async def on_ready():
    try:
        init_db()
        await bot.tree.sync(guild=guild_obj)
        if not refresh_task.is_running(): refresh_task.start()
        print(f"✅ 봇 로그인 성공: {bot.user}")
    except Exception: traceback.print_exc()

# ===== 봇 + FastAPI 동시 실행 =====
async def start_bot_and_api():
    bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    import uvicorn
    api_task = asyncio.create_task(uvicorn.run(api, host="0.0.0.0", port=8000))
    await asyncio.gather(bot_task, api_task)

if __name__ == "__main__":
    if not DISCORD_TOKEN: print("❌ DISCORD_TOKEN 환경 변수 없음")
    else: asyncio.run(start_bot_and_api())
