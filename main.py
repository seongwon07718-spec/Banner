import os, sys, asyncio, traceback, logging
from threading import Thread
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from fastapi import FastAPI
import uvicorn
import aiohttp

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.sql import func

# ===== 로깅 =====
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
if not os.getenv("BOOT_LOG_ONCE"):
    print(">>> main.py booting...")
    os.environ["BOOT_LOG_ONCE"] = "1"

# ===== 고정(네 서버/관리자) =====
GUILD_ID = 1419200424636055592
OWNER_ID = 1419336030175232071  # 네 관리자 유저 ID

# ===== 환경 변수 =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
SECURE_CHANNEL_ID = int(os.getenv("SECURE_CHANNEL_ID", "0") or 0)
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID", "")
REVIEW_WEBHOOK_URL = os.getenv("REVIEW_WEBHOOK_URL", "")
BUYLOG_WEBHOOK_URL = os.getenv("BUYLOG_WEBHOOK_URL", "")

# DB 파일은 영구볼륨(/data)에 저장
DB_PATH = os.getenv("DB_PATH", "/data/data.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# ===== DB =====
Base = declarative_base()
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
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
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
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

# ===== 임베드/웹훅 =====
def emb(title: str, desc: str, color: int = 0x2b6cb0) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=color)

async def send_webhook(url: Optional[str], content: str):
    if not url:
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={"content": content})
    except Exception:
        traceback.print_exc()

# ===== FastAPI =====
api = FastAPI()
@api.get("/health")
def health():
    return {"ok": True}

# ===== Discord Bot =====
intents = discord.Intents.default()
# message_content 안 씀 → 권한 이슈 최소화
# intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
guild_obj = discord.Object(id=GUILD_ID)

def is_admin(inter: discord.Interaction) -> bool:
    try:
        if getattr(inter.user, "id", 0) == OWNER_ID:
            return True
        if getattr(inter.user, "guild_permissions", None) and inter.user.guild_permissions.manage_guild:
            return True
        if ADMIN_ROLE_ID:
            rid = int(ADMIN_ROLE_ID)
            return any(getattr(r, "id", None) == rid for r in getattr(inter.user, "roles", []))
    except Exception:
        pass
    return False

# ===== 패널 뷰/임베드 =====
def panel_embed() -> discord.Embed:
    s = db()
    try:
        return emb("[ 24 ] 로벅스 자판기", f"재고 안내\n```{stock_text(s)}```\n아래 버튼으로 이용해줘.")
    finally:
        s.close()

def build_panel_view() -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(custom_id="buy",   label="로벅스 구매", style=discord.ButtonStyle.secondary))
    v.add_item(discord.ui.Button(custom_id="topup", label="충전",       style=discord.ButtonStyle.secondary))
    v.add_item(discord.ui.Button(custom_id="myinfo",label="내 정보",    style=discord.ButtonStyle.secondary))
    return v

# ===== 공통 응답 유틸 =====
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

# ===== 자동 패널 갱신 =====
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

# ===== 슬래시 =====
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
        if SECURE_CHANNEL_ID and not (st.secure_channel_id or "").strip():
            st.secure_channel_id = str(SECURE_CHANNEL_ID)
        if REVIEW_WEBHOOK_URL and not (st.review_webhook_url or "").strip():
            st.review_webhook_url = REVIEW_WEBHOOK_URL
        if BUYLOG_WEBHOOK_URL and not (st.buylog_webhook_url or "").strip():
            st.buylog_webhook_url = BUYLOG_WEBHOOK_URL
        s.commit()
    finally:
        s.close()
    await send_embed(inter, "완료", "패널이 게시됐습니다.", ephemeral=True)

@버튼패널.error
async def 버튼패널_error(inter: discord.Interaction, error: Exception):
    await send_embed(inter, "권한 없음", "관리자만 사용할 수 있어요.", ephemeral=True, color=0xff5555)

@bot.tree.command(name="재고추가", description="총 재고 설정/증가/감소 (관리자 전용)", guild=guild_obj)
@app_commands.describe(수량="변경 수량(R$)", 모드="set=설정, inc=증가, dec=감소")
@app_commands.choices(모드=[
    app_commands.Choice(name="설정(덮어쓰기)", value="set"),
    app_commands.Choice(name="증가(+)", value="inc"),
    app_commands.Choice(name="감소(-)", value="dec"),
])
@app_commands.check(lambda i: is_admin(i))
async def 재고추가(inter: discord.Interaction, 수량: int, 모드: app_commands.Choice[str]):
    await safe_ack(inter, ephemeral=True)
    s = db()
    try:
        set_or_inc_stock(s, 수량, mode=모드.value)
        st = get_settings(s)
        if st.panel_channel_id and st.panel_message_id:
            ch = bot.get_channel(int(st.panel_channel_id)) or await bot.fetch_channel(int(st.panel_channel_id))
            msg = await ch.fetch_message(int(st.panel_message_id))
            await msg.edit(embed=panel_embed(), view=build_panel_view())
        cur = stock_text(s)
    finally:
        s.close()
    await send_embed(inter, "재고 변경", f"모드: {모드.value}\n수량: {수량}\n현재 {cur}", ephemeral=True)

@재고추가.error
async def 재고추가_error(inter: discord.Interaction, error: Exception):
    await send_embed(inter, "권한 없음", "관리자만 사용할 수 있어요.", ephemeral=True, color=0xff5555)

