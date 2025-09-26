import os, re, asyncio, traceback
import discord
from discord import app_commands
from discord.ext import commands, tasks
from fastapi import FastAPI
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.sql import func

# ===== 환경/고정 =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = 1419200424636055592  # 길드 고정
SECURE_CHANNEL_ID = int(os.getenv("SECURE_CHANNEL_ID", "0") or 0)
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID", "")
REVIEW_WEBHOOK_URL = os.getenv("REVIEW_WEBHOOK_URL", "")
BUYLOG_WEBHOOK_URL = os.getenv("BUYLOG_WEBHOOK_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data.db")

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

def init_db():
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

def dec_stock_exact(s: Session, need: int) -> tuple[bool, str]:
    st = get_settings(s)
    cur = int(st.total_stock_rbx or 0)
    if need <= 0:
        return False, "요청 수량이 0 이하야."
    if cur < need:
        return False, f"재고 부족. 현재 {cur} R$"
    st.total_stock_rbx = cur - need
    s.commit()
    return True, "OK"

# ===== FastAPI(헬스만) =====
api = FastAPI()
@api.get("/health")
def health():
    return {"ok": True}

# ===== Discord =====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
guild_obj = discord.Object(id=GUILD_ID)

def is_admin(inter: discord.Interaction) -> bool:
    try:
        if inter.user.guild_permissions.manage_guild:
            return True
        if ADMIN_ROLE_ID:
            rid = int(ADMIN_ROLE_ID)
            return any(getattr(r, "id", None) == rid for r in getattr(inter.user, "roles", []))
    except:
        pass
    return False

def emb(title: str, desc: str, color: int = 0x2b6cb0) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=color)
    return e

def make_panel_embed() -> discord.Embed:
    s = db()
    try:
        desc = f"재고 안내\n```{stock_text(s)}```\n아래 버튼으로 이용해줘."
        return emb("[ 24 ] 로벅스 자판기", desc)
    finally:
        s.close()

# ===== 길드 동기화 =====
async def sync_guild_commands():
    try:
        cmds = await bot.tree.sync(guild=guild_obj)
        print(f"✅ 길드 동기화 성공: {GUILD_ID} (총 {len(cmds)}개)")
        return
    except Exception as e:
        traceback.print_exc()
        print(f"⚠️ 길드 동기화 실패 → 글로벌 폴백: {e}")
    try:
        cmds = await bot.tree.sync()
        print(f"✅ 글로벌 동기화 성공 (총 {len(cmds)}개)")
        return
    except Exception as e:
        traceback.print_exc()
        print(f"❌ 최종 동기화 실패: {e}")

@bot.event
async def on_ready():
    init_db()
    await sync_guild_commands()
    if not refresh_task.is_running():
        refresh_task.start()
    print(f"✅ 봇 로그인 성공: {bot.user}")

@tasks.loop(seconds=60)
async def refresh_task():
    s = db()
    try:
        st = get_settings(s)
        if not st.panel_channel_id or not st.panel_message_id:
            return
        ch = bot.get_channel(int(st.panel_channel_id)) or await bot.fetch_channel(int(st.panel_channel_id))
        msg = await ch.fetch_message(int(st.panel_message_id))
        e = make_panel_embed()
        await msg.edit(embed=e, view=build_panel_view())
    except:
        pass
    finally:
        s.close()

# ===== 공통 뷰(버튼 전부 회색) =====
def build_panel_view() -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(custom_id="buy", label="로벅스 구매", style=discord.ButtonStyle.secondary))
    v.add_item(discord.ui.Button(custom_id="topup", label="충전", style=discord.ButtonStyle.secondary))
    v.add_item(discord.ui.Button(custom_id="myinfo", label="내 정보", style=discord.ButtonStyle.secondary))
    return v

# ===== 슬래시 =====
@bot.tree.command(name="버튼패널", description="로벅스 패널 게시 (관리자 전용)", guild=guild_obj)
@app_commands.check(lambda i: is_admin(i))
async def 버튼패널(inter: discord.Interaction):
    await safe_defer(inter, ephemeral=False)
    msg = await inter.channel.send(embed=make_panel_embed(), view=build_panel_view())
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
    await follow_embed(inter, "완료", "패널이 게시됐어.", ephemeral=True)

