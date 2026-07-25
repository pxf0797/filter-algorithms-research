# Filter Research 配置与部署深度分析报告

> 生成日期: 2026-07-23
> 项目: filter-research (v10.5.0)
> 分析范围: 依赖管理 / Docker 优化 / CI-CD 效率 / 环境管理 / 代码质量门禁 / 配置策略 / 部署方案 / 依赖冲突

---

## 1. 依赖健康度

### 1.1 依赖清单与版本分析

| 依赖 | 当前版本 | 锁定方式 | 最新版本 | 落后 | 用途 | 使用状况 |
|------|---------|---------|---------|------|------|---------|
| streamlit | 1.57.0 | `==` | 1.60.0 | 3 minor | Web UI 框架 | **已使用** (5个文件) |
| loguru | 0.7.3 | `==` | 0.7.3 | 0 | 结构化日志 | **已使用** (7个文件) |
| numpy | 2.2.4 | `==` | ~2.2.x | 0 | 数值计算 | **已使用** (8+个文件) |
| scipy | 1.17.1 | `==` | ~1.17.x | 0 | 信号处理(滤波) | **已使用** (filter_engine.py) |
| plotly | 6.7.0 | `==` | ~6.7.x | 0 | 交互式图表 | **已使用** (2个文件) |
| pandas | 2.3.3 | `==` | ~2.3.x | 0 | 数据分析 | **已使用** (6+个文件) |
| statsmodels | 0.14.6 | `==` | ~0.14.x | 0 | LOWESS 平滑 | **已使用** (filter_engine.py) |
| yfinance | 1.4.1 | `==` | ~1.4.x | 0 | 股票数据拉取 | **已使用** (2个文件) |
| pyarrow | >=14.0.0 | `>=` 无上限 | ~18.x | 不定 | Parquet 序列化 | **已使用** (parquet_store.py) |
| pip-audit | >=2.7,<3 | 范围 | ~2.7.x | 0 | CI 安全审计 | **CI专用** (非运行时) |

**结论**: 9 个运行时依赖全部被代码引用，无冗余包。`pip-audit` 仅在 CI 中使用，作为运行时依赖写入 requirements.txt 不合理。

### 1.2 已知风险

| 风险 | 严重度 | 说明 |
|------|--------|------|
| **无 lock 文件** | 高 | 缺少 `poetry.lock` 或 `pip freeze` 输出。传递依赖版本不受控，不同时间 `pip install` 可能得到不同的依赖树，导致"在我机器上能跑"的问题 |
| **pyarrow 版本无上限** | 中 | `pyarrow>=14.0.0` 没有任何上限约束。如果 pyarrow 18.x 引入了 API 不兼容变更，自动安装会直接破坏项目 |
| **streamlit 落后 3 个版本** | 低 | 1.57.0 vs 1.60.0。虽然差异不大，但 1.58-1.60 可能包含安全修复和 bug 修复 |
| **pip-audit 混入运行时依赖** | 低 | CI 审计工具不应出现在生产依赖中，增大攻击面且浪费镜像空间 |

### 1.3 改进建议

1. **引入 lock 文件**: 执行 `pip freeze > requirements.lock` 或切换到 Poetry/PDM 管理依赖
2. **pyarrow 锁定上限**: 改为 `pyarrow>=14.0.0,<18.0`
3. **streamlit 升级**: 升级到 1.60.0 并回归测试
4. **pip-audit 分离**: 将 pip-audit 从 `requirements.txt` 移到 CI 步骤中独立安装（当前 CI 已经做了，但 requirements.txt 中也包含了，形成重复）

---

## 2. Docker 优化

### 2.1 当前 Dockerfile 分析

```dockerfile
FROM python:3.12-slim                    # 基础镜像 ~150MB
WORKDIR /app
RUN groupadd -r streamlit ...            # 非 root 用户 ✓
COPY filter/requirements.txt .       # 层缓存优化 ✓
RUN pip install --no-cache-dir -r requirements.txt
COPY . .                                 # 复制全部项目文件 ✗
RUN chown -R streamlit:streamlit /app
USER streamlit                           # 非 root 运行 ✓
EXPOSE 8501
HEALTHCHECK ...                          # 健康检查 ✓
CMD ["streamlit", "run", ...]
```

### 2.2 问题诊断

