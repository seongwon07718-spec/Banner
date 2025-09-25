import os, asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from db import SessionLocal, init_db
from models import Setting, Topup, Order
from services import ensure_user, get_settings, stock_summary_text, set_or_inc_stock, dec_stock_exact

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID")
REVIEW_WEBHOOK_URL = os.getenv("REVIEW_WEBHOOK_URL")
BUYLOG_WEBHOOK_URL = os.getenv("BUYLOG_WEBHOOK_URL")
SECURE_CHANNEL_ID = int(os.getenv("SECURE_CHANNEL_ID", "0"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def is_admin(interaction: discord.Interaction):
    if interaction.user.guild_permissions.manage_guild:
        return True
    if ADMIN_ROLE_ID and any(getattr(r, "id", None) == int(ADMIN_ROLE_ID) for r in getattr(interaction.user, "roles", [])):
        return True
    return False

def panel_embed_text():
    db = SessionLocal()
    try:
        return stock_summary_text(db)
    finally:
        db.close()

def make_panel_embed():
    return discord.Embed(
        title="[ 24 ] 로벅스 자판기",
        description=panel_embed_text(),
        color=0x2b6cb0
    )

@bot.even
async def on_ready():
    init_db()
    if GUILD_ID:
        await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    else:
        await bot.tree.sync()
    if not refresh_stock_task.is_running():
        refresh_stock_task.start()
    print(f"Logged in as {bot.user}")

@tasks.loop(seconds=60)
async def refresh_stock_task():
    db = SessionLocal()
    try:
        s = get_settings(db)
        if not s.panel_channel_id or not s.panel_message_id:
            return
        ch = bot.get_channel(int(s.panel_channel_id)) or await bot.fetch_channel(int(s.panel_channel_id))
        msg = await ch.fetch_message(int(s.panel_message_id))
        emb = msg.embeds[0] if msg.embeds else make_panel_embed()
        emb.title = "[ 24 ] 로벅스 자판기"
        emb.description = stock_summary_text(db)
        await msg.edit(embed=emb)
    except Exception:
        pass
    finally:
        db.close()

@bot.tree.command(name="버튼패널", description="로벅스 패널 게시 (관리자 전용)")
@app_commands.check(lambda i: is_admin(i))
async def panel(interaction: discord.Interaction):
    row = discord.ui.ActionRow(
        discord.ui.Button(custom_id="buy", label="로벅스 구매", style=discord.ButtonStyle.primary),
        discord.ui.Button(custom_id="topup", label="충전", style=discord.ButtonStyle.success),
        discord.ui.Button(custom_id="myinfo", label="내 정보", style=discord.ButtonStyle.secondary),
    )
    await interaction.response.send_message("패널 올릴게!", ephemeral=True)
    msg = await interaction.channel.send(embed=make_panel_embed(), components=[row])

    db = SessionLocal()
    try:
        s = get_settings(db)
        s.panel_channel_id = str(msg.channel.id)
        s.panel_message_id = str(msg.id)
        if SECURE_CHANNEL_ID and not s.secure_channel_id:
            s.secure_channel_id = str(SECURE_CHANNEL_ID)
        if REVIEW_WEBHOOK_URL and not s.review_webhook_url:
            s.review_webhook_url = REVIEW_WEBHOOK_URL
        if BUYLOG_WEBHOOK_URL and not s.buylog_webhook_url:
            s.buylog_webhook_url = BUYLOG_WEBHOOK_URL
        db.commit()
    finally:
        db.close()

@bot.tree.command(name="수동충전", description="유저에게 수동 충전 (관리자 전용)")
@app_commands.describe(유저="대상", 금액="충전 금액(원)")
@app_commands.check(lambda i: is_admin(i))
async def manual_topup(interaction: discord.Interaction, 유저: discord.User, 금액: int):
    db = SessionLocal()
    try:
        u = ensure_user(db, str(유저.id))
        u.balance += 금액
        db.commit()
    finally:
        db.close()
    await interaction.response.send_message(f"{유저.mention}에게 {금액:,}원 충전 완료.", ephemeral=True)
    try:
        await 유저.send(f"충전 완료! 현재 잔액이 반영됐어.")
    except:
        pass

@bot.tree.command(name="계좌수정", description="입금 계좌 수정 (관리자 전용)")
@app_commands.describe(은행="예: 토스은행", 계좌="예: 100-1234-567890", 예금주="예: 스토어명")
@app_commands.check(lambda i: is_admin(i))
async def edit_account(interaction: discord.Interaction, 은행: str, 계좌: str, 예금주: str):
    db = SessionLocal()
    try:
        s = get_settings(db)
        s.bank_name = 은행; s.account_number = 계좌; s.holder = 예금주
        db.commit()
    finally:
        db.close()
    await interaction.response.send_message(f"계좌 수정 완료!\n- 은행: {은행}\n- 계좌: {계좌}\n- 예금주: {예금주}", ephemeral=True)

@bot.tree.command(name="재고추가", description="총 로벅스 재고 수량 설정/증가 (관리자 전용)")
@app_commands.describe(수량="총 재고 수량(R$)", 모드="set=덮어쓰기 / inc=증가")
@app_commands.choices(모드=[
    app_commands.Choice(name="설정(덮어쓰기)", value="set"),
    app_commands.Choice(name="증가(+)", value="inc"),
])
@app_commands.check(lambda i: is_admin(i))
async def add_stock(interaction: discord.Interaction, 수량: int, 모드: app_commands.Choice[str]):
    db = SessionLocal()
    try:
        set_or_inc_stock(db, 수량, mode=모드.value)
    finally:
        db.close()
    await interaction.response.send_message("재고 반영 완료! 1분 내 패널에 업데이트돼.", ephemeral=True)

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    cid = interaction.data.get("custom_id")

    if cid == "myinfo":
        db = SessionLocal()
        try:
            u = ensure_user(db, str(interaction.user.id))
        finally:
            db.close()
        await interaction.response.send_message(
            f"누적 금액: {u.total_spent:,}원\n잔액: {u.balance:,}원\n등급: {u.tier}\n최근 구매: 티켓에서 확인 가능해!",
            ephemeral=True
        )

    if cid == "topup":
        class TopupChoice(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=180)
                self.add_item(discord.ui.Button(custom_id="topup_bank", label="계좌이체", style=discord.ButtonStyle.primary))
        embed = discord.Embed(title="충전", description="원하시는 충전 방식을 선택해주세요", color=0x16a34a)
        await interaction.response.send_message(embed=embed, view=TopupChoice(), ephemeral=True)

    if cid == "topup_bank":
        db = SessionLocal()
        try:
            s = get_settings(db)
            bank_info = f"- 은행: {s.bank_name}\n- 계좌: {s.account_number}\n- 예금주: {s.holder}"
        finally:
            db.close()

        class BankModal(discord.ui.Modal, title="계좌이체 신청"):
            depositor = discord.ui.TextInput(label="입금자명", placeholder="예: 홍길동", required=True, max_length=32)
            amount = discord.ui.TextInput(label="충전금액(원)", placeholder="예: 30000", required=True, max_length=12)
            async def on_submit(self, i2: discord.Interaction):
                name = str(self.depositor.value).strip()
                try:
                    amt = int(str(self.amount.value).replace(",", "").strip())
                except:
                    return await i2.response.send_message("금액 형식이 올바르지 않아.", ephemeral=True)
                db2 = SessionLocal()
                try:
                    t = Topup(discord_id=str(i2.user.id), depositor_name=name, amount=amt, status="waiting")
                    db2.add(t); db2.commit(); db2.refresh(t)
                    s2 = get_settings(db2)
                    secure_ch_id = int(s2.secure_channel_id or 0) or SECURE_CHANNEL_ID
                finally:
                    db2.close()
                await i2.response.send_message(f"신청 접수 완료!\n계좌 정보:\n{bank_info}\n승인까지 잠시만 기다려줘.", ephemeral=True)

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

        await interaction.response.send_modal(BankModal())

    if cid.startswith("topup_approve_") or cid.startswith("topup_reject_"):
        is_approve = cid.startswith("topup_approve_")
        tid = int(cid.split("_")[-1])
        if not is_admin(interaction):
            return await interaction.response.send_message("권한 없음", ephemeral=True)
        db = SessionLocal()
        try:
            t = db.query(Topup).get(tid)
            if not t or t.status != "waiting":
                db.close()
                return await interaction.response.send_message("이미 처리됐거나 존재하지 않아.", ephemeral=True)
            u = ensure_user(db, t.discord_id)
            if is_approve:
                u.balance += t.amount
                t.status = "approved"
                db.commit()
                try:
                    user = await bot.fetch_user(int(t.discord_id))
                    await user.send(f"충전 완료! {t.amount:,}원이 반영됐어.\n현재 잔액: {u.balance:,}원")
                except:
                    pass
                await interaction.response.edit_message(embed=discord.Embed(
                    title="충전 승인 완료",
                    description=f"유저: <@{t.discord_id}>\n금액: {t.amount:,}원",
                    color=0x22c55e
                ), view=None)
            else:
                t.status = "rejected"
                db.commit()
                try:
                    user = await bot.fetch_user(int(t.discord_id))
                    await user.send("충전 요청이 거부되었어. 문의는 티켓으로 부탁해")
                except:
                    pass
                await interaction.response.edit_message(embed=discord.Embed(
                    title="충전 거부 처리",
                    description=f"유저: <@{t.discord_id}>\n금액: {t.amount:,}원",
                    color=0xef4444
                ), view=None)
        finally:
            db.close()

    if cid == "buy":
        class BuyModal(discord.ui.Modal, title="로벅스 구매 신청"):
            method = discord.ui.TextInput(label="지급방식", placeholder="예: 그룹펀드/기타", required=True, max_length=50)
            nick = discord.ui.TextInput(label="로블 닉", placeholder="예: RobloxNickname", required=True, max_length=50)
            async def on_submit(self, i2: discord.Interaction):
                m = str(self.method.value).strip()
                n = str(self.nick.value).strip()
                db2 = SessionLocal()
                try:
                    o = Order(discord_id=str(i2.user.id), method=m, roblox_nick=n, status="requested")
                    db2.add(o); db2.commit()
                finally:
                    db2.close()
                await i2.response.send_message("신청 접수! DM을 확인해줘.", ephemeral=True)
                try:
                    user = await bot.fetch_user(int(i2.user.id))
                    await user.send("구매할 로벅스 수량을 보내주세요. 숫자만 보내주세요.")
                except:
                    pass
        await interaction.response.send_modal(BuyModal())

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if isinstance(message.channel, discord.DMChannel):
        txt = message.content.strip().replace(",", "")
        if not txt.isdigit():
            return await message.channel.send("숫자만 입력해줘!")
        qty = int(txt)

        db = SessionLocal()
        try:
            o = db.query(Order).filter_by(discord_id=str(message.author.id), status="requested")\
                               .order_by(Order.created_at.desc()).first()
            if not o:
                db.close()
                return await message.channel.send("진행 중인 구매 신청이 없어. 다시 버튼으로 신청해줘.")
            o.amount_rbx = qty
            ok, msg = dec_stock_exact(db, qty)
            if not ok:
                o.status = "requested"
                db.commit()
                db.close()
                return await message.channel.send(msg)
            o.status = "queued"
            db.commit()
            s = get_settings(db)
            secure_ch_id = int(s.secure_channel_id or 0) or SECURE_CHANNEL_ID
            method, nick = o.method, o.roblox_nick
        finally:
            db.close()

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
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(BUYLOG_WEBHOOK_URL, json=payload)
            except:
                pass

    await bot.process_commands(message)

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        cid = interaction.data.get("custom_id")
        if cid == "make_review":
            class ReviewModal(discord.ui.Modal, title="후기 작성"):
                text = discord.ui.TextInput(label="후기 내용", style=discord.TextStyle.paragraph, required=True, max_length=400)
                async def on_submit(self, i2: discord.Interaction):
                    content = str(self.text.value).strip()
                    url = REVIEW_WEBHOOK_URL
                    if not url:
                        db = SessionLocal()
                        try:
                            s = get_settings(db)
                            url = s.review_webhook_url
                        finally:
                            db.close()
                    if url:
                        import httpx
                        async with httpx.AsyncClient(timeout=10) as client:
                            await client.post(url, json={"content": f"후기 - <@{i2.user.id}>:\n{content}"})
                        await i2.response.send_message("후기 등록 완료!", ephemeral=True)
                    else:
                        await i2.response.send_message("후기 웹훅이 설정되지 않았어.", ephemeral=True)
            return await interaction.response.send_modal(ReviewModal())