@bot.tree.command(name="재고추가", description="총 재고 설정/증가/감소 (관리자 전용)", guild=guild_obj)
@app_commands.describe(수량="변경 수량(R$)", 모드="set=설정, inc=증가, dec=감소")
@app_commands.choices(모드=[
    app_commands.Choice(name="설정(덮어쓰기)", value="set"),
    app_commands.Choice(name="증가(+)", value="inc"),
    app_commands.Choice(name="감소(-)", value="dec"),
])
@app_commands.check(lambda i: is_admin(i))
async def 재고추가(inter: discord.Interaction, 수량: int, 모드: app_commands.Choice[str]):
    await safe_defer(inter, ephemeral=True)
    s = db()
    try:
        set_or_inc_stock(s, 수량, mode=모드.value)
        st = get_settings(s)
        # 패널 갱신
        if st.panel_channel_id and st.panel_message_id:
            ch = bot.get_channel(int(st.panel_channel_id)) or await bot.fetch_channel(int(st.panel_channel_id))
            msg = await ch.fetch_message(int(st.panel_message_id))
            await msg.edit(embed=make_panel_embed(), view=build_panel_view())
    finally:
        s.close()
    await follow_embed(inter, "재고 변경", f"모드: {모드.value}\n수량: {수량}\n현재 {stock_text(db())}", ephemeral=True)

@bot.tree.command(name="유저정보", description="특정 유저의 잔액/정보 조회 (관리자 전용)", guild=guild_obj)
@app_commands.describe(유저="대상 유저")
@app_commands.check(lambda i: is_admin(i))
async def 유저정보(inter: discord.Interaction, 유저: discord.User):
    await safe_defer(inter, ephemeral=True)
    s = db()
    try:
        u = ensure_user(s, str(유저.id))
        e = emb("유저 정보",
                f"유저: {유저.mention}\n잔액: {u.balance:,}원\n누적: {u.total_spent:,}원\n등급: {u.tier}")
    finally:
        s.close()
    await follow_embed(inter, e.title, e.description, ephemeral=True)

@bot.tree.command(name="잔액차감", description="유저 잔액 차감 (관리자 전용)", guild=guild_obj)
@app_commands.describe(유저="대상 유저", 차감금액="차감할 원화 금액")
@app_commands.check(lambda i: is_admin(i))
async def 잔액차감(inter: discord.Interaction, 유저: discord.User, 차감금액: int):
    await safe_defer(inter, ephemeral=True)
    if 차감금액 <= 0:
        return await follow_embed(inter, "오류", "차감금액은 1 이상이어야 해.", ephemeral=True, color=0xff5555)
    s = db()
    try:
        u = ensure_user(s, str(유저.id))
        if u.balance < 차감금액:
            return await follow_embed(inter, "오류", f"유저 잔액 부족. 현재 {u.balance:,}원", ephemeral=True, color=0xff5555)
        u.balance -= 차감금액
        s.commit()
        e = emb("잔액 차감 완료",
                f"유저: {유저.mention}\n차감: {차감금액:,}원\n잔액: {u.balance:,}원", color=0x22c55e)
    finally:
        s.close()
    await follow_embed(inter, e.title, e.description, ephemeral=True)

