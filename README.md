# FastAPI + Redis + QMT 信号中转服务

这是一个面向小型量化交易场景的 FastAPI + Redis 信号中转服务，支持：

- 聚宽或其他策略端通过 webhook 推送交易信号
- FastAPI 将信号写入 Redis 的 pending 队列
- QMT 轮询拉取时，信号会被原子转移到 processing 队列
- QMT 下单成功后调用 ack 接口确认消费
- 超时未确认的信号会显式回补到 pending 队列
- 多次失败的信号进入 dead-letter 队列，支持查看与重放

当前服务语义是 **at-least-once**，不是 exactly-once，因此 QMT 端必须保证幂等处理。

## 0. 快速开始

如果你只是想最快把服务跑起来，可以按下面步骤执行：

1. 安装依赖：`pip install -r requirements.txt`
2. 复制配置模板：`copy .env.example .env`
3. 修改 `.env` 中的 `SECRET_TOKEN`，使用真实且足够长的随机字符串
4. 启动 Redis
5. 启动服务：
   - 本地运行：`uvicorn app.main:app --host 0.0.0.0 --port 8000`
   - 或容器运行：`docker compose up --build`
6. 验证接口：
   - `GET /livez`
   - `GET /readyz`
   - `GET /metrics`
   - `GET /api/queue/status`

建议第一次启动后直接执行：

```bash
curl http://127.0.0.1:8000/livez
curl -H "x-token: 你的密钥" http://127.0.0.1:8000/readyz
curl -H "x-token: 你的密钥" http://127.0.0.1:8000/metrics
curl -H "x-token: 你的密钥" http://127.0.0.1:8000/api/queue/status
```

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

## 2. 准备配置

把 `.env.example` 复制为 `.env`，然后按实际环境修改：

```bash
copy .env.example .env
```

必须重点修改：

- `SECRET_TOKEN`
- `REDIS_HOST`
- `REDIS_PORT`
- `ACK_TIMEOUT_SECONDS`

建议同时关注：

- `MAX_DELIVERY_ATTEMPTS`
- `QUEUE_PENDING_NAME`
- `QUEUE_PROCESSING_NAME`
- `QUEUE_DEAD_LETTER_NAME`
- `LOG_LEVEL`

## 3. 启动 Redis

确保 Redis 已在本机或可访问的服务器上启动，默认端口为 `6379`。

## 4. 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

服务启动时会主动检查 Redis 是否可用；如果 Redis 不可用，应用会直接启动失败，而不是带着错误配置继续提供服务。

## 5. 接口说明

### 5.1 推送信号

- 方法：`POST /webhook/signal`
- Header：`x-token: 你的密钥`

示例：

```bash
curl -X POST http://127.0.0.1:8000/webhook/signal ^
  -H "Content-Type: application/json" ^
  -H "x-token: change_me_to_a_long_random_string" ^
  -d "{\"symbol\":\"000001.XSHE\",\"action\":\"buy\",\"volume\":100,\"price\":12.34,\"strategy_id\":\"demo_strategy\",\"source\":\"jq\"}"
```

返回体示例：

```json
{
  "status": "success",
  "message": "信号已接收并写入 pending 队列",
  "data": {
    "signal_id": "2af2d1f9-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "symbol": "000001.XSHE",
    "action": "buy",
    "volume": 100,
    "price": 12.34,
    "strategy_id": "demo_strategy",
    "source": "jq",
    "received_at": 1710000000.0,
    "status": "pending",
    "delivery_attempts": 0,
    "last_delivered_at": null,
    "requeued_at": null,
    "dead_lettered_at": null,
    "extra": {}
  }
}
```

### 5.2 拉取待处理信号

- 方法：`GET /api/get_signals?limit=10`
- Header：`x-token: 你的密钥`

示例：

```bash
curl -X GET "http://127.0.0.1:8000/api/get_signals?limit=10" ^
  -H "x-token: change_me_to_a_long_random_string"
```

返回的信号会进入 processing 队列，并附带 `signal_id`，QMT 需要在下单成功后回传该编号进行确认。

说明：`GET /api/get_signals` 不会隐式触发超时回补；超时回补需要通过专门的运维接口执行。

### 5.3 确认消费

- 方法：`POST /api/ack_signals`
- Header：`x-token: 你的密钥`

示例：

```bash
curl -X POST http://127.0.0.1:8000/api/ack_signals ^
  -H "Content-Type: application/json" ^
  -H "x-token: change_me_to_a_long_random_string" ^
  -d "{\"signal_ids\":[\"替换成实际 signal_id\"]}"
```

如果某个 `signal_id` 不存在，或存在但不在 processing 队列中，接口会分别在 `missing` 和 `not_processing` 字段中返回，便于调用方排查状态不一致问题。

### 5.4 存活检查

- 方法：`GET /livez`

### 5.5 就绪检查

- 方法：`GET /readyz`
- Header：`x-token: 你的密钥`

### 5.6 队列状态

