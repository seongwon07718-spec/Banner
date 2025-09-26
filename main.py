import os
import re
import asyncio
import subprocess
import signal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from fastapi import FastAPI

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.sql import func

# ================= 환경변수 =================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID", "")
SECURE_CHANNEL_ID = int(os.getenv("SECURE_CHANNEL_ID", "0") or 0)
REVIEW_WEBHOOK_URL = os.getenv("REVIEW_WEBHOOK_URL", "")
BUYLOG_WEBHOOK_URL = os.getenv("BUYLOG_WEBHOOK_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data.db")
PORT = int(os.getenv("PORT", "8000"))

# ================= DB =================
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
    return f"재고 : ```총 재고: {st.total_stock_rbx} R$```\n아래 버튼을 눌려 이용해 주세요"

def set_or_inc_stock(s: Session, value: int, mode: str = "set"):
    st = get_settings(s)
    if mode == "set":
        st.total_stock_rbx = max(0, value)
    else:
        st.total_stock_rbx = max(0, (st.total_stock_rbx or 0) + value)
    s.commit()

def dec_stock_exact(s: Session, need: int) -> tuple[bool, str]:
    if need <= 0:
        return False, "요청 수량이 0 이하야."
    st = get_settings(s)
    cur = int(st.total_stock_rbx or 0)
    if cur < need:
        return False, f"재고가 부족해. 현재 재고는 {cur} R$야."
    st.total_stock_rbx = cur - need
    s.commit()
    return True, "OK"

# ================= FastAPI =================
api = FastAPI()

@api.get("/health")
def health():
    return {"ok": True}

# ================= Discord Bot =================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

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

def make_panel_embed() -> discord.Embed:
    s = db()
    try:
        return discord.Embed(
            title="[ 24 ] 로벅스 자판기",
            description=stock_text(s),
            color=0x2b6cb0
        )
    finally:
        s.close()

async def sync_guild_commands():
    if not GUILD_ID:
        await bot.tree.sync()
        print("✅ 글로벌 커맨드 동기화 완료")
        return
    guild = discord.Object(id=GUILD_ID)
    try:
        bot.tree.clear_commands(guild=guild)  # 길드 잔여 커맨드 제거
        for cmd in bot.tree.get_commands():
            cmd.guild_ids = [GUILD_ID]
        await bot.tree.sync(guild=guild)
        print(f"✅ 길드 커맨드 동기화 완료: {GUILD_ID}")
    except Exception as e:
        print(f"⚠️ 길드 동기화 실패: {e}")
        try:
            await bot.tree.sync()
        except:
            pass

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
        emb = msg.embeds[0] if msg.embeds else make_panel_embed()
        emb.title = "[ 24 ] 로벅스 자판기"
        emb.description = stock_text(s)
        await msg.edit(embed=emb)
    except:
        pass
    finally:
        s.close()

# ================= Slash Commands =================
@bot.tree.command(name="버튼패널", description="로벅스 패널 게시 (관리자 전용)")
@app_commands.check(lambda i: is_admin(i))
async def 버튼패널(inter: discord.Interaction):
    try:
        await inter.response.defer(thinking=False, ephemeral=False)
    except:
        pass

    class PanelView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.add_item(discord.ui.Button(custom_id="buy", label="로벅스 구매", style=discord.ButtonStyle.primary))
            self.add_item(discord.ui.Button(custom_id="topup", label="충전", style=discord.ButtonStyle.success))
            self.add_item(discord.ui.Button(custom_id="myinfo", label="내 정보", style=discord.ButtonStyle.secondary))

    msg = await inter.channel.send(embed=make_panel_embed(), view=PanelView())
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

@bot.tree.command(name="수동충전", description="유저에게 수동 충전 (관리자 전용)")
@app_commands.describe(유저="대상", 금액="충전 금액(원)")
@app_commands.check(lambda i: is_admin(i))
async def 수동충전(inter: discord.Interaction, 유저: discord.User, 금액: int):
    try:
        await inter.response.defer(thinking=False, ephemeral=False)
    except:
        pass
    s = db()
    try:
        u = ensure_user(s, str(유저.id))
        u.balance += 금액
        s.commit()
    finally:
        s.close()
    try:
        await 유저.send(f"{금액:,}원 충전 완료! 현재 잔액 반영됐어.")
    except:
        pass

