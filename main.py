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

# 환경 변수
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
SECURE_CHANNEL_ID = int(os.getenv("SECURE_CHANNEL_ID", "0") or 0)
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID", "")
REVIEW_WEBHOOK_URL = os.getenv("REVIEW_WEBHOOK_URL", "")
BUYLOG_WEBHOOK_URL = os.getenv("BUYLOG_WEBHOOK_URL", "")

# DB 설정
DB_PATH = os.getenv("DB_PATH", "/data/data.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
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

def init_db():
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    except Exception:
        pass
    Base.metadata.create_all(bind=engine)

def db() -> Session:
    return SessionLocal()

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
    if mode == "set":
        st.total_stock_rbx = max(0, value)
    elif mode == "inc":
        st.total_stock_rbx = max(0, (st.total_stock_rbx or 0) + value)
    elif mode == "dec":
        st.total_stock_rbx = max(0, (st.total_stock_rbx or 0) - value)
    else:
        raise ValueError("mode must be set|inc|dec")
    s.commit()

def emb(title: str, desc: str, color: int = 0x2b6cb0) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=color)

# FastAPI
api = FastAPI()
@api.get("/health")
def health():
    return {"ok": True}

# Discord Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
guild_obj = discord.Object(id=GUILD_ID)

async def safe_ack(inter: discord.Interaction, ephemeral: bool = True):
    try:
        if not inter.response.is_done():
            await inter.response.defer(thinking=False, ephemeral=ephemeral)
    except Exception:
        pass

async def send_embed(inter: discord.Interaction, title: str, desc: str, ephemeral: bool = True, color: int = 0x2b6cb0):
    e = emb(title, desc, color)
    try:
        if inter.response.is_done():
            await inter.followup.send(embed=e, ephemeral=ephemeral)
        else:
            await inter.response.send_message(embed=e, ephemeral=ephemeral)
    except Exception:
        try:
            await inter.followup.send(embed=e, ephemeral=ephemeral)
        except Exception:
            traceback.print_exc()

async def send_webhook(url: str, embed: discord.Embed):
    if not url:
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={"embeds": [embed.to_dict()]})
    except Exception:
        traceback.print_exc()

def is_admin(inter: discord.Interaction) -> bool:
    try:
        if inter.user.guild_permissions.manage_guild:
            return True
        if ADMIN_ROLE_ID:
            rid = int(ADMIN_ROLE_ID)
            return any(getattr(r, "id", None) == rid for r in getattr(inter.user, "roles", []))
    except Exception:
        pass
    return False

# ===== 승인/거절 UI =====
class ApproveRejectView(discord.ui.View):
    def __init__(self, entry_id: int, entry_type: str):
        super().__init__(timeout=None)
        self.entry_id = entry_id
        self.entry_type = entry_type  # "order" or "topup"

    @discord.ui.button(label="승인", style=discord.ButtonStyle.success, custom_id="approve_btn")
    async def approve(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter):
            return await send_embed(inter, "오류", "관리자만 승인 가능", True, 0xff0000)
        s = db()
        try:
            if self.entry_type == "order":
                entry = s.query(Order).filter_by(id=self.entry_id).first()
                if entry and entry.status == "requested":
                    st = get_settings(s)
                    if st.total_stock_rbx >= entry.amount_rbx:
                        set_or_inc_stock(s, entry.amount_rbx, "dec")
                        entry.status = "approved"
                        s.commit()
                        await send_webhook(get_settings(s).buylog_webhook_url, emb("주문 승인", f"{entry.roblox_nick} ({entry.amount_rbx} R$)"))
                        await send_embed(inter, "완료", "주문 승인됨", True)
            elif self.entry_type == "topup":
                entry = s.query(Topup).filter_by(id=self.entry_id).first()
                if entry and entry.status == "waiting":
                    u = ensure_user(s, entry.discord_id)
                    u.balance += entry.amount
                    entry.status = "approved"
                    s.commit()
                    await send_webhook(get_settings(s).review_webhook_url, emb("충전 승인", f"{entry.depositor_name} → {entry.amount}원"))
                    await send_embed(inter, "완료", "충전 승인됨", True)
        finally:
            s.close()

    @discord.ui.button(label="거절", style=discord.ButtonStyle.danger, custom_id="reject_btn")
    async def reject(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter):
            return await send_embed(inter, "오류", "관리자만 거절 가능", True, 0xff0000)
        s = db()
        try:
            if self.entry_type == "order":
                entry = s.query(Order).filter_by(id=self.entry_id).first()
                if entry and entry.status == "requested":
                    entry.status = "rejected"
                    s.commit()
                    await send_embed(inter, "거절됨", "주문 거절됨", True, 0xff0000)
            elif self.entry_type == "topup":
                entry = s.query(Topup).filter_by(id=self.entry_id).first()
                if entry and entry.status == "waiting":
                    entry.status = "rejected"
                    s.commit()
                    await send_embed(inter, "거절됨", "충전 거절됨", True, 0xff0000)
        finally:
            s.close()