- 方法：`GET /api/queue/status`
- Header：`x-token: 你的密钥`

### 5.7 手动回补超时信号

- 方法：`POST /api/queue/requeue-timeouts`
- Header：`x-token: 你的密钥`

说明：该接口会扫描 processing 队列，将超过 `ACK_TIMEOUT_SECONDS` 且未确认的信号回补到 pending 队列；当投递次数超过 `MAX_DELIVERY_ATTEMPTS` 时，信号会进入 dead-letter 队列，避免无限重试。生产环境建议由受保护的运维任务定期调用，而不是依赖拉取接口的隐式副作用。

### 5.8 查看死信队列

- 方法：`GET /api/queue/dead-letter`
- Header：`x-token: 你的密钥`

### 5.9 重放死信

- 方法：`POST /api/queue/dead-letter/replay`
- Header：`x-token: 你的密钥`
- Body：`{"signal_ids": ["实际 dead-letter signal_id"]}`

### 5.10 Prometheus 指标

- 方法：`GET /metrics`
- Header：`x-token: 你的密钥`

当前会暴露 HTTP 请求计数/耗时、入队/拉取/确认/回补/死信/重放计数，以及 pending/processing/dead-letter 队列深度。

说明：`/metrics` 需要 `x-token`，并且会在每次抓取前从 Redis 同步最新队列深度，避免进程重启后导出过期 backlog 数据。

## 6. 典型使用与运维流程

### 6.1 正常业务闭环

1. 策略端调用 `POST /webhook/signal` 推送信号
2. 服务把信号写入 Redis pending 队列
3. QMT 调用 `GET /api/get_signals` 拉取信号
4. 信号进入 processing 队列
5. QMT 成功下单后调用 `POST /api/ack_signals`
6. 信号从 processing 和 payload 存储中删除

### 6.2 QMT 拉取后崩溃，如何恢复

1. 调用 `GET /api/queue/status` 查看 processing 是否有积压
2. 调用 `POST /api/queue/requeue-timeouts`
3. 超时且未确认的信号会重新进入 pending
4. 超过最大投递次数的信号会进入 dead-letter

### 6.3 dead-letter 处理流程

1. 调用 `GET /api/queue/dead-letter` 查看死信内容
2. 根据 `extra.dead_letter_reason` 判断进入死信的原因
3. 确认可再次尝试后，调用 `POST /api/queue/dead-letter/replay`
4. replay 后信号回到 pending，等待再次被 QMT 拉取

## 7. 常见错误响应

### 7.1 鉴权失败（401）

```json
{
  "status": "error",
  "message": "Token 无效，拒绝访问。",
  "error_code": "unauthorized"
}
```

### 7.2 参数校验失败（422）

```json
{
  "status": "error",
  "message": "请求参数校验失败",
  "error_code": "validation_error",
  "details": [
    {
      "loc": ["body", "signal_ids"],
      "msg": "Field required"
    }
  ]
}
```

### 7.3 Redis 不可用（503）

```json
{
  "status": "error",
  "message": "Redis 不可用：...",
  "error_code": "redis_unavailable"
}
```

## 8. Redis 数据结构设计

- `QUEUE_PENDING_NAME`：待消费队列
- `QUEUE_PROCESSING_NAME`：已拉取待确认队列
- `QUEUE_PAYLOAD_HASH_NAME`：信号载荷哈希表，`field=signal_id`，`value=JSON`
- `QUEUE_DEAD_LETTER_NAME`：超过最大重试次数后的死信队列

## 9. 可靠消费语义

1. webhook 写入信号时，同时保存 payload 和 pending 队列
2. QMT 拉取时使用 `RPOPLPUSH` 将信号从 pending 原子转到 processing
3. QMT 成功下单后调用 ack 删除 processing 中的对应 `signal_id`
4. 如果 QMT 拉取后崩溃，超过 `ACK_TIMEOUT_SECONDS` 的 processing 信号可通过受保护的回补接口重新放回 pending
5. 如果同一信号超过 `MAX_DELIVERY_ATTEMPTS` 仍未完成确认，会进入 dead-letter 队列等待人工处理

## 10. 生产化建议

- `SECRET_TOKEN` 必须使用高强度随机字符串，默认占位值无法启动。
- 生产环境不要提交 `.env`，仅保留 `.env.example` 作为模板。
- 建议将 `/api/queue/requeue-timeouts` 挂到受控运维任务中定期执行。
- 可通过 `/api/queue/dead-letter` 查看死信，并使用 `/api/queue/dead-letter/replay` 做定点重放。
- 可通过 `/metrics` 接入 Prometheus 抓取基础运行指标。
- 当前服务语义是至少一次投递（at-least-once），QMT 端必须保证幂等处理。

## 11. 当前保证与限制

### 当前保证

- 服务启动时会检查 Redis 可用性
- 错误响应格式已统一
- 支持 timeout requeue、dead-letter、replay
- 支持 Prometheus 指标暴露
- 支持 request id 透传与日志关联

