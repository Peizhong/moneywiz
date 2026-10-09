# 红利投资筛选工具
#
# config/ 与 cache/ 在构建时打进镜像，运行不需要任何挂载：
# 构建：
#   docker build -t moneywiz .
# 运行：
#   docker run --rm moneywiz
# 容器内跑测试：
#   docker run --rm moneywiz pytest
# 说明：镜像内的 cache 是构建时的快照（各档 TTL 到期后自动重新拉取，行情与
# 最新净值不缓存、每次都重新取）；
# 容器内新增的缓存随 --rm 消失，需要持久化时自行挂载或改用 docker commit。
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

# config/ 与 cache/ 一并打入（见 .dockerignore 的排除列表）
COPY . .

# 非 root 运行；cache/ 需可写（镜像内快照由构建时的内容提供）
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/cache \
    && chown -R app:app /app
USER app

CMD ["python", "main.py"]