# ===== 버튼/모달(전부 회색, 반응 확실) =====
@bot.event
async def on_interaction(inter: discord.Interaction):
    if inter.type != discord.InteractionType.component:
        return

    cid = inter.data.get("custom_id", "")

    # 모든 버튼 즉시 ACK (thinking=False, ephemeral=True/False는 케이스별)
    try:
        if not inter.response.is_done():
            # 버튼 클릭 시 화면 잔상 최소화를 위해 ephemeral=True로 본인만 보기
            await inter.response.defer(thinking=False, ephemeral=True)
    except:
        pass

    if cid == "myinfo":
        s = db()
        try:
            u = ensure_user(s, str(inter.user.id))
            e = emb("내 정보",
                    f"유저: {inter.user.mention}\n잔액: {u.balance:,}원\n누적: {u.total_spent:,}원\n등급: {u.tier}")
        finally:
            s.close()
        return await follow_embed(inter, e.title, e.description, ephemeral=True)

    if cid == "topup":
        class BankModal(discord.ui.Modal, title="충전 신청"):
            depositor = discord.ui.TextInput(label="입금자명", placeholder="예: 홍길동", required=True, max_length=32)
            amount = discord.ui.TextInput(label="충전금액(원)", placeholder="예: 30000", required=True, max_length=12)
            async def on_submit(self, i2: discord.Interaction):
                name = str(self.depositor.value).strip()
                try:
                    amt = int(str(self.amount.value).replace(",", "").strip())
                except:
                    return await i2.response.send_message(embed=emb("오류","금액 형식이 올바르지 않아.",0xff5555), ephemeral=True)
                s2 = db()
                try:
                    t = Topup(discord_id=str(i2.user.id), depositor_name=name, amount=amt, status="waiting")
                    s2.add(t); s2.commit(); s2.refresh(t)
                    st2 = get_settings(s2)
                    secure_ch_id = int(st2.secure_channel_id or 0) or SECURE_CHANNEL_ID
                    bank_info = f"- 은행: {st2.bank_name}\n- 계좌: {st2.account_number}\n- 예금주: {st2.holder}"
                finally:
                    s2.close()
                await i2.response.send_message(embed=emb("충전 신청 완료","DM 사용 안 함. 아래 정보를 확인해줘.\n"+bank_info), ephemeral=True)
                if secure_ch_id:
                    ch = bot.get_channel(secure_ch_id) or await bot.fetch_channel(secure_ch_id)
                    await ch.send(embed=emb("충전 승인 요청", f"유저: <@{i2.user.id}>\n입금자명: {name}\n금액: {amt:,}원", 0xf59e0b))
        return await inter.response.send_modal(BankModal())

    if cid == "buy":
        class BuyModal(discord.ui.Modal, title="로벅스 구매 신청"):
            method = discord.ui.TextInput(label="지급방식", placeholder="예: 그룹펀드/기타", required=True, max_length=50)
            nick = discord.ui.TextInput(label="로블 닉", placeholder="예: RobloxNickname", required=True, max_length=50)
            async def on_submit(self, i2: discord.Interaction):
                m = str(self.method.value).strip()
                n = str(self.nick.value).strip()
                s2 = db()
                try:
                    o = Order(discord_id=str(i2.user.id), method=m, roblox_nick=n, status="requested")
                    s2.add(o); s2.commit()
                finally:
                    s2.close()
                await i2.response.send_message(embed=emb("구매 신청 완료","구매 수량을 DM이 아닌 여기서 숫자만 보내줘."), ephemeral=True)
        return await inter.response.send_modal(BuyModal())

    # 승인/거부 버튼은 기존 로직 유지(필요 시 추가)

# DM 숫자 입력 대신, 채널 DM 금지 요구가 있어 구매 수량은 에페메랄로 입력 유도.
# 만약 DM로 수량 받는 플로우를 유지하고 싶으면 on_message(DM) 핸들러를 켜고, 여기 에페메랄 안내만 남겨.

# ===== 유틸: 안전 defer/팔로업 =====
async def safe_defer(inter: discord.Interaction, ephemeral: bool):
    try:
        if not inter.response.is_done():
            await inter.response.defer(thinking=False, ephemeral=ephemeral)
    except:
        pass

async def follow_embed(inter: discord.Interaction, title: str, desc: str, ephemeral: bool, color: int = 0x2b6cb0):
    e = emb(title, desc, color)
    try:
        if inter.response.is_done():
            await inter.followup.send(embed=e, ephemeral=ephemeral)
        else:
            await inter.response.send_message(embed=e, ephemeral=ephemeral)
    except:
        try:
            await inter.followup.send(embed=e, ephemeral=ephemeral)
        except:
            pass