| 问题 | 影响 | 严重度 |
|------|------|--------|
| **无多阶段构建** | 最终镜像包含 pip 缓存残留、不必要的中间层。镜像体积约 500-800MB（估算），多阶段可降至 300-450MB | 中 |
| **`COPY . .` 复制过多** | `.dockerignore` 虽排除了 `.git`, `.pytest_cache`, `__pycache__`, `.gstack`, `.claude`, `venv/`, `*.egg-info/`，但 **未排除 `tests/`, `docs/`, `tools/`, `backtest_output/`**。这些目录包含数百 KB 的测试代码和文档，增加了镜像体积和攻击面 | 中 |
| **python:3.12-slim vs pyproject.toml requires-python>=3.11** | 不一致。pyproject.toml 声明兼容 3.11+，但 Dockerfile 硬编码 3.12。如果用 3.11 的 `FROM` 构建会失败吗？实际上不会——但需求声明的模糊性是个问题 | 低 |
| **缺少 `--require-hashes`** | pip install 不验证包完整性，存在供应链攻击风险 | 中 |
| **docker-compose healthcheck 冗余** | docker-compose.yml 的 `healthcheck` 与 Dockerfile 的 `HEALTHCHECK` 完全重复。Compose 中的 healthcheck 会覆盖镜像中的——实际上 Compose 的优先级更高，这意味着 Dockerfile 的 HEALTHCHECK 在 Compose 中不会生效，维护者可能改了一处忘了另一处 | 低 |

### 2.3 优化方案对比

#### 当前方案 (28 行)

- 构建后镜像大小: 估计 600-800MB
- 构建缓存效率: 中等（pip 层有缓存，但 `COPY . .` 后每次代码变更都重新 pip install）

#### 推荐方案 A: 多阶段构建 + 最小化复制

```dockerfile
# ---- 构建阶段 ----
FROM python:3.12-slim AS builder
WORKDIR /app
COPY filter/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- 运行阶段 ----
FROM python:3.12-slim
WORKDIR /app
RUN groupadd -r streamlit && useradd -r -g streamlit -m -u 1000 streamlit

# 只复制已安装的依赖（不复制 pip 缓存）
COPY --from=builder /root/.local /home/streamlit/.local

# 只复制运行时需要的代码
COPY filter/ ./filter/

# 创建 volume 挂载点
RUN mkdir -p /app/data /app/config && chown -R streamlit:streamlit /app

ENV PATH="/home/streamlit/.local/bin:$PATH"
USER streamlit
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1
CMD ["streamlit", "run", "filter/streamlit_app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
```

**改进效果预估**:
- 镜像大小: 减少 30-40%（约 350-500MB）
- 安全攻击面: 减少（无 pip 缓存、测试代码、文档）
- 构建速度: pip 层缓存命中率不变，COPY 更快

#### 推荐方案 B: 极简优化（最小改动）

如果不想大改，只需修改 3 处：
1. 在 `.dockerignore` 中增加: `tests/`, `docs/`, `tools/`, `backtest_output/`, `*.md`
2. 将 `COPY . .` 改为 `COPY filter/ ./filter/`
3. 在 docker-compose.yml 中删除重复的 `healthcheck:` 块

### 2.4 docker-compose.yml 评估

| 项目 | 状态 | 说明 |
|------|------|------|
| 版本声明 | ✓ | `version: '3.8'` （Compose V1 语法，V2 已废弃 version 字段但仍兼容） |
| 端口映射 | ✓ | `8501:8501` |
| 数据持久化 | ✓ | `./data` 和 `./config` 挂载 |
| 环境变量 | ⚠ | 仅 2 个变量，`LOGURU_LEVEL` 带默认值 |
| 自动重启 | ✓ | `restart: unless-stopped` |
| 资源限制 | ✗ | 未设置 `deploy.resources.limits`（CPU/内存） |
| 日志管理 | ✗ | 无 `logging` 配置，容器日志无限增长 |
| 健康检查重复 | ⚠ | 与 Dockerfile 重复 |
| 网络隔离 | ✗ | 使用默认 bridge 网络 |

---

## 3. CI/CD 配置分析

### 3.1 流水线概览

文件: `.github/workflows/ci.yml` (50 行)

