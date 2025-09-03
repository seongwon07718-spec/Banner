import sqlite3
import datetime as dt
import random, string
import discord
from .config import DB_PATH, COLOR_BLACK
from .embeds import make_embed

def generate_license(lic_type: str):
    part = "-".join(''.join(random.choices(string.ascii_uppercase+string.digits, k=5)) for _ in range(3))
    return f"Wind-Banner-{part}-{lic_type}"

class LicenseModal(discord.ui.Modal, title="라이선스 등록"):
    code = discord.ui.TextInput(label="라이선스 코드", placeholder="Wind-Banner-XXXXX-XXXXX-XXXXX-7D")
    async def on_submit(self, interaction: discord.Interaction):
        try:
            code = str(self.code).strip()
            user_id = interaction.user.id
            now = dt.datetime.utcnow()
            conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
            cur.execute("SELECT type, used_by FROM license_codes WHERE code=?", (code,))
            row = cur.fetchone()
            if not row:
                conn.close()
                return await interaction.response.send_message(embed=make_embed("라이선스 등록 실패","존재하지 않는 코드입니다."), ephemeral=True)
            lic_type, used_by = row
            if used_by is not None:
                conn.close()
                return await interaction.response.send_message(embed=make_embed("라이선스 등록 실패","이미 사용된 코드입니다."), ephemeral=True)
            if lic_type == "7D":
                expires = now + dt.timedelta(days=7); label="7일"
            elif lic_type == "30D":
                expires = now + dt.timedelta(days=30); label="30일"
            elif lic_type == "PERM":
                expires=None; label="영구"
            else:
                expires = now + dt.timedelta(days=1); label="기타"
            cur.execute("REPLACE INTO licenses(user_id,code,type,activated_at,expires_at) VALUES(?,?,?,?,?)",
                        (user_id, code, label, now.isoformat(), expires.isoformat() if expires else None))
            cur.execute("UPDATE license_codes SET used_by=?, used_at=? WHERE code=?", (user_id, now.isoformat(), code))
            conn.commit(); conn.close()
            fields=[("종류",label,True),("등록일",now.strftime("%Y-%m-%d %H:%M"),True),
                    ("만료일", expires.strftime("%Y-%m-%d %H:%M") if expires else "해당 없음",True)]
            await interaction.response.send_message(embed=make_embed("라이선스 등록 완료","",COLOR_BLACK,fields), ephemeral=True)
        except Exception as e:
            print("LicenseModal error:", e)
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=make_embed("오류","등록 중 오류가 발생했습니다."), ephemeral=True)