@bot.tree.command(name="유저정보", description="특정 유저 정보 조회 (관리자 전용)", guild=guild_obj)
@app_commands.describe(유저="대상 유저")
@app_commands.check(lambda i: is_admin(i))
async def 유저정보(inter: discord.Interaction, 유저: discord.User):
    await safe_ack(inter, ephemeral=True)
    s = db()
    try:
        u = ensure_user(s, str(유저.id))
        desc = f"유저: {유저.mention}\n잔액: {u.balance:,}원\n누적: {u.total_spent:,}원\n등급: {u.tier}"
    finally:
        s.close()
    await send_embed(inter, "유저 정보", desc, ephemeral=True)

@유저정보.error
async def 유저정보_error(inter: discord.Interaction, error: Exception):
    await send_embed(inter, "권한 없음", "관리자만 사용할 수 있어요.", ephemeral=True, color=0xff5555)

@bot.tree.command(name="잔액차감", description="유저 잔액 차감 (관리자 전용)", guild=guild_obj)
@app_commands.describe(유저="대상 유저", 차감금액="차감할 금액(원)")
@app_commands.check(lambda i: is_admin(i))
async def 잔액차감(inter: discord.Interaction, 유저: discord.User, 차감금액: int):
    await safe_ack(inter, ephemeral=True)
    if 차감금액 <= 0:
        return await send_embed(inter, "오류", "차감금액은 1 이상이어야 합니다.", ephemeral=True, color=0xff5555)
    s = db()
    try:
        u = ensure_user(s, str(유저.id))
        if u.balance < 차감금액:
            return await send_embed(inter, "오류", f"유저 잔액 부족. 현재 {u.balance:,}원", ephemeral=True, color=0xff5555)
        u.balance -= 차감금액
        s.commit()
        desc = f"유저: {유저.mention}\n차감: {차감금액:,}원\n잔액: {u.balance:,}원"
    finally:
        s.close()
    await send_embed(inter, "잔액 차감 완료", desc, ephemeral=True)

@잔액차감.error
async def 잔액차감_error(inter: discord.Interaction, error: Exception):
    await send_embed(inter, "권한 없음", "관리자만 사용할 수 있어요.", ephemeral=True, color=0xff5555)

# ===== 버튼 처리 =====
@bot.event
async def on_interaction(inter: discord.Interaction):
    if inter.type != discord.InteractionType.component:
        return
    await safe_ack(inter, ephemeral=True)
    cid = ""
    try:
        if inter.data:
            cid = inter.data.get("custom_id", "") or inter.data.get("customId", "")
    except Exception:
        cid = ""

    s = db()
    try:
        if cid == "myinfo":
            u = ensure_user(s, str(inter.user.id))
            return await send_embed(
                inter, "내 정보",
                f"유저: {inter.user.mention}\n잔액: {u.balance:,}원\n누적: {u.total_spent:,}원\n등급: {u.tier}",
                ephemeral=True
            )
        if cid == "topup":
            class BankModal(discord.ui.Modal, title="충전 신청"):
                depositor = discord.ui.TextInput(label="입금자명", max_length=32)
                amount = discord.ui.TextInput(label="충전금액(원)", max_length=12)
                async def on_submit(self2, i2: discord.Interaction):
                    name = str(self2.depositor.value).strip()
                    try:
                        amt = int(str(self2.amount.value).replace(",", "").strip())
                    except:
                        return await i2.response.send_message(embed=emb("오류","금액 형식 오류",0xff5555), ephemeral=True)
                    s2 = db()
                    try:
                        t = Topup(discord_id=str(i2.user.id), depositor_name=name, amount=amt, status="waiting")
                        s2.add(t); s2.commit(); s2.refresh(t)
                        st2 = get_settings(s2)
                        info = f"- 은행: {st2.bank_name}\n- 계좌: {st2.account_number}\n- 예금주: {st2.holder}"
                        secure = int(st2.secure_channel_id or 0) or SECURE_CHANNEL_ID
                    finally:
                        s2.close()
                    await i2.response.send_message(embed=emb("신청 완료", info), ephemeral=True)
                    if secure:
                        ch = bot.get_channel(secure) or await bot.fetch_channel(secure)
                        await ch.send(embed=emb("충전 승인 요청", f"유저: <@{i2.user.id}>\n입금자명: {name}\n금액: {amt:,}원", 0xf59e0b))
            return await inter.response.send_modal(BankModal())

        if cid == "buy":
            class BuyModal(discord.ui.Modal, title="로벅스 구매 신청"):
                method = discord.ui.TextInput(label="지급방식", max_length=50)
                nick = discord.ui.TextInput(label="로블 닉", max_length=50)
                async def on_submit(self2, i2: discord.Interaction):
                    m = str(self2.method.value).strip()
                    n = str(self2.nick.value).strip()
                    s2 = db()
                    try:
                        o = Order(discord_id=str(i2.user.id), method=m, roblox_nick=n, status="requested")
                        s2.add(o); s2.commit()
                    finally:
                        s2.close()
                    await i2.response.send_message(embed=emb("신청 완료","구매 수량은 채팅에 숫자로 보내줘(에페메랄)."), ephemeral=True)
            return await inter.response.send_modal(BuyModal())
    finally:
        s.close()

# ===== 동기화/상태 =====
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

# ===== 웹/봇 동시 실행 =====
def run_api():
    uvicorn.run("main:api", host="0.0.0.0", port=8000)

# 로그인 에러를 무조건 보여주기 위한 래퍼(원인 확정용)
async def run_bot():
    try:
        await bot.start(DISCORD_TOKEN)
    except Exception:
        print("!!! 디스코드 로그인 실패 또는 초기화 예외 !!!")
        traceback.print_exc()
        raise

if __name__ == "__main__":
    Thread(target=run_api, daemon=True).start()
    init_db()
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN 환경 변수 없음")
        sys.exit(1)
    try:
        # 바로 에러가 보이도록 래퍼 사용
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("KeyboardInterrupt, exiting")
    except Exception:
        # 위에서 이미 스택 찍음
        pass