| 阶段 | 内容 | 耗时估计 |
|------|------|---------|
| Checkout + Setup Python | 拉代码、安装 Python | ~30s |
| Cache pip | 缓存恢复 | ~5s |
| Install dependencies | pip install 10+ 个包 | ~60s |
| Security audit | pip-audit | ~15s |
| Lint | ruff check | ~3s |
| Type check | mypy | ~5s |
| Unit tests | pytest (跳过 UI 测试) | ~60-120s |
| UI tests | pytest (仅 UI 测试) | ~120-180s |

**总估算耗时**: 约 5-7 分钟（Python 3.11 + 3.12 矩阵并行）

### 3.2 问题诊断

| 问题 | 影响 | 建议 |
|------|------|------|
| **pip-audit 重复安装** | CI 中显式 `pip install pip-audit`，但 requirements.txt 也已包含。浪费约 5s | 从 requirements.txt 移除 pip-audit，仅 CI 中安装 |
| **pip-audit DB 未缓存** | pip-audit 每次下载完整的漏洞数据库，额外耗时 | 缓存 `~/.cache/pip-audit` |
| **mypy 过于宽松** | `--ignore-missing-imports --follow-imports=skip` 等价于关闭了大部分类型检查。第三方库缺少类型存根时会静默跳过 | 为核心模块（filter_engine, backtest_core）增加更严格的 mypy 配置 |
| **ruff ignore=F841** | F841 是"未使用变量"检测，忽略它意味着未使用的变量不会被 lint 发现。代码质量下降 | 移除 F841 忽略，或仅对个别文件豁免 |
| **测试分两次运行** | pytest 启动和 fixture 初始化执行了两次，浪费约 15-20s | 合并为一次 pytest 调用，用 `-m` 或条件 skip 区分测试 |
| **无 Docker 构建验证** | CI 不验证 Dockerfile 是否可构建，可能在 Docker 部署时才发现问题 | 增加 `docker build` 步骤（至少语法检查） |
| **覆盖率仅 55%** | 与项目规模不匹配。README 声明 623 个测试、50% 覆盖率，说明代码中有大量未测试路径 | 关键路径（filter_engine, backtest_core）应有 70%+ 覆盖 |
| **仅 2 个 Python 版本** | 3.11 和 3.12，缺少 3.13。如果用户升级 Python 可能遇到问题 | 增加 3.13 到矩阵（如果依赖兼容） |

### 3.3 优化后 CI 流程建议

```
Checkout → Setup Python (matrix 3.11/3.12/3.13)
         → Cache pip + pip-audit-db
         → Install deps
         → Lint (ruff) + Format check
         → mypy (分模块严格度)
         → pip-audit
         → pytest (全部测试一次跑完, with cov)
         → docker build --check (dry run)
```

---

## 4. 环境管理

### 4.1 当前状态

| 维度 | 现状 | 评价 |
|------|------|------|
| 环境变量 | `.env.example` 仅 2 个变量 | 过于简陋 |
| 多环境支持 | 无 dev/staging/prod 区分 | 缺失 |
| 配置存储 | SQLite (config_db.py) + JSON 文件 | 非标准 |
| secrets 管理 | 无 secrets 配置 | 当前无敏感信息也算合理 |
| 配置默认值 | docker-compose.yml 中有 `${LOGURU_LEVEL:-INFO}` | 基本满足 |

### 4.2 具体问题

1. **`.env.example` 不完整**: 仅包含 `LOGURU_LEVEL` 和 `STREAMLIT_SERVER_PORT`。实际项目还需要：
   - 数据库路径（当前硬编码，如 `data/market.db`）
   - Parquet 缓存目录
   - 日志文件路径
   - 数据源配置（yfinance 超时、重试参数）

2. **`config/` 目录不存在但 docker-compose 挂载了它**: docker-compose.yml 中有 `- ./config:/app/config`，但项目中 `config/` 目录被 `.gitignore` 排除且当前不存在。Docker 会自动创建空目录，但意图不明确。

3. **SQLite 作为配置存储**: config_db.py 将预设配置存储在 SQLite 中。这种方式无法版本控制，也无法做配置评审（code review）。

4. **硬编码路径分散**: 数据库路径、缓存路径、输出路径分散在各模块中硬编码，没有统一的环境变量抽象层。

5. **无日志级别控制代码集成**: `LOGURU_LEVEL` 在 docker-compose 中定义，但代码中 logger 的初始化需要显式读取该环境变量。如果直接 `pip install` 运行而不通过 docker-compose，该环境变量可能未设置。