@bot.tree.command(name="계좌수정", description="입금 계좌 수정 (관리자 전용)")
@app_commands.describe(은행="예: 토스은행", 계좌="예: 100-1234-567890", 예금주="예: 스토어명")
@app_commands.check(lambda i: is_admin(i))
async def 계좌수정(inter: discord.Interaction, 은행: str, 계좌: str, 예금주: str):
    try:
        await inter.response.defer(thinking=False, ephemeral=False)
    except:
        pass
    s = db()
    try:
        st = get_settings(s)
        st.bank_name = 은행; st.account_number = 계좌; st.holder = 예금주
        s.commit()
    finally:
        s.close()

@bot.tree.command(name="재고추가", description="총 로벅스 재고 수량 설정/증가 (관리자 전용)")
@app_commands.describe(수량="총 재고 수량(R$)", 모드="set=덮어쓰기 / inc=증가")
@app_commands.choices(모드=[
    app_commands.Choice(name="설정(덮어쓰기)", value="set"),
    app_commands.Choice(name="증가(+)", value="inc"),
])
@app_commands.check(lambda i: is_admin(i))
async def 재고추가(inter: discord.Interaction, 수량: int, 모드: app_commands.Choice[str]):
    try:
        await inter.response.defer(thinking=False, ephemeral=False)
    except:
        pass
    s = db()
    try:
        set_or_inc_stock(s, 수량, mode=모드.value)
        st = get_settings(s)
        if st.panel_channel_id and st.panel_message_id:
            ch = bot.get_channel(int(st.panel_channel_id)) or await bot.fetch_channel(int(st.panel_channel_id))
            msg = await ch.fetch_message(int(st.panel_message_id))
            await msg.edit(embed=make_panel_embed())
    finally:
        s.close()

# ================= Components / Modals / DM =================
@bot.event
async def on_interaction(inter: discord.Interaction):
    if inter.type != discord.InteractionType.component:
        return
    try:
        if not inter.response.is_done():
            await inter.response.defer(thinking=False, ephemeral=False)
    except:
        pass

    cid = inter.data.get("custom_id")

    if cid == "myinfo":
        s = db()
        try:
            u = ensure_user(s, str(inter.user.id))
        finally:
            s.close()
        try:
            await inter.user.send(
                f"누적 금액: {u.total_spent:,}원\n잔액: {u.balance:,}원\n등급: {u.tier}"
            )
        except:
            pass
        return

    if cid == "topup":
        class BankModal(discord.ui.Modal, title="계좌이체 신청"):
            depositor = discord.ui.TextInput(label="입금자명", placeholder="예: 홍길동", required=True, max_length=32)
            amount = discord.ui.TextInput(label="충전금액(원)", placeholder="예: 30000", required=True, max_length=12)
            async def on_submit(self, i2: discord.Interaction):
                name = str(self.depositor.value).strip()
                try:
                    amt = int(str(self.amount.value).replace(",", "").strip())
                except:
                    return await i2.response.send_message("금액 형식이 올바르지 않아.", ephemeral=True)
                s2 = db()
                try:
                    t = Topup(discord_id=str(i2.user.id), depositor_name=name, amount=amt, status="waiting")
                    s2.add(t); s2.commit(); s2.refresh(t)
                    st2 = get_settings(s2)
                    secure_ch_id = int(st2.secure_channel_id or 0) or SECURE_CHANNEL_ID
                    bank_info = f"- 은행: {st2.bank_name}\n- 계좌: {st2.account_number}\n- 예금주: {st2.holder}"
                finally:
                    s2.close()

                await i2.response.send_message("신청 접수 완료! DM 확인해줘.", ephemeral=True)
                try:
                    await i2.user.send(f"계좌 정보:\n{bank_info}\n승인까지 잠시만 기다려줘.")
                except:
                    pass
                if secure_ch_id:
                    ch = bot.get_channel(secure_ch_id) or await bot.fetch_channel(secure_ch_id)
                    embed = discord.Embed(
                        title="충전 승인 요청",
                        description=f"유저: <@{i2.user.id}>\n입금자명: {name}\n충전금액: {amt:,}원",
                        color=0xf59e0b
                    )
                    view = discord.ui.View(timeout=None)
                    view.add_item(discord.ui.Button(custom_id=f"topup_approve_{t.id}", label="승인", style=discord.ButtonStyle.success))
                    view.add_item(discord.ui.Button(custom_id=f"topup_reject_{t.id}", label="거부", style=discord.ButtonStyle.danger))
                    await ch.send(embed=embed, view=view)
        return await inter.response.send_modal(BankModal())

    if cid.startswith("topup_approve_") or cid.startswith("topup_reject_"):
        is_ok = cid.startswith("topup_approve_")
        tid = int(cid.split("_")[-1])
        if not is_admin(inter):
            return
        s = db()
        try:
            t = s.query(Topup).get(tid)
            if not t or t.status != "waiting":
                return
            u = ensure_user(s, t.discord_id)
            if is_ok:
                u.balance += t.amount
                t.status = "approved"
                s.commit()
                try:
                    user = await bot.fetch_user(int(t.discord_id))
                    await user.send(f"충전 완료! {t.amount:,}원이 반영됐어.\n현재 잔액: {u.balance:,}원")
                except:
                    pass
            else:
                t.status = "rejected"
                s.commit()
                try:
                    user = await bot.fetch_user(int(t.discord_id))
                    await user.send("충전 요청이 거부되었어. 문의는 티켓으로 부탁해!")
                except:
                    pass
        finally:
            s.close()
        return

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
                await i2.response.send_message("신청 접수! DM을 확인해줘.", ephemeral=True)
                try:
                    user = await bot.fetch_user(int(i2.user.id))
                    await user.send("구매할 로벅스 수량을 보내주세요. 숫자만 보내주세요.")
                except:
                    pass
        return await inter.response.send_modal(BuyModal())

