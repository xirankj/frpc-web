# frpc-web（FRPC Web 管理界面）

一个基于 Flask 的轻量级 FRPC（fatedier/frp 客户端）可视化管理与监控面板，支持一键容器化部署、登录认证、实时状态与日志、健康检查，以及对 frpc 配置文件的在线管理。当前版本强调“单文件配置”：仅需修改根目录下的 `.env` 即可完成部署与运行。

## 功能特点

- 登录认证与安全
  - 默认管理员账号由环境变量注入（首次启动自动创建）
  - 首次登录强制修改密码（提升安全性）
  - 会话/Remember Cookie 安全选项可通过环境变量控制
- FRPC 管理与配置
  - 在线保存/读取 `frpc.json` 配置（同时兼容 `config.json`，自动补齐 `enabled` 字段）
  - 自动检测根目录存在 `frpc` 与 `frpc.json` 时尝试启动 FRPC
  - 提供“下载 frpc（linux_amd64）”能力并显示进度
- 运行状态与日志
  - WebSocket 实时推送 FRPC 状态、下载进度与日志
  - 应用日志与 FRPC 日志分离存储、滚动切分
- 健康检查与自愈
  - 内置 `/health` 健康检查端点（Docker Compose 已集成）
  - 网络恢复后自动尝试重启 FRPC
- 容器友好
  - Docker 镜像与 Compose 一键部署
  - 数据与日志目录持久化挂载

## 系统要求

- 推荐：Docker 24+ 与 Docker Compose 插件
- 架构：容器镜像基于 linux/amd64（内置“下载 frpc”亦指向 linux_amd64 发行包）。其他架构请手动放置对应 `frpc` 可执行文件。
- 手动运行（非容器）：Python 3.10+；建议在 Linux/WSL 环境运行（网络检查使用 `ping -c` 语法，Windows 原生命令参数不同）。

## 界面截图
<img width="2560" height="1219" alt="服务器配置" src="https://github.com/user-attachments/assets/5cd498d8-03f7-4f58-b693-417b434ad1a3" />
<img width="2545" height="1219" alt="客户端配置" src="https://github.com/user-attachments/assets/bf096c12-9d1b-49d1-8cc9-7d8c2a65cf82" />
<img width="2560" height="1219" alt="服务状态" src="https://github.com/user-attachments/assets/25811636-5aad-49d5-82f1-bb08ce8702de" />

## 部署（推荐：Docker Compose）

1) 获取代码
```bash
git clone https://github.com/xirankj/frpc-web.git
cd frpc-web
```

2) 准备配置（只需编辑一个文件：.env）
- 如仓库中已有 `.env`，直接编辑即可；否则可从示例复制：
```bash
cp .env.example .env
```
- 最小可用配置（务必修改默认口令与密钥）：
```ini
WEB_PORT=8001
DEFAULT_USERNAME=admin
DEFAULT_PASSWORD=admin123
SECRET_KEY=replace-with-a-random-secret
DATABASE_URL=sqlite:////app/data/frpc.db
LOG_LEVEL=INFO
LOG_FILE=/var/log/frpc-web/app.log
FRPC_LOG_DIR=/var/log/frpc-web
TZ=Asia/Shanghai
NETWORK_CHECK_INTERVAL=1800
NETWORK_CHECK_HOSTS=8.8.8.8,114.114.114.114
NETWORK_CHECK_TIMEOUT=5
SESSION_COOKIE_SECURE=False
SESSION_COOKIE_HTTPONLY=True
REMEMBER_COOKIE_SECURE=False
REMEMBER_COOKIE_HTTPONLY=True
```

3) 启动服务
```bash
docker compose up -d
```
首次启动会初始化数据库并创建默认管理员（来源于 `.env`）。

4) 访问与登录
- 访问地址：http://<服务器IP>:WEB_PORT（默认 8001）
- 使用 `.env` 中的默认账号登录，首次登录会强制修改密码。

