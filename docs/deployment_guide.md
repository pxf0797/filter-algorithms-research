# 滤波分析工具部署指南

## 1. 总体方案

### 架构

```
┌─────────┐     HTTPS      ┌──────────┐   reverse proxy   ┌────────────┐
│ 用户浏览器 ├──────────────►│  Nginx   ├─────────────────►│  Streamlit  │
└─────────┘                 │ :443     │                   │ :8501       │
                            │          │                   └─────┬──────┘
                            │  Let's   │                         │
                            │  Encrypt │                    ┌────▼─────┐
                            │  certbot │                    │  SQLite   │
                            └──────────┘                    │ market.db │
                                                            └──────────┘
```

一句话总结：用户在浏览器通过 HTTPS 访问 Nginx，Nginx 将请求转发给本地运行的 Streamlit 进程；Streamlit 读取 SQLite 数据库，返回渲染好的页面；WebSocket 连接用于实时交互，通过 Nginx 额外配置保持长连接。

### 技术栈

| 组件 | 技术选型 | 用途 |
|------|---------|------|
| Web 应用 | Streamlit | 交互式滤波分析前端 + 后端 |
| 数据库 | SQLite (WAL 模式) | 持久化股票 K 线数据 |
| 反向代理 | Nginx | TLS 终止、WebSocket 代理 |
| HTTPS 证书 | Let's Encrypt + certbot | 免费自动续签 |
| 进程守护 | systemd | 开机自启、崩溃恢复 |
| VPS | Hetzner CX22 (1vCPU/1GB/20GB) | 月费 $3.79 |

---

## 2. VPS 部署

### 2.1 一键部署脚本

以下脚本适用于 **Ubuntu 22.04 / 24.04**。SSH 登录到 VPS 后以 root 用户执行。

创建一个脚本文件:

```bash
# deploy_filter_app.sh — 滤波分析工具一键部署 (Ubuntu 22.04/24.04)
set -euo pipefail

DOMAIN="${1:-}"                 # 例: filter.example.com
GIT_REPO="${2:-}"               # 例: https://github.com/user/filter_research.git
STREAMLIT_PORT=8501
APP_DIR="/opt/filter_research"
APP_USER="filterapp"

# ---- 前置检查 ----
if [[ -z "$DOMAIN" || -z "$GIT_REPO" ]]; then
  echo "Usage: $0 <domain> <git_repo>"
  echo "Example: $0 filter.example.com https://github.com/user/filter_research.git"
  exit 1
fi

echo ">>> 1/7 系统更新 & 安装依赖"
apt-get update && apt-get upgrade -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git

echo ">>> 2/7 创建专用用户"
id -u "$APP_USER" &>/dev/null || useradd -r -s /bin/false -m -d "$APP_DIR" "$APP_USER"

echo ">>> 3/7 克隆项目并安装 Python 依赖"
git clone "$GIT_REPO" "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/streamlit/requirements.txt"

echo ">>> 4/7 创建 Streamlit 配置目录"
mkdir -p "$APP_DIR/.streamlit"
chown "$APP_USER:$APP_USER" "$APP_DIR/.streamlit"

echo ">>> 5/7 配置 systemd 服务"
cat > /etc/systemd/system/filterapp.service << 'SERVICE_EOF'
[Unit]
Description=Filter Research Streamlit App
After=network.target

[Service]
Type=simple
User=filterapp
Group=filterapp
WorkingDirectory=/opt/filter_research
Environment="STREAMLIT_SERVER_PORT=8501"
Environment="STREAMLIT_SERVER_ADDRESS=127.0.0.1"
ExecStart=/opt/filter_research/venv/bin/streamlit run streamlit/streamlit_app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE_EOF

systemctl daemon-reload
systemctl enable filterapp
systemctl start filterapp

echo ">>> 6/7 配置 Nginx"
cat > /etc/nginx/sites-available/filterapp << NGINX_EOF
server {
    listen 80;
    server_name ${DOMAIN};

    # Let's Encrypt 验证
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        proxy_pass http://127.0.0.1:${STREAMLIT_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
NGINX_EOF

ln -sf /etc/nginx/sites-available/filterapp /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ">>> 7/7 申请 HTTPS 证书"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@"$DOMAIN" --redirect

echo ""
echo "=== 部署完成 ==="
echo "访问: https://${DOMAIN}"
echo "查看日志: journalctl -u filterapp -f"
```

