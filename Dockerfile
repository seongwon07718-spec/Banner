FROM python:3.11-slim

# 기본 환경
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# 캐시 무력화용(값 바꾸면 매번 새로 설치됨)
ARG BUILD_TS=2025-09-27-04-23

# 작업 디렉터리
WORKDIR /work

# 필수 패키지 최소 설치(휠 빌드 안정화)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# 파이썬 패키지 설치
COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
 && python -m pip install -r requirements.txt

# 앱 복사
COPY . .

# 포트
ENV PORT=8000

# 실행: uvicorn을 모듈 방식으로 실행해 PATH 이슈 회피 + 디코봇 동시 실행
CMD ["sh","-c","python -m uvicorn main:api --host 0.0.0.0 --port ${PORT:-8000} & python -u main.py"]
