# VPS Monitor

轻量级 VPS 监控面板，Docker 部署，内存占用约 25MB。

## 功能

- 系统资源概览（CPU / 内存 / 磁盘 / 网络流量）
- 资源占用排行（按 CPU 或内存排序的进程列表）
- 实时活跃连接（IP + 归属地 + ISP）
- 历史连接记录（持久化存储，支持搜索分页）
- 访问量 TOP 10 排行
- 密码保护
- 中文界面

## 部署

```bash
git clone https://github.com/semisavanna/vps-monitor.git
cd vps-monitor
cp .env.example .env
# 编辑 .env 设置密码
vim .env
docker compose up -d --build
```

## 更新

```bash
cd /opt/vps-monitor
git pull
docker compose up -d --build
```

## 配置

通过 `.env` 文件配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MONITOR_PORT` | 8899 | 监听端口 |
| `MONITOR_PASSWORD` | changeme | 登录密码 |

## 技术栈

- Python + Flask
- SQLite（连接历史持久化）
- 原生 HTML/CSS/JS（无框架依赖）
- Docker（`--network host` + `--pid host`）
