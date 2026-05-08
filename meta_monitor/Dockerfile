# 故意使用独立 slim 镜像，不复用主应用镜像，避免元监控与 AIOps 主链路同生共死。
FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir "httpx>=0.28" && \
    rm -rf /root/.cache

COPY meta_monitor /app/meta_monitor

CMD ["python", "-m", "meta_monitor.healthcheck"]
