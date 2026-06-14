# 运行维护手册

> 服务器: 67.216.199.50 | 端口: 8501 | 服务: streamlit-filter

---

## 1. 服务管理

```bash
# SSH 登录
ssh xfpan@67.216.199.50

# 查看状态
sudo systemctl status streamlit-filter

# 重启
sudo systemctl restart streamlit-filter

# 停止
sudo systemctl stop streamlit-filter

# 启动
sudo systemctl start streamlit-filter

# 开机自启（已启用）
sudo systemctl enable streamlit-filter
```

## 2. 日志查看

```bash
# 实时日志
journalctl -u streamlit-filter -f

# 最近 100 行
journalctl -u streamlit-filter -n 100

# 今天的日志
journalctl -u streamlit-filter --since today

# 最近 1 小时
journalctl -u streamlit-filter --since "1 hour ago"
```

## 3. 代码更新

```bash
cd /opt/filter-research
git pull
sudo systemctl restart streamlit-filter
```

## 4. 数据库备份

```bash
# 手动备份
cp /opt/filter-research/data/market.db /opt/filter-research/data/backups/market-$(date +%Y%m%d_%H%M).db

# 设置每日自动备份 (crontab -e)
0 3 * * * cp /opt/filter-research/data/market.db /opt/filter-research/data/backups/market-$(date +\%Y\%m\%d).db
```

## 5. 磁盘空间

```bash
# 查看占用
du -sh /opt/filter-research/data/market.db
df -h /

# 数据库大小
ls -lh /opt/filter-research/data/market.db
```

## 6. 进程监控

```bash
# 查看 Streamlit 进程
ps aux | grep streamlit

# 内存占用
free -h

# 端口监听
ss -tlnp | grep 8501
```

## 7. 故障排查

| 问题 | 检查 | 解决 |
|------|------|------|
| 无法访问 | `ss -tlnp \| grep 8501` | `sudo systemctl restart streamlit-filter` |
| 内存不足 | `free -h` | 减少并发用户或升级 VPS |
| 数据库损坏 | 日志中有 SQLite 错误 | 从备份恢复 `cp backups/market-xxx.db data/market.db` |
| 依赖缺失 | `journalctl -u streamlit-filter \| grep ModuleNotFoundError` | `source venv/bin/activate && pip install <包名>` |

## 8. 卸载

```bash
sudo systemctl stop streamlit-filter
sudo systemctl disable streamlit-filter
sudo rm /etc/systemd/system/streamlit-filter.service
sudo systemctl daemon-reload
rm -rf /opt/filter-research
```