### 4.3 改进建议

- **扩展 `.env.example`**: 增加 `DATA_DIR`, `CONFIG_DIR`, `CACHE_DIR`, `BACKTEST_OUTPUT_DIR` 等
- **增加 `config/settings.py`**: 统一从环境变量加载配置，提供默认值
- **`config/` 目录模板化**: 创建 `config/` 目录并放入示例配置文件或 `.gitkeep`
- **多环境支持**: 创建 `.env.dev`, `.env.prod` 模板文件

---

## 5. pre-commit 配置

### 5.1 当前配置

```yaml
repos:
  - ruff-pre-commit v0.11.0: ruff + ruff-format
  - pre-commit-hooks v5.0.0: check-yaml, check-toml, end-of-file-fixer, trailing-whitespace
```

### 5.2 缺失的检查

| 缺失项 | 重要性 | 说明 |
|--------|--------|------|
| `check-added-large-files` | 高 | 防止意外提交大文件（如数据库、Parquet 文件）到 git |
| `detect-private-key` | 高 | 防止 AWS/SSH 私钥泄露 |
| `check-merge-conflict` | 中 | 防止提交含 `<<<<<<<` 合并冲突标记的代码 |
| `check-json` | 低 | 项目有 JSON 配置文件（`data/backtest_config.json` 等） |
| mypy hook | 中 | 类型检查应该尽早发现，不应等到 CI |
| `name-tests-test` | 低 | pytest 约定测试文件名以 `test_` 开头 |

### 5.3 版本检查

- **ruff v0.11.0**: 当前最新 ruff 版本远超 v0.11.0。建议升级以获取新的 lint 规则。
- **pre-commit-hooks v5.0.0**: 这是最新版本，状态良好。

### 5.4 改进建议

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.11.0  # 升级到最新稳定版
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: check-yaml
      - id: check-toml
      - id: check-json
      - id: check-added-large-files        # 新增
        args: ['--maxkb=500']
      - id: detect-private-key             # 新增
      - id: check-merge-conflict           # 新增
      - id: end-of-file-fixer
      - id: trailing-whitespace
```

---

## 6. 配置文件组织

### 6.1 配置全景图

| 文件 | 用途 | 问题 |
|------|------|------|
| `pyproject.toml` | 项目元数据 + ruff/pytest/coverage 配置 | 缺 `[build-system]` 和 `[project.dependencies]` |
| `pytest.ini` | pytest 运行配置 | **与 pyproject.toml 中的 pytest 配置重复** |
| `filter/requirements.txt` | Python 依赖 | 无 hash 锁定 |
| `.pre-commit-config.yaml` | Git 提交前检查 | 缺少安全相关 hooks |
| `.env.example` | 环境变量模板 | 仅 2 个变量 |
| `Dockerfile` | 容器构建定义 | 单阶段，未优化 |
| `docker-compose.yml` | 容器编排 | 无资源限制 |
| `.github/workflows/ci.yml` | CI/CD 流水线 | 效率可优化 |
| `.dockerignore` | Docker 构建排除 | 未排除 tests/docs/tools |
| `.gitignore` | Git 跟踪排除 | 较全面 |
| `data/backtest_config.json` | 回测配置 | 合理的 JSON 配置 |
| `config_db.py` | 运行时预设配置 (SQLite) | 无法版本控制 |

### 6.2 关键问题: pytest 配置重复

**pyproject.toml 中的配置**:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = ["-v", "--tb=short", "--strict-markers"]
```

**pytest.ini 中的配置**:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short --strict-markers
markers =
    slow: marks tests as slow
    filter: filter algorithm tests
    ...
```

**冲突**: 两处都定义了 `testpaths`, `python_files`, `addopts`。pytest 会合并两者，但如果值冲突（如 testpaths 带不带引号），行为可能不明确。更严重的是，`markers` 只在 `pytest.ini` 中定义，但 pyproject.toml 中没有——维护者可能改了 pyproject.toml 但忘了 pytest.ini，或者反之。

**建议**: 保留一个。推荐保留 `pyproject.toml` 中的配置（现代化趋势），删除 `pytest.ini`。将 markers 移到 pyproject.toml：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = ["-v", "--tb=short", "--strict-markers"]
markers = [
    "slow: marks tests as slow",
    "filter: filter algorithm tests",
    "signal: Schmitt trigger and signal tests",
    "strategy: PnL and strategy tests",
    "alignment: time alignment tests",
]
```

