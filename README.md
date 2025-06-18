# FRPC Web 管理界面

一个基于 Flask 的 FRPC 服务管理界面，提供 Web 界面来管理和监控 FRPC 服务。

## 功能特点

- Web 界面管理 FRPC 服务
- 实时监控 FRPC 服务状态
- 自动网络检测和重连
- Docker 容器化部署
- 支持多用户管理
- 日志记录和查看

## 系统要求

- Python 3.10+
- Docker 和 Docker Compose（可选，用于容器化部署）
- 现代浏览器（Chrome、Firefox、Edge 等）

## 快速开始

### 使用 Docker 部署

1. 克隆仓库：
```bash
git clone https://github.com/xirankj/frpc-web.git
cd frpc-web
```

2. 配置环境变量：
```bash
cp .env.example .env
# 编辑 .env 文件设置必要的环境变量
```

3. 启动服务：
```bash
docker-compose up -d
```

4. 访问 Web 界面：
打开浏览器访问 `http://localhost:8001`

### 手动部署

1. 创建虚拟环境：
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 配置环境变量：
```bash
cp .env.example .env
# 编辑 .env 文件设置必要的环境变量
```

4. 启动服务：
```bash
python run.py
```

## 环境变量配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| WEB_PORT | Web 服务端口 | 8001 |
| DEFAULT_USERNAME | 默认管理员用户名 | admin |
| DEFAULT_PASSWORD | 默认管理员密码 | admin123 |
| SECRET_KEY | Flask 密钥 | your-secret-key-here |
| DATABASE_URL | 数据库连接 URL | sqlite:///data/frpc.db |
| NETWORK_CHECK_INTERVAL | 网络检查间隔（秒） | 1800 |
| NETWORK_CHECK_HOSTS | 网络检查目标 | 8.8.8.8,114.114.114.114 |
| NETWORK_CHECK_TIMEOUT | 网络检查超时（秒） | 5 |

## 项目结构

```
frpc-web/
├── app/                    # 应用主目录
│   ├── models/            # 数据模型
│   ├── routes/            # 路由处理
│   ├── static/            # 静态文件
│   ├── templates/         # 模板文件
│   └── utils/             # 工具函数
├── data/                  # 数据存储目录
├── logs/                  # 日志目录
├── tests/                 # 测试文件
├── .env.example          # 环境变量示例
├── .gitignore            # Git 忽略文件
├── docker-compose.yml    # Docker 编排配置
├── Dockerfile            # Docker 构建文件
├── requirements.txt      # Python 依赖
└── run.py               # 应用入口
```

## 开发指南

1. 安装开发依赖：
```bash
pip install -r requirements.txt
```

2. 运行测试：
```bash
pytest
```

3. 代码风格检查：
```bash
flake8
```

## 贡献指南

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情

## 联系方式

- 项目维护者：[xirankj]
- 邮箱：[xirankj@163.com]
- 项目链接：[https://github.com/xirankj/frpc-web] 