5) 准备 frpc 可执行文件与配置
- 方法A（容器内一键下载，推荐）：在 Web 界面发起“下载 frpc”，系统会自动下载最新 linux_amd64 版本并解压到容器工作目录。
- 方法B（手动放置）：将 `frpc` 与 `frpc.json` 放在容器工作目录（/app）。若希望从宿主机挂载，可在 docker-compose.yml 的 `volumes` 中按需添加：
```yaml
services:
  frpc-web:
    volumes:
      - ./frpc:/app/frpc
      - ./frpc.json:/app/frpc.json
```
当根目录同时存在 `frpc` 与 `frpc.json` 时，程序会在启动后自动尝试运行 FRPC。

6) 在线保存配置与查看日志
- Web 界面可在线编辑并保存 `frpc.json`（内部自动过滤/补齐必要字段）。
- WebSocket 实时输出 FRPC 日志与运行状态，支持清空日志。

## 另一种方式：自行构建镜像并运行

1) 构建镜像
```bash
docker build -t frpc-web:local .
```

2) 运行容器（使用 `.env` 注入配置）
```bash
docker run -d \
  --name frpc-web \
  --env-file ./.env \
  -p 8001:8001 \
  -v ./data:/app/data \
  -v ./logs:/var/log/frpc-web \
  frpc-web:local
```
如需自定义对外端口，将 `-p 8001:8001` 的左侧改成你的端口，并确保 `.env` 中的 `WEB_PORT` 对应更新（Compose 场景会自动插值，docker run 则直接映射）。

## 环境变量一览

- Core
  - `WEB_PORT`：对外端口（默认 8001）
  - `DEFAULT_USERNAME` / `DEFAULT_PASSWORD`：默认管理员账号与密码（首次启动创建）
  - `SECRET_KEY`：Flask 密钥，生产环境务必改为高强度随机值
  - `DATABASE_URL`：数据库连接，容器内默认 `sqlite:////app/data/frpc.db`
- 日志
  - `LOG_LEVEL`、`LOG_FORMAT`、`LOG_FILE`：应用日志级别/格式/路径
  - `FRPC_LOG_DIR`：FRPC 日志目录（默认与应用日志目录一致），日志文件名为 `frpc.log`
- 网络健康检查
  - `NETWORK_CHECK_INTERVAL`（秒，默认 1800）
  - `NETWORK_CHECK_HOSTS`（逗号分隔的主机列表）
  - `NETWORK_CHECK_TIMEOUT`（秒）
- 会话安全
  - `SESSION_COOKIE_SECURE`、`SESSION_COOKIE_HTTPONLY`
  - `REMEMBER_COOKIE_SECURE`、`REMEMBER_COOKIE_HTTPONLY`

生产环境建议：开启 `SESSION_COOKIE_SECURE=True`，并在反向代理层启用 HTTPS。

## 目录与持久化

- 数据库：`/app/data/frpc.db`（宿主机挂载路径 `./data`）
- 应用与 FRPC 日志目录：`/var/log/frpc-web`（宿主机挂载路径 `./logs`，FRPC 日志文件为 `frpc.log`）

## 健康检查

- HTTP GET `/health` 返回 `{ "status": "ok" }`（无需登录）
- Docker Compose 已内置基于该端点的 `healthcheck`

## 已知限制

- “下载 frpc”当前仅覆盖 linux_amd64 发行包；其他平台/架构请自行放置对应的 `frpc` 可执行文件并（可选）通过卷挂载到容器 `/app`。
- 若在非容器的 Windows 环境手动运行，网络检查使用的 `ping` 参数与 Linux 不同，可能导致网络检查功能不可用。

## 许可证

本项目采用 MIT 许可证。

```
MIT License

Copyright (c) 2025 xirankj

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 联系方式

- 项目维护者：[xirankj]
- 邮箱：[xirankj@163.com]
- 项目链接：[https://github.com/xirankj/frpc-web]