# ================= DM 숫자 처리 =================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if isinstance(message.channel, discord.DMChannel):
        txt = message.content.strip().replace(",", "")
        if not re.fullmatch(r"\d+", txt or ""):
            return await message.channel.send("숫자만 입력해줘!")
        qty = int(txt)
        s = db()
        try:
            o = s.query(Order).filter_by(discord_id=str(message.author.id), status="requested")\
                              .order_by(Order.created_at.desc()).first()
            if not o:
                s.close()
                return await message.channel.send("진행 중인 구매 신청이 없어. 다시 버튼으로 신청해줘.")
            o.amount_rbx = qty
            ok, msg = dec_stock_exact(s, qty)
            if not ok:
                o.status = "requested"
                s.commit(); s.close()
                return await message.channel.send(msg)
            o.status = "queued"
            s.commit()
            st = get_settings(s)
            secure_ch_id = int(st.secure_channel_id or 0) or SECURE_CHANNEL_ID
            method, nick = o.method, o.roblox_nick
        finally:
            s.close()

        await message.channel.send("확인했어! 조금만 대기해주세요.")
        if secure_ch_id:
            ch = bot.get_channel(secure_ch_id) or await bot.fetch_channel(secure_ch_id)
            embed = discord.Embed(
                title="구매 확인 요청",
                description=f"구매자: <@{message.author.id}>\n로벅스 수량: {qty}\n지급방식: {method}\n로블 닉: {nick}",
                color=0x60a5fa
            )
            await ch.send(embed=embed)

        class ReviewView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=300)
                self.add_item(discord.ui.Button(custom_id="make_review", label="후기작성하기", style=discord.ButtonStyle.primary))
        await message.channel.send("확인 완료되었습니다. 티켓을 열어주시면 처리 해드리겠습니다.", view=ReviewView())

        if BUYLOG_WEBHOOK_URL:
            try:
                import httpx
                payload = {
                    "content": None,
                    "embeds": [{
                        "title": "구매 로그",
                        "description": f"구매자: <@{message.author.id}>\n구매 로벅스 수량: {qty}\n이용해주셔서 감사합니다",
                        "color": 6345341,
                        "fields": [
                            {"name": "지급방식", "value": method, "inline": True},
                            {"name": "로블 닉", "value": nick, "inline": True},
                        ]
                    }]
                }
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(BUYLOG_WEBHOOK_URL, json=payload)
            except:
                pass

    await bot.process_commands(message)

# ================= 실행(uvicorn 모듈 방식) =================
def run_web_bg():
    cmd = ["python", "-m", "uvicorn", "main:api", "--host", "0.0.0.0", "--port", str(PORT)]
    return subprocess.Popen(cmd)

async def start_discord():
    if not DISCORD_TOKEN:
        print("DISCORD_TOKEN이 비어있어. 환경변수에 설정해줘.")
        return
    await bot.start(DISCORD_TOKEN)

async def main_async():
    init_db()
    web = run_web_bg()
    try:
        await start_discord()
    finally:
        try:
            web.send_signal(signal.SIGINT)
        except:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass

---
