# 红利投资筛选工具
#
# 构建：
#   docker build -t moneywiz .
# 运行（挂载宿主的 config/ 与 cache/，首次先执行上面的构建）：
#   docker run --rm \
#     -v "$PWD/config":/app/config \
#     -v "$PWD/cache":/app/cache \
#     moneywiz
# 容器内跑测试：
#   docker run --rm moneywiz pytest
# 属主不符时（非 1000 用户）可加：-u "$(id -u):$(id -g)"
FROM python:3.12-slim

# 行情日期/缓存 TTL 依赖本地日期，默认按中国市场时区（可用 -e TZ=... 覆盖）
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖单独一层：requirements.txt 不变时复用构建缓存
# 一并装开发依赖（pytest），使容器内可直接运行测试
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY . .

# 非 root 运行；cache/ 需可写（挂载宿主目录时注意属主，见文件头注释）
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/cache \
    && chown -R app:app /app
USER app

CMD ["python", "main.py"]
