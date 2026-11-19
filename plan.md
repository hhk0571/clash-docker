# 为 clash-docker 增加密码认证，并部署到腾讯云

我核对了你当前的 GitHub 仓库 [hhk0571/clash-docker](https://github.com/hhk0571/clash-docker?utm_source=chatgpt.com)。

你的项目已经支持订阅拉取、定时更新、多订阅合并和 Docker 环境配置修正。现在只需要补上代理认证，同时调整公网访问方式。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



我建议采用以下方案：

| 项目   | 方案                         |
| ---- | -------------------------- |
| 代理内核 | 继续使用现有 Mihomo              |
| 代理认证 | Mihomo 原生 `authentication` |
| 认证配置 | 新增 Docker 环境变量             |
| 代理端口 | 7890（HTTP / SOCKS mixed）   |
| 管理面板 | 9091，仅本机访问                 |
| 公网安全 | 来源 IP 白名单 + TLS 加密         |
| 公司电脑 | Windows 系统 HTTP 代理         |
| 订阅更新 | 每次更新自动重新注入认证配置             |

部署原则：先在腾讯云服务器本机验证认证，再验证加密访问，最后才开放受限制的公网入口。 不建议将裸 HTTP 代理直接开放给公网。

## 第一步：确认 Mihomo 的认证配置

Mihomo 官方明确支持 HTTP、SOCKS 和 mixed 端口的用户名密码验证。

[image](https://www.google.com/s2/favicons?domain=https://wiki.metacubex.one\&sz=32)

mihomo docs



配置格式为：

```
mixed-port: 7890
allow-lan: true
bind-address: '*'

authentication:
  - "leo:your-strong-password"
```

这里有两点值得注意：

1. `authentication` 是代理客户端认证，与 `CLASH_API_SECRET` 管理面板密钥完全不同。
2. 不能只修改生成的 `config.yaml`，因为你的项目在订阅更新时会重新生成配置，手动增加的字段可能丢失。

因此，我们需要修改的是配置生成流程，不是 Mihomo 内核本身。

## 第二步：给 Docker 项目增加认证环境变量

建议给项目增加两个变量：

```
CLASH_PROXY_USERNAME: "leo"
CLASH_PROXY_PASSWORD: "your-strong-password"
```

在你的 `docker-compose.yaml` 的 `environment` 区域加入：

```
environment:
  TZ: Asia/Shanghai

  CLASH_SUBSCRIPTION_URL: ${CLASH_SUBSCRIPTION_URL}

  CLASH_PROXY_USERNAME: ${CLASH_PROXY_USERNAME}
  CLASH_PROXY_PASSWORD: ${CLASH_PROXY_PASSWORD}

  CLASH_API_SECRET: ${CLASH_API_SECRET}
```

然后在同目录创建 `.env`：

```
CLASH_SUBSCRIPTION_URL=https://example.com/sub
CLASH_PROXY_USERNAME=leo
CLASH_PROXY_PASSWORD=ReplaceWithLongRandomPassword
CLASH_API_SECRET=ReplaceWithAnotherRandomSecret
```

设置权限：

```
chmod 600 .env
```

同时把 `.env` 添加到 `.gitignore`，防止把真实密码和订阅地址提交到 GitHub。

### 关键：修改配置更新脚本

你的 Dockerfile 通过 `/app/scripts/entrypoint.sh` 启动服务，并将 `scripts/*.sh` 复制进镜像。

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

GitHub



最合适的修改位置，是项目中原有的订阅配置修正逻辑。

流程应当改成：

下载 / 合并订阅

修正 Docker 相关配置

注入 authentication 用户认证

校验最终 YAML 和 Mihomo 配置

保存配置并重新加载 Mihomo

下面给你一个可以直接借鉴的 Python 注入函数，适合在现有脚本的配置修正环节调用。

```
import osdef apply_proxy_auth(config: dict) -> dict:    username = os.getenv("CLASH_PROXY_USERNAME", "")    password = os.getenv("CLASH_PROXY_PASSWORD", "")    if bool(username) != bool(password):        raise ValueError("Proxy auth config incomplete")    if username and password:        if ":" in username:            raise ValueError("Invalid username")        config["authentication"] = [            f"{username}:{password}"        ]    return config
```

此函数处理的是已经解析成 Python 字典的 YAML 配置，例如：

```
config = apply_proxy_auth(config)
```

随后由现有逻辑将配置写回 YAML 文件。

这段代码是需要集成的核心逻辑，不是能独立运行的补丁。由于目前还没有完整核实你仓库中的 Shell 配置处理代码，我不建议直接猜测某个脚本的插入行号。

正式集成时还建议补上三个保护措施：

- 启用认证时，缺少用户名或密码应当启动失败，而不是降级成无认证代理。
- 更新订阅失败或生成无效配置时，保留上一份有效配置。
- 写入配置后进行 Mihomo 配置校验，成功后再重新加载。

这样既能保护现有功能，也能避免因为订阅刷新而突然变成开放代理。

## 第三步：调整 Docker 端口映射

你的仓库当前公开映射了 7890、7891 和 9091。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



建议腾讯云部署时改为：

```
ports:
  - "127.0.0.1:7890:7890"
  - "127.0.0.1:9091:9090"
```

这表示：

- 7890：仅供服务器本机访问，作为后续安全入口的后端。
- 9091：管理面板仅供服务器本机访问。
- 7891：不再映射，避免开放不必要的 SOCKS 端口。

Docker 官方文档确认，使用 `127.0.0.1` 绑定可以限制宿主机端口的外部访问；仍需保持 Docker 及宿主机防火墙更新。

[image](https://www.google.com/s2/favicons?domain=https://docs.docker.com\&sz=32)

Docker Docs



注意，这一步完成后，公司笔记本还不能直接连接代理。这是有意的：我们先确保代理不会以明文、无访问限制的方式暴露，再部署安全入口。

## 第四步：腾讯云安全组

建议至少按以下策略配置：

| 端口       | 来源         | 动作   |
| -------- | ---------- | ---- |
| TCP 22   | 你的个人管理 IP  | 允许   |
| TCP 7890 | 所有公网地址     | 拒绝   |
| TCP 7891 | 所有公网地址     | 拒绝   |
| TCP 9091 | 所有公网地址     | 拒绝   |
| 安全入口端口   | 公司批准的出口 IP | 按需允许 |

假如公司网络的公网出口 IP 是固定的，可以把它作为额外的访问限制。

但 IP 白名单不能取代密码认证或 TLS 加密。

## 第五步：在腾讯云上验证认证功能

在修改后的镜像构建完成并启动后，在 VPS 中执行：

```
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 clash
```

首先测试无密码访问：

```
curl -i \
  -x http://127.0.0.1:7890 \
  https://example.com
```

期望收到：

```
HTTP/1.1 407 Proxy Authentication Required
```

再测试正确密码：

```
curl -I \
  -x http://127.0.0.1:7890 \
  -U "leo:your-strong-password" \
  https://example.com
```

如果返回 `HTTP/1.1 200 Connection established`，说明代理认证已经通过，且 HTTP CONNECT 隧道成功建立。

如果后续目标网站访问失败，还需要分别检查 Mihomo 节点、DNS 和规则，并不能仅凭网站访问失败判断认证没有成功。

## 第六步：Windows 11 公司电脑的连接方式

Windows 内置的手动代理设置通常可以指向 HTTP 代理：

[How to Setup a Proxy in Windows 11 - Geek Rewind](https://images.openai.com/static-rsc-4/9c7Xjv-G8ADbmsKS_pqlndR9DLEe8iT0HTMUYwCGFOTAd-SQtE3jDD_qpTY7TiiZCbfsKAdV3LHTHjDGmaYoZN_-jYnON0P90jr9N1_wCklkvCreXh4kaYm4ZdjzWVYPyFxx-uDANc8UXjmY7FvwoLZUOlNIHYh7yArX6jMsGes?purpose=inline)

Windows 设置 → 网络和 Internet → 代理

打开「使用代理服务器」，填写服务器地址和端口，浏览器在收到代理认证挑战时可能弹出用户名密码窗口。

不过，对你的使用场景有一个非常重要的区别：

- Windows 内置代理设置可以配置普通 HTTP 代理。
- 它不一定支持直接配置需要 TLS 的 HTTPS 代理入口，具体取决于 Windows、浏览器版本及企业策略。
- 通过 HTTP 代理访问 HTTPS 网站虽然能使用 CONNECT，仍不代表客户端到代理服务器之间的连接完全加密。

因此，目前我不建议你直接填写腾讯云公网 IP 和 7890，也不建议为了可用性而放弃 TLS。

理想的最后一步是，在腾讯云增加 HTTPS Forward Proxy 安全入口，并确认公司浏览器能够通过其受支持的代理配置或 PAC 策略连接。否则，需要采用公司 IT 批准的远程访问方案。

## 最终实施顺序

已确认：Mihomo 支持代理账号密码

无须额外部署一套认证服务。

先修改 clash-docker

增加环境变量和订阅更新后的认证注入。

在腾讯云本机完成测试

验证无密码 407、正确密码连接成功以及订阅更新后认证不丢失。

部署加密访问入口

确认客户端支持后，配置 TLS、来源 IP 白名单，再开放公网端口。

我建议先把 `clash-docker` 认证功能做好，这是最明确、最容易验证的一部分，也很适合正式加入你 GitHub 开源项目。

随后再处理公司 Windows 客户端与腾讯云之间的 TLS 连接兼容性，避免出现代理功能正常、但实际公网部署不安全的问题。