### 6.3 pyproject.toml 缺少的元数据

当前 pyproject.toml 缺少：
- `[build-system]`: 未声明构建后端（如 setuptools / hatchling）
- `[project.dependencies]`: 依赖未在 pyproject.toml 中声明（仅在 requirements.txt）
- `[project.optional-dependencies]`: 无开发依赖分组（pytest, ruff, mypy）
- `[project.scripts]`: 无 CLI 入口点（虽然有 backtest_cli.py，但未注册）

这意味着该项目的 `pyproject.toml` 仅用于工具配置，而非标准的 Python 包管理。

---

## 7. 部署策略

### 7.1 当前部署方案

| 方案 | 适用场景 | 说明 |
|------|---------|------|
| 直接运行 | 开发/个人使用 | `pip install -r requirements.txt && streamlit run` |
| Docker Compose | 单机生产 | `docker compose up --build -d` |
| 静态 HTML | 演示/Demo | `python3 -m http.server 8765`，纯前端静态页面 |

### 7.2 安全评估

| 项目 | 状态 | 说明 |
|------|------|------|
| HTTPS | ✗ 缺失 | Streamlit 在 8501 端口无 TLS 终止。生产环境应在前面加 Nginx/Caddy 反向代理 |
| 认证 | ✗ 缺失 | Streamlit 无内置认证。任何人都能访问界面并修改参数 |
| 用户隔离 | ✗ 缺失 | 多用户共享同一 session state |
| 镜像漏洞扫描 | ✗ 缺失 | CI 未集成 Trivy/Snyk 镜像扫描 |
| 非 root 运行 | ✓ | Dockerfile 中切换到 streamlit 用户 |
| 健康检查 | ✓ | 有 HEALTHCHECK |
| Secrets 泄露 | ⚠ | 当前无 API key，但 `.dockerignore` 不排除 `.env`（虽然 `.gitignore` 排除了） |

### 7.3 生产化建议

