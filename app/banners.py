import sqlite
import discord
import datetime as dt
from .config import DB_PATH, ROLE_ID, COLOR_BLACK
from .embeds import make_embed, build_channel_name
from .db import has_active_license
from .config import TARGET_ID

async def resolve_category_and_announce(guild: discord.Guild):
    target = guild.get_channel(TARGET_ID)
    if target is None:
        return None, None
    if isinstance(target, discord.CategoryChannel):
        return target, None
    if isinstance(target, discord.TextChannel):
        return target.category, target
    return None, None

class BannerSettingModal(discord.ui.Modal, title="배너 설정"):
    emoji = discord.ui.TextInput(label="이모지", placeholder="예) EMOJI_0  또는  <:custom:1234567890>", max_length=50, required=True)
    banner_name = discord.ui.TextInput(label="배너명", placeholder="배너에 표시할 이름", max_length=50, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            ok, _, _ = has_active_license(interaction.user.id)
            if not ok:
                return await interaction.response.send_message(embed=make_embed("권한 없음","유효한 라이선스가 있어야 배너를 설정할 수 있어요."), ephemeral=True)
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message(embed=make_embed("실행 불가","길드에서만 사용할 수 있어요."), ephemeral=True)
            raw_emoji = str(self.emoji).strip()
            name = str(self.banner_name).strip()
            if not name:
                return await interaction.response.send_message(embed=make_embed("입력 오류","배너명은 비울 수 없어요."), ephemeral=True)
            category, announce_ch = await resolve_category_and_announce(guild)
            if category is None:
                return await interaction.response.send_message(embed=make_embed("설정 오류","지정한 ID에서 카테고리를 찾지 못했어요."), ephemeral=True)

            conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
            cur.execute("REPLACE INTO banner_settings(user_id,emoji,banner_name,updated_at) VALUES(?,?,?,?)",
                        (interaction.user.id, raw_emoji, name, dt.datetime.utcnow().isoformat()))
            conn.commit()
            cur.execute("SELECT channel_id FROM banner_channels WHERE user_id=? AND guild_id=?",
                        (interaction.user.id, guild.id))
            row = cur.fetchone()
            channel_name = build_channel_name(raw_emoji, name)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            }
            if row:
                ch_id = row[0]; channel = guild.get_channel(ch_id)
                if channel is None:
                    channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
                    cur.execute("REPLACE INTO banner_channels(user_id,guild_id,channel_id) VALUES(?,?,?)",
                                (interaction.user.id, guild.id, channel.id)); conn.commit()
                else:
                    try:
                        await channel.edit(name=channel_name, category=category, overwrites=overwrites)
                    except Exception as e:
                        print("channel.edit:", e); await channel.edit(overwrites=overwrites)
            else:
                channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
                cur.execute("INSERT OR REPLACE INTO banner_channels(user_id,guild_id,channel_id) VALUES(?,?,?)",
                            (interaction.user.id, guild.id, channel.id)); conn.commit()

            role = guild.get_role(ROLE_ID); role_msg = "역할 부여 실패(역할 확인 필요)"
            if role:
                try:
                    await interaction.user.add_roles(role, reason="배너 설정 완료")
                    role_msg = f"역할 부여 완료: {role.name}"
                except Exception as e:
                    print("add_roles:", e)

            conn.close()

            if announce_ch and isinstance(announce_ch, discord.TextChannel):
                try:
                    pub = make_embed("배너 채널 생성/갱신","",COLOR_BLACK,[("사용자", interaction.user.mention, True),("채널", f"#{channel.name}", True)])
                    await announce_ch.send(embed=pub)
                except Exception as e:
                    print("announce:", e)

            fields=[("채널", f"#{channel.name}", True), ("역할 처리", role_msg, True), ("입력값", f"{raw_emoji} / {name}", False)]
            await interaction.response.send_message(embed=make_embed("배너 설정 저장 완료","",COLOR_BLACK,fields), ephemeral=True)

        except discord.Forbidden:
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=make_embed("권한 부족","채널/역할 관리 권한이 필요해요."), ephemeral=True)
        except Exception as e:
            print("BannerSettingModal error:", e)
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=make_embed("오류","저장/채널/역할 처리 중 오류가 발생했어요."), ephemeral=True)