### 当前限制

- 当前语义是 **at-least-once**，不是 exactly-once
- `/metrics` 是受保护接口，抓取时需要携带 `x-token`
- CI 中的 Docker 校验目前是 **build-only**，不代表已经完成运行态 smoke test
- 当前 Redis 队列模型适合小型服务；如果后续复杂度继续上升，可以再评估 Redis Streams

## 12. Docker 运行

### 12.1 本地容器启动

```bash
docker compose up --build
```

请确保 `.env` 中已经设置真实的 `SECRET_TOKEN`。Compose 的应用健康检查会使用当前环境中的 `SECRET_TOKEN` 调用 `/readyz`，不会再依赖占位默认值。

默认会启动两个服务：

- `redis`
- `app`

Compose 中应用健康检查默认使用 `/readyz`，确保 Redis 依赖异常时不会继续被标记为健康。

应用启动后可访问：

- `http://127.0.0.1:8000/livez`
- `http://127.0.0.1:8000/readyz`
- `http://127.0.0.1:8000/metrics`

### 12.2 使用已发布镜像启动

如果你不想本地构建，也可以直接使用 GitHub Container Registry 中已经发布的镜像。

镜像地址：

```bash
ghcr.io/xujw3/jqmt:latest
```

如果你使用的是版本发布镜像，也可以拉取指定 tag，例如：

```bash
ghcr.io/xujw3/jqmt:v0.1.0
```

拉取镜像：

```bash
docker pull ghcr.io/xujw3/jqmt:latest
```

### 12.3 使用 docker run 直接启动

最小可运行示例：

```bash
docker run -d --name jqmt \
  -p 8000:8000 \
  -e SECRET_TOKEN=请替换成真实高强度密钥 \
  -e REDIS_HOST=宿主机或Redis地址 \
  -e REDIS_PORT=6379 \
  ghcr.io/xujw3/jqmt:latest
```

如果 Redis 也跑在 Docker 中，推荐把应用容器和 Redis 容器放到同一个 Docker network 中，然后让 `REDIS_HOST` 指向 Redis 容器名。

例如：

```bash
docker network create jqmt-net

docker run -d --name jqmt-redis --network jqmt-net redis:7-alpine

docker run -d --name jqmt \
  --network jqmt-net \
  -p 8000:8000 \
  -e SECRET_TOKEN=请替换成真实高强度密钥 \
  -e REDIS_HOST=jqmt-redis \
  -e REDIS_PORT=6379 \
  ghcr.io/xujw3/jqmt:latest
```

### 12.4 容器启动后如何验证

启动后建议依次检查：

```bash
curl http://127.0.0.1:8000/livez
curl -H "x-token: 你的密钥" http://127.0.0.1:8000/readyz
curl -H "x-token: 你的密钥" http://127.0.0.1:8000/metrics
curl -H "x-token: 你的密钥" http://127.0.0.1:8000/api/queue/status
```

如果容器启动失败，可以先看日志：

```bash
docker logs jqmt
```

最常见原因通常是：

- `SECRET_TOKEN` 仍然用了占位值
- `REDIS_HOST` 配错
- Redis 没启动或网络不通

## 13. CI

仓库已提供基础 GitHub Actions 工作流：

- 安装依赖
- 运行 `pytest`
- 使用 Docker Buildx 构建镜像
- 自动生成标准镜像 metadata 与 tags
- 在 `main` 分支 push 时自动发布到 `ghcr.io/xujw3/jqmt`
- 在 `v*` tag push 时自动发布版本镜像

当前发布策略：

- `pull_request`：只构建，不推送
- 非 `main` 分支 push：只构建，不推送
- `main` 分支 push：构建并发布到 GHCR
- `v*` tag push：构建并发布版本镜像到 GHCR

当前 CI 还会在发布前执行一个最小运行态 smoke test：

- 启动临时 Redis 容器
- 启动当前构建出的应用镜像
- 检查 `/livez`
- 使用临时 token 检查 `/readyz`

也就是说，现在 CI 不只是验证“镜像能构建”，还会验证“镜像能启动，并能通过基本探针”。后续如果需要，可以继续扩展为更完整的业务级 smoke test、lint/typecheck、Redis 集成测试。

## 14. Request ID 与日志关联

- 服务会优先透传请求头中的 `X-Request-ID`
- 如果请求未携带该 header，服务会自动生成一个新的 request id
- 所有响应都会回写 `X-Request-ID`
- 应用日志会自动带上 `request_id=...`，便于把 API 调用、异常和队列操作串起来排查

## 15. 推荐的 QMT 端处理顺序

1. 轮询 `GET /api/get_signals`
2. 遍历返回的 `signals`
3. 用返回的 `symbol`、`action`、`volume`、`price` 下单
4. 下单成功后，把对应 `signal_id` 列表提交到 `POST /api/ack_signals`
5. 下单失败则不要 ack，等待超时后回补重试