**执行:**

```bash
chmod +x deploy_filter_app.sh
sudo ./deploy_filter_app.sh filter.example.com https://github.com/your/repo.git
```

> 注意: 将 `filter.example.com` 替换为实际域名，将 `https://github.com/your/repo.git` 替换为实际仓库地址。

---

### 2.2 手动部署步骤

如果一键脚本执行失败或需要排查，按以下步骤手动操作。

**2.2.1 系统准备**

```bash
# Ubuntu 22.04/24.04
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git
```

**2.2.2 创建用户和应用目录**

```bash
sudo useradd -r -s /bin/false -m -d /opt/filter_research filterapp
sudo git clone <your-repo> /opt/filter_research
sudo chown -R filterapp:filterapp /opt/filter_research
```

**2.2.3 安装 Python 依赖**

```bash
cd /opt/filter_research
sudo -u filterapp python3 -m venv venv
sudo -u filterapp ./venv/bin/pip install --upgrade pip
sudo -u filterapp ./venv/bin/pip install -r streamlit/requirements.txt
```

---

### 2.3 systemd 服务配置

创建 `/etc/systemd/system/filterapp.service`:

```ini
[Unit]
Description=Filter Research Streamlit App
After=network.target

[Service]
Type=simple
User=filterapp
Group=filterapp
WorkingDirectory=/opt/filter_research
Environment="STREAMLIT_SERVER_PORT=8501"
Environment="STREAMLIT_SERVER_ADDRESS=127.0.0.1"
ExecStart=/opt/filter_research/venv/bin/streamlit run streamlit/streamlit_app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

启用并启动:

```bash
sudo systemctl daemon-reload
sudo systemctl enable filterapp
sudo systemctl start filterapp
```

---

### 2.4 Nginx 反向代理配置（含 WebSocket）

创建 `/etc/nginx/sites-available/filterapp`:

```nginx
server {
    listen 80;
    server_name filter.example.com;

    # Let's Encrypt 验证
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
```

关键配置说明:

| 配置项 | 作用 |
|--------|------|
| `proxy_http_version 1.1` | WebSocket 需要 HTTP/1.1 |
| `proxy_set_header Upgrade $http_upgrade` | 将 Upgrade 头转发到 Streamlit |
| `proxy_set_header Connection "upgrade"` | 将连接升级为 WebSocket |
| `proxy_read_timeout 86400s` | WebSocket 长连接超时（24 小时） |
| `proxy_set_header X-Forwarded-*` | 让 Streamlit 获取真实客户端 IP 和协议 |

启用站点:

```bash
sudo ln -s /etc/nginx/sites-available/filterapp /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default   # 删除默认站点
sudo nginx -t && sudo systemctl reload nginx
```

---

### 2.5 HTTPS 配置

首次申请证书:

```bash
sudo certbot --nginx -d filter.example.com --redirect
```

`--redirect` 参数会自动将 HTTP 请求 301 跳转到 HTTPS。

**证书续签:**

Let's Encrypt 证书有效期为 90 天。certbot 安装时会自动配置 systemd timer，无需手动操作。

验证自动续签:

```bash
sudo systemctl status certbot.timer
```

测试续签流程:

```bash
sudo certbot renew --dry-run
```

---

## 3. 手机端优化

### 3.1 Streamlit 移动端配置

创建 `/opt/filter_research/.streamlit/config.toml`:

```toml
[server]
maxUploadSize = 5
enableXsrfProtection = true

[browser]
gatherUsageStats = false

[theme]
base = "light"
primaryColor = "#1f77b4"

# ---- 移动端优化 ----
[runner]
# 防止 WebSocket 因闲置断开 (单位: 秒)
disconnectedSessionTTL = 600
```

`disconnectedSessionTTL = 600` 的作用是: 当手机浏览器切换后台、锁屏或短暂失去网络连接时，Streamlit 会话不会立即销毁，而是保留 600 秒。用户返回应用时可以无缝继续，无需重新加载。

### 3.2 CSS 移动端触控优化

Streamlit 支持通过 `st.markdown` 注入自定义 CSS。在 `streamlit_app.py` 中应用启动时加入以下内容。

在 `set_page_config` 之后添加:

```python
# 注入移动端优化 CSS
st.markdown("""
<style>
/* 触控目标最小 48px (WCAG 2.2 规范) */
@media (hover: none) and (pointer: coarse) {
    .stButton > button {
        min-height: 48px !important;
        font-size: 16px !important;
    }
    .stSelectbox > div > div {
        min-height: 48px !important;
    }
    .stSlider > div > div {
        min-height: 48px !important;
    }
    /* 侧边栏收窄，为图表腾出空间 */
    section[data-testid="stSidebar"] {
        width: 280px !important;
    }
    /* 间距优化 */
    .row-widget.stButton {
        padding-bottom: 0.5rem;
    }
}
/* 通用: 防止 iOS 字体缩放 */
* {
    -webkit-text-size-adjust: 100%;
}
</style>
""", unsafe_allow_html=True)
```

关键优化说明:

- `@media (hover: none) and (pointer: coarse)` — 仅在触屏设备上生效，不影响桌面端
- `min-height: 48px` — 符合 WCAG 2.2 触控目标最小尺寸规范，防止误触
- `font-size: 16px` — 防止 iOS 在 `<input>` 聚焦时自动缩放页面
- `section[data-testid="stSidebar"]` — 缩小侧边栏宽度腾出图表空间
- `-webkit-text-size-adapt: 100%` — 防止 iOS Safari 字体缩放

### 3.3 Nginx WebSocket 长连接配置

WebSocket 连接在手机弱网环境下容易断开。Nginx 配置中已包含以下关键参数:

```nginx
proxy_read_timeout 86400s;
proxy_send_timeout 86400s;
```

将超时设为 24 小时，确保 Nginx 不会主动关闭长连接。加上 Streamlit 端的 `disconnectedSessionTTL = 600`，两者配合可在短暂断网后自动恢复会话。

### 3.4 手机端注意事项

| 注意事项 | 说明 |
|----------|------|
| 浏览器兼容性 | 推荐 Chrome / Safari 最新版，WebSocket 支持良好 |
| 数据用量 | 图表交互会持续传输数据；Wi-Fi 下体验最佳 |
| 性能 | 复杂滤波计算在服务端执行，手机端渲染压力较小 |
| PWA | 暂不推荐启用 PWA。Streamlit 页面非 SPA，离线支持有限 |
| 横屏模式 | 建议横屏使用，图表可获得更宽显示区域 |
| 交互评分 | 基本可用 (三星)。复杂参数调节在桌面端更高效 |

---

## 4. 成本估算

### 4.1 VPS

以 Hetzner CX22 为例 (2025 年价格):

| 项目 | 规格 | 月度费用 | 年度费用 |
|------|------|---------|---------|
| VPS (Hetzner CX22) | 1 vCPU / 1 GB RAM / 20 GB SSD | $3.79 | $45.48 |
| 快照备份 (可选) | 1 个快照 | $0.00 (第 1 个免费) | $0.00 |
| 流量 | 20 TB / 月 | 免费 | 免费 |
| **小计** | | **$3.79** | **$45.48** |

### 4.2 域名

| 项目 | 费用 |
|------|------|
| 域名注册 (`.com` / `.xyz`) | $5-$15 / 年 |
| DNS 解析 | 免费 (Cloudflare / 域名注册商自带) |

### 4.3 总计

| 周期 | 费用范围 |
|------|---------|
| 月度 | $3.79 + 域名摊销 |
| 年度 | $50-$60 (含域名) |

> 可选: 使用 Oracle Cloud 免费套餐 (AMD 实例, 1 vCPU / 1 GB RAM / 100 GB) 可将 VPS 成本降至 $0。注意免费实例可能因资源不足被回收，不适合生产环境。

---

## 5. 运维指南

### 5.1 服务管理

```bash
# 启动
sudo systemctl start filterapp

# 停止
sudo systemctl stop filterapp

# 重启
sudo systemctl restart filterapp

# 查看状态
sudo systemctl status filterapp

# 查看实时日志 (Ctrl+C 退出)
sudo journalctl -u filterapp -f

# 查看最近 100 条日志
sudo journalctl -u filterapp -n 100 --no-pager
```

### 5.2 Nginx 管理

```bash
# 测试配置
sudo nginx -t

# 重载配置 (不中断连接)
sudo systemctl reload nginx

# 重启
sudo systemctl restart nginx

# 查看 Nginx 访问日志
sudo tail -f /var/log/nginx/access.log

# 查看 Nginx 错误日志
sudo tail -f /var/log/nginx/error.log
```

### 5.3 数据库备份

数据库文件位于 `/opt/filter_research/data/market.db`。

**手动备份:**

```bash
# 创建备份目录
sudo mkdir -p /opt/backups

# 执行备份 (使用 SQLite WAL 一致性备份)
sudo -u filterapp sqlite3 /opt/filter_research/data/market.db \
  ".backup '/opt/backups/market_$(date +%Y%m%d_%H%M%S).db'"

# 保留最近 30 天备份，删除旧备份
find /opt/backups -name 'market_*.db' -mtime +30 -delete
```

**自动每日备份 (crontab):**

```bash
sudo crontab -e
```

添加以下行 (每天凌晨 3:00 执行):

```
0 3 * * * /usr/bin/sudo -u filterapp /usr/bin/sqlite3 /opt/filter_research/data/market.db ".backup '/opt/backups/market_$(date +\%Y\%m\%d_\%H\%M\%S).db'" && find /opt/backups -name 'market_*.db' -mtime +30 -delete
```

### 5.4 版本升级

```bash
# 1. 进入应用目录
cd /opt/filter_research

# 2. 拉取最新代码
sudo -u filterapp git pull

# 3. 更新依赖 (如果有变更)
sudo -u filterapp ./venv/bin/pip install -r streamlit/requirements.txt

# 4. 重启服务
sudo systemctl restart filterapp

# 5. 确认启动成功
sudo journalctl -u filterapp -n 20 --no-pager
```

更新数据库 schema (如果有数据库迁移):

```bash
# 先备份
sudo -u filterapp sqlite3 /opt/filter_research/data/market.db \
  ".backup '/opt/backups/market_pre_upgrade_$(date +%Y%m%d_%H%M%S).db'"

# 重启后 Streamlit 启动时 init_db() 会自动执行 CREATE TABLE IF NOT EXISTS
# 如果需要变更已有表结构，手动执行 ALTER TABLE
```

### 5.5 故障排查

| 问题 | 排查命令 | 常见原因 |
|------|---------|---------|
| 502 Bad Gateway | `sudo systemctl status filterapp` | Streamlit 未启动或崩溃 |
| 503 Service Unavailable | `sudo nginx -t && sudo systemctl reload nginx` | Nginx 配置错误 |
| SSL 证书过期 | `sudo certbot renew --dry-run` | certbot timer 未生效 |
| WebSocket 断开 | `sudo journalctl -u filterapp -n 50` | 检查 disconnectedSessionTTL 配置 |
| 数据库错误 | `sudo -u filterapp sqlite3 /opt/filter_research/data/market.db "PRAGMA integrity_check"` | 数据库文件损坏 (从备份恢复) |
| 磁盘空间满 | `df -h` | 日志 / 备份文件过多 |
| 端口冲突 | `ss -tlnp | grep 8501` | 其他进程占用 8501 端口 |

---