# ===== 패널 =====
def panel_embed() -> discord.Embed:
    s = db()
    try:
        return emb("[24] 로벅스 자판기", f"재고 안내\n```{stock_text(s)}```\n아래 버튼 이용")
    finally:
        s.close()

def build_panel_view() -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(custom_id="buy", label="로벅스 구매", style=discord.ButtonStyle.secondary))
    v.add_item(discord.ui.Button(custom_id="topup", label="충전", style=discord.ButtonStyle.secondary))
    v.add_item(discord.ui.Button(custom_id="myinfo", label="내 정보", style=discord.ButtonStyle.secondary))
    return v

# ===== 이벤트 =====
async def sync_guild_commands():
    try:
        cmds = await bot.tree.sync(guild=guild_obj)
        print(f"✅ 길드 동기화 성공: {GUILD_ID} (총 {len(cmds)}개)")
    except Exception as e:
        traceback.print_exc()
        print(f"⚠️ 길드 동기화 실패 → 글로벌 폴백: {e}")
        try:
            cmds = await bot.tree.sync()
            print(f"✅ 글로벌 동기화 성공 (총 {len(cmds)}개)")
        except Exception as e2:
            traceback.print_exc()
            print(f"❌ 최종 동기화 실패: {e2}")

@bot.event
async def on_ready():
    try:
        init_db()
        await sync_guild_commands()
        if not refresh_task.is_running():
            refresh_task.start()
        print(f"✅ 봇 로그인 성공: {bot.user}")
    except Exception:
        traceback.print_exc()

@tasks.loop(seconds=60)
async def refresh_task():
    s = db()
    try:
        st = get_settings(s)
        if not st.panel_channel_id or not st.panel_message_id:
            return
        ch = bot.get_channel(int(st.panel_channel_id)) or await bot.fetch_channel(int(st.panel_channel_id))
        msg = await ch.fetch_message(int(st.panel_message_id))
        await msg.edit(embed=panel_embed(), view=build_panel_view())
    except Exception:
        traceback.print_exc()
    finally:
        s.close()

# ===== 명령어 =====
@bot.tree.command(name="버튼패널", description="로벅스 패널 게시 (관리자 전용)", guild=guild_obj)
@app_commands.check(lambda i: is_admin(i))
async def 버튼패널(inter: discord.Interaction):
    await safe_ack(inter, ephemeral=True)
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
    finally:
        s.close()
    await send_embed(inter, "완료", "패널 게시됨", ephemeral=True)

# ===== 실행 =====
if __name__ == "__main__":
    if DISCORD_TOKEN:
        asyncio.run(bot.start(DISCORD_TOKEN))
    else:
        print("❌ DISCORD_TOKEN 환경 변수 없음")
