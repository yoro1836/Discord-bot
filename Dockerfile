FROM python:3.13-slim
WORKDIR /app
# nsenter: AI_SHELL=host에서 호스트 명령 실행용
RUN apt-get update && apt-get install -y --no-install-recommends util-linux && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
ENV DATA_DIR=/data
CMD ["python", "-m", "bot"]
