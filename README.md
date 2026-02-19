# clash-docker
轻量级简单易用的 的 Clash 容器镜像（基于 Docker），用于从订阅链接拉取并应用 Clash 配置，支持可选的 Subconverter 转换与多订阅合并。

镜像（Docker Hub）：https://hub.docker.com/r/hhk0571/clash-for-linux

## 主要特性
- 订阅自动拉取并应用到 Clash（支持定时/间隔更新）。
- 订阅链接格式自动转换: 支持 Clash, ClashR, V2Ray, Trojan, Surge, SS, SSR, SSD, Surfboard, Mellow, Loon, Quantumult 等多种订阅格式（当 `SUBCONVERTER_ENABLED=true` 时使用本地 subconverter 服务进行转换）。
- 多订阅合并（当 `SUBCONVERTER_ENABLED=true` 时使用）。
- 支持自定义 User-Agent 和额外的 HTTP 头部（当下载订阅需要时使用）。
- 支持跳过 TLS 证书验证（当 `ALLOW_INSECURE_TLS=true` 时使用）。
- 支持使用本地`config.yaml`文件而不是订阅 URL (当 `CLASH_SUBSCRIPTION_URL` 未设置时使用)。

## 快速开始

1. 方式1: 以环境变量方式运行（最简单, 但强烈推荐使用`docker-compose.yml`）：

```bash
# 必填：订阅地址（支持多条 URL，使用逗号分隔）
export CLASH_SUBSCRIPTION_URL="https://example.com/sub1,https://example.com/sub2"

docker run -d \
  --name clash \
  -p 7890:7890 \
  -p 7891:7891 \
  -p 9091:9090 \
  -e CLASH_SUBSCRIPTION_URL="${CLASH_SUBSCRIPTION_URL}" \
  -v "$PWD/config":/app/config \
  --restart unless-stopped \
  hhk0571/clash-for-linux:latest
```

2. 方式2: 使用 `docker-compose.yml`（⭐⭐⭐⭐⭐强烈推荐）：
docker-compose 是更推荐的方式，尤其当你需要管理多个服务（如 Subconverter）或有更复杂的环境变量配置时。

订阅地址支持多条 URL, 在 `docker-compose.yml` 中可以使用 YAML block scalar 来写多行(⚠️注意下面的例子, 有个竖线 `|`)，或者直接用逗号分隔的单行字符串。

`docker-compose.yaml` 配置：
```yaml
services:
  clash:
    image: hhk0571/clash-for-linux:latest
    environment:
      TZ: Asia/Shanghai
      ### (必填)订阅链接. 若想多链接订阅, 需要打开 SUBCONVERTER_ENABLED
      ### 若不打开 SUBCONVERTER_ENABLED 则只会使用第一条 URL 直接下载
      CLASH_SUBSCRIPTION_URL: |
        https://example.com/sub1
        https://example.com/sub2
      ### (可选) Clash 控制器/仪表盘密码 ###
      # CLASH_API_SECRET: "your-secret"
      ### (可选) 启用本地 subconverter 服务进行多订阅合并 ###
      # SUBCONVERTER_ENABLED: true
    ports:
      - "7890:7890"  # HTTP proxy
      - "7891:7891"  # SOCKS5 proxy
      - "9091:9090"  # Dashboard 仪表盘 (host:port -> container:9090) / 根据需要设置宿主机端口
    volumes:
      - ./config:/app/config
    restart: unless-stopped
```

## 环境变量（常用）
- `CLASH_SUBSCRIPTION_URL`：(必填) 支持单条 URL、以逗号/分号分隔的多 URL，或在 `docker-compose.yml` 中使用 YAML block scalar 写多行。
- `CLASH_API_SECRET`：(可选) Clash 控制器/仪表盘的认证密钥。
- `SUBCONVERTER_ENABLED`：(可选 `true`/`false`) 是否启用本地 subconverter（true/false）。启用后会把 URL 发送到本地 subconverter 进行转换与合并。
- `CLASH_UPDATE_HOUR`：(可选) 每天定时更新的小时（0-23），默认 `3`（凌晨 3 点）。
- `CLASH_UPDATE_INTERVAL`：(可选) 从0时起每隔几个小时更新（1-23），设置后覆盖 `CLASH_UPDATE_HOUR` 的定时策略。比如设置为 `8` 则每 8 小时更新一次(即0、8、16点)。
- `ALLOW_INSECURE_TLS`：(可选 `true`/`false`) 设为`true` 时允许跳过 TLS 证书验证（仅在特殊场景下使用, 比如证书问题, 但不推荐长期使用）。
- `SUBSCRIPTION_USER_AGENT`：(可选) 有些订阅下载需要使用的 User-Agent, 已内置默认值, 亦可自定义。
- `SUBSCRIPTION_HEADERS`：(可选) 下载订阅时额外的自定义头。
- `DISABLE_CLASH_DNS`：(可选 `true`/`false`) 根据需要禁用 Clash 内置 DNS 行为（少数高级场景）。

## 查看UI 界面
浏览器打开 URL:  http://<宿主机地址>:9091/ui

![UI web login](snapshots/ui_login.png)

打开后在后端地址栏填上 http://<宿主机地址>:9091
在秘钥栏填前面生成的密码, 如果为空就不填; 然后点击"添加"按钮, 即可进入仪表盘界面.

![UI dashboard](snapshots/ui_dashboard.png)

## 常用docker命令
- 启动：`docker-compose up -d`
- 停止：`docker-compose down`
- 查看日志：`docker-compose logs -f clash`
- 进入容器：`docker-compose exec clash /bin/sh`
