import sqlite3
import datetime as dt
import discord
from .config import BTN_STYLE, COLOR_BLACK, DB_PATH
from .embeds import make_embed
from .licenses import LicenseModal
from .banners import BannerSettingModal
from .db import get_license_row, has_active_license

class SimpleBannerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="등록하기", style=BTN_STYLE, custom_id="register", row=0)
    async def register_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LicenseModal())

    @discord.ui.button(label="설정하기", style=BTN_STYLE, custom_id="setting", row=0)
    async def setting_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, lic_type, exp = has_active_license(interaction.user.id)
        if not ok:
            fields=[]
            if lic_type: fields.append(("보유 라이선스", lic_type, True))
            if exp:
                try: fields.append(("만료일", exp.strftime("%Y-%m-%d %H:%M"), True))
                except Exception: pass
            return await interaction.response.send_message(embed=make_embed("설정 불가","유효한 라이선스가 있어야 설정할 수 있어요.",COLOR_BLACK,fields), ephemeral=True)
        await interaction.response.send_modal(BannerSettingModal())

    @discord.ui.button(label="내정보", style=BTN_STYLE, custom_id="info", row=0)
    async def info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            row = get_license_row(interaction.user.id)
            if not row:
                return await interaction.followup.send(embed=make_embed("라이선스 없음","등록된 라이선스가 없습니다."), ephemeral=True)
            lic_type, activated_at, expires_at = row
            activated_at_fmt = dt.datetime.fromisoformat(activated_at).strftime("%Y-%m-%d %H:%M")
            if lic_type == "영구":
                fields=[("종류","영구",True),("등록일",activated_at_fmt,True)]
                embed = make_embed("라이선스 정보","",COLOR_BLACK,fields)
            else:
                exp = dt.datetime.fromisoformat(expires_at)
                now = dt.datetime.utcnow()
                if (exp - now).total_seconds() <= 0:
                    fields=[("등록일",activated_at_fmt,True),("만료일",exp.strftime("%Y-%m-%d %H:%M"),True)]
                    embed = make_embed("라이선스 만료","라이선스가 만료되었습니다.",COLOR_BLACK,fields)
                else:
                    days = (exp-now).days
                    hours = ((exp-now).seconds)//3600
                    fields=[("종류",lic_type,True),("등록일",activated_at_fmt,True),
                            ("만료일",exp.strftime("%Y-%m-%d %H:%M"),True),("남은 기간",f"{days}일 {hours}시간",True)]
                    embed = make_embed("라이선스 활성화됨","",COLOR_BLACK,fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print("info_button:", e)
            await interaction.followup.send(embed=make_embed("오류","처리 중 오류가 발생했습니다."), ephemeral=True)