1. **前置反向代理**: 增加 Nginx/Caddy 容器到 docker-compose.yml，提供 HTTPS 终止和静态文件缓存
2. **认证层**: 对于外网访问，至少增加 Basic Auth 或 Streamlit-Authenticator
3. **资源限制**: 在 docker-compose.yml 中添加:
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '2'
         memory: 2G
       reservations:
         cpus: '1'
         memory: 512M
   ```
4. **日志轮转**: 添加 `logging` 配置防止容器日志撑满磁盘:
   ```yaml
   logging:
     driver: "json-file"
     options:
       max-size: "50m"
       max-file: "3"
   ```
5. **备份策略**: 对 `./data` 卷增加定期备份脚本或 cron job
6. **监控**: 集成 Prometheus + Grafana 或至少 UptimeRobot 外部监控

### 7.4 部署架构推荐

```
Internet → Nginx/Caddy (:443, TLS) → Streamlit (:8501, internal)
                                          ↓
                                    SQLite (./data/*.db)
                                    Parquet (./data/display/)
```

---

## 8. 依赖冲突风险

### 8.1 直接依赖兼容性分析

当前所有直接依赖均为 `==` 精确锁定，**当前版本组合无已知冲突**。但精确锁定意味着没有自动的安全补丁。

### 8.2 传递依赖风险

由于缺少 lock 文件，传递依赖（transitive dependencies）的版本完全不受控制。以下关键传递依赖需要关注：

| 包 | 来源 | 潜在风险 |
|-----|------|---------|
| protobuf | streamlit → protobuf | 版本冲突常见，streamlit 有严格 protobuf 版本要求 |
| pillow | streamlit → pillow | 历史安全漏洞较多 |
| tornado | streamlit → tornado | Web 服务器，历史有安全 CVE |
| werkzeug | streamlit → werkzeug | 开发服务器，有安全公告历史 |
| lxml | pandas → lxml | 有历史 CVE |
| numpy (传递) | scipy, pandas, statsmodels 各需要不同的 numpy | ABI 兼容性问题（特别是 numpy 1.x → 2.x 迁移） |

### 8.3 NumPy 2.x 兼容性

项目使用 `numpy==2.2.4`（NumPy 2.x 系列）。这是一个重要版本跳跃：
- NumPy 2.0 移除了一大批已弃用的 API
- scipy 1.17.1 和 pandas 2.3.3 都已在 CI 中与 numpy 2.2.4 测试通过，兼容性已确认
- 但如果升级/降级 numpy，需要全部重新验证

### 8.4 版本升级路径

如果要批量升级依赖，风险排序（从低到高）：

1. **loguru 0.7.3**: 已是最新，无需升级
2. **pyarrow**: 从 14.x 升级到最新（需增加上限约束后再升级）
3. **plotly 6.7.0**: 小版本升级风险低
4. **streamlit 1.57.0 → 1.60.0**: 3 个 minor 版本，需要回归测试 UI
5. **numpy/scipy/pandas/statsmodels**: 科学计算栈互相耦合，应**捆绑升级**并在全矩阵 CI 中验证

---

## 附录 A: 问题汇总与优先级

### P0 — 立即修复

| # | 问题 | 文件 | 预计时间 |
|---|------|------|---------|
| 1 | pytest 配置重复（pyproject.toml vs pytest.ini） | 删除 pytest.ini，合并到 pyproject.toml | 10min |
| 2 | `.dockerignore` 缺少 tests/docs/tools | 增加排除项 | 2min |
| 3 | docker-compose.yml 重复 healthcheck | 删除 compose 中的 healthcheck 块 | 1min |

### P1 — 近期改进

| # | 问题 | 文件 | 预计时间 |
|---|------|------|---------|
| 4 | 生成 requirements lock 文件 | 新建 `requirements.lock` | 5min |
| 5 | pyarrow 增加版本上限 | requirements.txt | 1min |
| 6 | pip-audit 从 requirements.txt 分离 | requirements.txt + ci.yml | 5min |
| 7 | pre-commit 增加 check-added-large-files + detect-private-key | .pre-commit-config.yaml | 2min |
| 8 | CI 中 ruff 移除 F841 忽略 | ci.yml | 1min |
| 9 | CI 增加 Docker 构建验证 | ci.yml | 10min |

### P2 — 架构优化

| # | 问题 | 涉及 | 预计时间 |
|---|------|------|---------|
| 10 | Dockerfile 多阶段构建 | Dockerfile | 30min |
| 11 | 统一配置管理（settings.py） | 新建文件 | 1h |
| 12 | docker-compose 增加资源限制和日志轮转 | docker-compose.yml | 5min |
| 13 | 增加生产级反向代理（Nginx/Caddy） | Dockerfile + compose | 1h |
| 14 | CI 增加 pip-audit 缓存 | ci.yml | 5min |
| 15 | 扩展 .env.example | .env.example | 5min |

---

## 附录 B: Dockerfile 优化前后对比

| 指标 | 当前 | 优化后 (方案A) | 改善 |
|------|------|---------------|------|
| 镜像大小 | ~600-800MB | ~350-500MB | -35% |
| 构建层数 | 7 | 6 | -1 |
| 生产代码大小 | 包含 tests/docs/tools | 仅 filter/ | -60%+ |
| 攻击面 | 含 pip 缓存 | 无 pip 缓存 | 减少 |
| pip 层缓存命中 | 代码变更时失效 | 代码变更不影响 pip 层 | 更稳定 |
| 安全扫描耗时 | 正常 | 减少 30% | 更快 |

---

## 附录 C: 评分总览

| 维度 | 评分 | 评语 |
|------|------|------|
| 依赖管理 | 6/10 | 无 lock 文件，pyarrow 无上限，pip-audit 混入生产依赖 |
| Docker | 6/10 | 有安全意识(非 root)，但单阶段、复制过多 |
| CI/CD | 7/10 | 矩阵测试好，但有重复安装和过于宽松的检查 |
| 环境管理 | 4/10 | 仅有 2 个环境变量，无多环境支持，配置分散 |
| pre-commit | 6/10 | 有 ruff，但缺少安全 hooks |
| 配置组织 | 5/10 | pytest 配置重复，pyproject.toml 不完整 |
| 部署策略 | 5/10 | 功能可用，缺 HTTPS、认证、资源限制 |
| 依赖冲突 | 7/10 | 精确锁定降低了风险，但无 lock 文件 |

**综合评分: 5.8/10** — 项目在单机/个人使用场景下可正常运行，但离生产级部署有一定差距。核心痛点是配置管理的散乱和容器化不够优化。
