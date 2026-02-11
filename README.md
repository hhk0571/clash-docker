# clash-docker
简单易用的 Clash (代理) + Mihomo(Clash Meta 管理面板) docker

# 使用方法

## docker run 命令

```bash
# 设置clash订阅地址
export CLASH_SUB_URL=https://your-subscription-ur

docker run -d \
  --name clash \
  -p 7890:7890 \
  -p 7891:7891 \
  -p 9091:9090 \
  -e CLASH_SUBSCRIPTION_URL=${CLASH_SUB_URL} \
  --health-cmd "curl -f http://127.0.0.1:9090/version || exit 1" \
  --health-interval 30s \
  --health-timeout 10s \
  --health-retries 3 \
  --restart unless-stopped \
  hhk0571/clash-for-linux:latest
```

## docker compose

通常用于在NAS设备上启动docker.

配置 `docker-compose.yaml`:
```yaml
services:
  clash:
    build:
      context: .
      dockerfile: Dockerfile
    image: hhk0571/clash-for-linux:latest # 改成这样
    environment:
      # Proxy settings/代理设置 (optional, remove if not needed/ 若无需代理, 注释掉下面这几行 )
      # - HTTP_PROXY=http://10.10.10.9:8080
      # - HTTPS_PROXY=http://10.10.10.9:8080
      # - http_proxy=http://10.10.10.9:8080
      # - https_proxy=http://10.10.10.9:8080
      # - NO_PROXY=127.0.0.1,localhost
      # - no_proxy=127.0.0.1,localhost

      # Clash subscription URL / clash 订阅链接
      - CLASH_SUBSCRIPTION_URL=https://your-subscription-url

      # Interval (in seconds) to check for configuration updates (default: 3600 = 1 hour)
      - CLASH_UPDATE_INTERVAL=3600 # 订阅更新间隔(单位:秒), 3600秒=1小时

      # Clash secret for API authentication (comment out to use config default)
      #- CLASH_SECRET=your-secret-key # UI 界面的密码, 可用命令 openssl rand -hex 32 生成随机密码.

      # Override DNS settings (true/false, comment out to use config default)
      #- CLASH_DNS_ENABLE=false  # Set to false in corporate environments with custom DNS (保持注释就好, 需要时再设为false)
    ports:
      - "7890:7890"  # HTTP proxy
      - "7891:7891"  # SOCKS5 proxy
      - "9091:9090"  # External controller / Dashboard (host:port -> container:9090) / 根据需要设置宿主机端口
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:9090/version"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
```

## 查看UI 界面
浏览器打开 URL:  http://<宿主机地址>:9091/ui

![UI web login](snapshots/ui_login.png)

打开后在后端地址栏填上 http://<宿主机地址>:9091
在秘钥栏填前面生成的密码, 如果为空就不填; 然后点击"添加"按钮, 即可进入仪表盘界面.

![UI dashboard](snapshots/ui_dashboard.png)
