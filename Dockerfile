FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ARG BUILD_TS=2025-09-27-06-20
WORKDIR /work

# 영구 데이터 디렉터리(Zeabur 볼륨 마운트 지점)
RUN mkdir -p /data

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel && python -m pip install -r requirements.txt
COPY . .

ENV PORT=8000
# uvicorn + 디코봇 동시 실행
CMD ["sh","-c","python -m uvicorn main:api --host 0.0.0.0 --port ${PORT:-8000} & python -u main.py"]
