# clash-docker
简单易用的 Clash (代理) + Mihomo(Clash Meta 管理面板) docker

docker hub: https://hub.docker.com/r/hhk0571/clash-for-linux

# 使用方法

## docker run 命令

```bash
# clash订阅地址(必填), 默认每天凌晨3点更新订阅
export CLASH_SUB_URL=https://your-subscription-ur

docker run -d \
  --name clash \
  -p 7890:7890 \
  -p 7891:7891 \
  -p 9091:9090 \
  -e CLASH_SUBSCRIPTION_URL=${CLASH_SUB_URL} \
  -v ./config:/app/config \
  --restart unless-stopped \
  hhk0571/clash-for-linux:latest
```

其他环境变量说明请参考下面 `docker-compose.yaml` 文件中的注释.

## docker compose

通常用于在NAS设备上启动docker.

配置 `docker-compose.yaml`:
```yaml
services:
  clash:
    image: hhk0571/clash-for-linux:latest
    environment:
      ## Timezone setting
      TZ: Asia/Shanghai

      ## clash 订阅链接 (required/必填)
      CLASH_SUBSCRIPTION_URL: https://your-subscription-url

      ## 仪表盘界面访问密码(默认为空), 觉得有必要再设置
      # CLASH_SECRET: ${CLASH_SECRET:-}

      ## 订阅定时更新时间 (0-23), 默认每天凌晨3点更新
      # CLASH_UPDATE_HOUR: ${CLASH_UPDATE_HOUR:-3}

      ## 订阅更新间隔 (单位:小时), 设置此项会覆盖上面的定时更新.
      ## 比如设置为8就是每8小时更新一次订阅, 觉得有必要再设置
      # CLASH_UPDATE_INTERVAL: ${CLASH_UPDATE_INTERVAL:-}

      ## 是否禁用 Clash 内置 DNS, 只有在你非常清楚自己在做什么的情况下才需要禁用它.
      # DISABLE_CLASH_DNS: ${DISABLE_CLASH_DNS:-}
    ports:
      - "7890:7890"  # HTTP proxy
      - "7891:7891"  # SOCKS5 proxy
      - "9091:9090"  # External controller / Dashboard (host:port -> container:9090) / 根据需要设置宿主机端口
    volumes:
      - ./config:/app/config
    restart: unless-stopped
```

## 查看UI 界面
浏览器打开 URL:  http://<宿主机地址>:9091/ui

![UI web login](snapshots/ui_login.png)

打开后在后端地址栏填上 http://<宿主机地址>:9091
在秘钥栏填前面生成的密码, 如果为空就不填; 然后点击"添加"按钮, 即可进入仪表盘界面.

![UI dashboard](snapshots/ui_dashboard.png)
