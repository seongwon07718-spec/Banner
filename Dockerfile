# 베이스 이미지
FROM python:3.13-slim

# 작업 디렉토리
WORKDIR /app

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스 복사
COPY . .

# 환경 변수 (Zeabur에서 설정 가능)
ENV DISCORD_TOKEN=""
ENV GUILD_ID="0"
ENV SECURE_CHANNEL_ID="0"
ENV ADMIN_ROLE_ID=""
ENV REVIEW_WEBHOOK_URL=""
ENV BUYLOG_WEBHOOK_URL=""
ENV DB_PATH="/data/data.db"

# /data 디렉토리 생성 (영구 볼륨용)
RUN mkdir -p /data

# 기본 CMD: main.py 실행 (FastAPI + Discord Bot 통합)
CMD ["python", "main.py"]
