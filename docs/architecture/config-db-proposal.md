# 配置管理方案：DB + UI 预设选择器

## 1. 摘要

推荐将当前基于 JSON 文件的配置管理迁移至 SQLite 存储，同时在 Streamlit 侧边栏添加预设选择器 UI。核心交互为：用户从下拉框中选择预设名称，系统一键填充所有配置参数至 `st.session_state`，无需手动拖拽滑块反复调试。预设支持快速保存、按标的（ticker-variant）自动匹配、变更历史追溯与一键回滚。现有 JSON 导入/导出功能保持兼容，DB 写入作为透明同步层，迁移成本约 80-100 行新增代码。

---

## 2. 现状与规模

### 2.1 当前参数总量

| 层级 | 4视图(单滤波器) | 4视图(dual 滤波器) |
|------|:-:|:-:|
| 固定视图参数（时间框、N 点、Schmitt、Prediction 等） | 56 | 60 |
| 滤波器参数（各滤波器独有滑块） | 4-12 | 8-24 |
| 全局参数（市场、代码、滤波器类型） | 9 | 9 |
| **总计** | **69-77** | **77-93** |

### 2.2 现有配置数据

- 10 个 JSON 文件，每个 ~77 key 的扁平 dict
- 文件名约定：`{TICKER}_{EXCHANGE}.json`，变体后缀 `_DP`（dual filter）、`_QS`
- 10 个注册滤波器：SMA(1)、EMA(1)、WMA(1)、ALMA(3)、Savgol(2)、Kalman(2)、Butterworth(2)、Gaussian(1)、Median(1)、LOWESS(1) — 共 15 个独有参数名

### 2.3 扩展预期

- 50 标的 x 3 变体 = 150 条配置
- 150 x 80 参数 = 12,000 个参数值
- JSON 分散存储：~375 KB（150 文件） vs SQLite 集中存储：~30 KB
- 大规模下 JSON 管理困难（查找、版本对比、批量操作均需额外工具），DB 方案优势明显

### 2.4 现有 DB 基础设施

- `filter_app/db.py` 提供 `DB_PATH`（`data/market.db`）和 `get_conn()`（WAL 模式，`sqlite3.Row` factory）
- 当前仅 1 张表 `kline(ticker, timeframe, ts, open, high, low, close, volume)`
- 扩展 3 张配置表即可，复用现有连接管理逻辑

---

## 3. DB Schema 设计

### 3.1 DDL

```sql
-- 预设定义表：存储命名的配置模板
CREATE TABLE IF NOT EXISTS config_presets (
    preset_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    description  TEXT    DEFAULT '',
    category     TEXT    DEFAULT 'general',    -- 'general', 'single', 'dual', 'quick'
    params_json  TEXT    NOT NULL,              -- 完整配置的 JSON 序列化
    created_at   TEXT    DEFAULT (datetime('now','localtime')),
    updated_at   TEXT    DEFAULT (datetime('now','localtime'))
);

-- 标的-预设关联表：按 ticker+variant 存储当前有效预设
-- variant 含义: 'single' 单滤波器, 'dual' 双滤波器, 'qs' 快速配置
CREATE TABLE IF NOT EXISTS config_ticker (
    ticker       TEXT    NOT NULL,
    variant      TEXT    NOT NULL DEFAULT 'single',  -- 'single','dual','qs','custom'
    market       TEXT    DEFAULT '',
    preset_id    INTEGER REFERENCES config_presets(preset_id),
    params_json  TEXT    DEFAULT '',                 -- 当不使用预设时直接存储参数
    updated_at   TEXT    DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ticker, variant)
);

-- 配置变更历史表：记录每次参数变更的 diff
CREATE TABLE IF NOT EXISTS config_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL,
    variant      TEXT    NOT NULL DEFAULT 'single',
    preset_id    INTEGER,                            -- 变更后关联的预设 ID（可为空）
    old_json     TEXT    DEFAULT '',                  -- 变更前完整配置
    new_json     TEXT    DEFAULT '',                  -- 变更后完整配置
    changed_at   TEXT    DEFAULT (datetime('now','localtime')),
    source       TEXT    DEFAULT 'ui',                -- 'ui', 'import', 'preset_apply', 'rollback'
    FOREIGN KEY (ticker, variant) REFERENCES config_ticker(ticker, variant)
);
```

### 3.2 设计说明

- **config_presets**：独立预设集市，预设与标的解耦。一条预设可供多个标的复用（例如对所有港股使用同一套参数模板）。`name` UNIQUE 约束防止重名覆盖。`category` 支持按滤波器模式分类。
- **config_ticker**：标的-变体的当前状态。`preset_id` 指明当前链接的预设；当用户手动调整参数后（不再与预设同步），`params_json` 保存本地覆盖值，`preset_id` 置 NULL。PRIMARY KEY 为 `(ticker, variant)`，天然避免重复。
- **config_history**：变更日志，支持追溯和回滚。`old_json` / `new_json` 保存完整快照以简化回滚逻辑。`source` 字段区分触发来源，便于审计。

---

## 4. UI 交互设计

### 4.1 预设选择器（下拉框 → 一键应用）

在侧边栏**参数面板之前**插入以下区域：

```
┌─ 配置管理 ──────────────────────┐
│  [导入配置] (file_uploader)     │  ← 保留现有功能
│                                 │
│  当前预设: [SMA+EMA 快线 ▼]     │  ← 新：预设下拉选择器
│  [保存当前配置]  [删除预设]     │  ← 新：操作按钮
│                                 │
│  ┌ 配置变更历史 ──────────┐     │  ← 新：可折叠展示
│  │ 2026-06-27 15:30 导入  │     │
│  │ 2026-06-27 14:20 预设  │     │
│  │ [回滚到此版本]          │     │
│  └─────────────────────────┘     │
│                                 │
├─ 参数面板 ──────────────────────┤
│  市场: ● 美股  ○ A股  ○ 港股   │
│  ...                            │
└─────────────────────────────────┘
```

**交互流程：**

1. 用户从 `st.selectbox` 选择预设后，触发回调（`on_change`）
2. 回调函数 `apply_preset(name)`：
   - 查询 `config_presets WHERE name = ?` 获取 `params_json`
   - 反序列化后逐 key 写入 `st.session_state` 和 `st.session_state[f"_imp_{k}"]`
   - 调用 `st.rerun()` 刷新 UI
3. 用户切换 tiker 时，自动查询 `config_ticker` 是否有匹配记录：
   - 有 -> 自动选中对应预设
   - 无 -> 不清空，允许用户手动选择

**预设列表排序：** 按 `updated_at DESC`，最近使用的排最前。

### 4.2 保存当前配置为预设

- 用户调整完参数后点击 **"保存当前配置"** 按钮
- `st.popover` 或 `st.text_input` 弹出命名对话框
- 输入预设名称 + 可选描述
- 点击确认后：
  1. 调用 `collect_current_params()` 从 `st.session_state` 采集当前所有参数
  2. 序列化为 JSON
  3. `INSERT OR REPLACE INTO config_presets`（同名则覆盖）
  4. 同时 `UPSERT config_ticker` 记录当前 `(ticker, variant)` 的关联
  5. 写入 `config_history` 记录变更
  6. 刷新预设下拉框

### 4.3 配置变更历史与回滚

- 以 `st.expander` 包裹，默认折叠
- 查询 `config_history WHERE ticker=? AND variant=? ORDER BY changed_at DESC`
- 每条记录展示：时间、来源、操作类型
- 每条记录右侧 **"回滚到此版本"** 按钮：
  1. 读取 `old_json`（前置版本）或 `new_json`（目标版本）
  2. 写入 `st.session_state`
  3. 插入一条新的历史记录：`source='rollback'`
  4. `st.rerun()`

### 4.4 与 JSON 导入导出兼容

- **导入时**（现有 `file_uploader`）：完成现有逻辑后，额外调用 `config_db.import_to_db(params_dict)`，自动：
  - 创建/更新对应 `config_ticker` 记录
  - 写入 `config_history`，`source='import'`
  - 如果参数与已有 90% 以上相似，不创建新预设（避免垃圾预设）；否则提示用户命名保存
- **导出时**（现有 `download_button`）：保留完全不变，DB 中的数据仅供 UI 侧消费
- **启动时**：用户未选预设时，检查 `config_ticker` 中当前 `(ticker, variant)` 行是否存在参数，存在则自动加载

---

## 5. 模块设计

### 5.1 文件位置

**新建文件：** `filter_app/config_db.py`

- 与 `db.py` 同级，避免新建目录导致的命名冲突（上次教训：新建 `config/` 目录与 Streamlit 内置 `config.py` 冲突）
- 备选命名：`cfg_mgr.py`（若 `config_db` 仍然有冲突风险）

### 5.2 复用方式

```python
from db import DB_PATH, get_conn
```

直接引用 `db.py` 中的 `get_conn()`，避免重复创建连接逻辑。DB 文件统一在 `data/market.db`。

### 5.3 核心 API 接口

```python
# ── 预设管理 ──
def list_presets(category: str = None) -> list[dict]
    """列出所有预设，支持按 category 过滤。返回 [{preset_id, name, description, category, updated_at}]。"""

def get_preset(name: str) -> dict | None
    """按名称获取预设完整信息，包括 params_json（反序列化为 dict）。"""

def save_preset(name: str, params: dict, description: str = "",
                category: str = "general") -> int
    """保存预设。同名则覆盖 (INSERT OR REPLACE)。返回 preset_id。"""

def delete_preset(name: str) -> bool
    """删除预设。同时将关联的 config_ticker.preset_id 置 NULL。"""
    # 避免删除后 config_ticker 悬挂无效外键

# ── 标的配置 ──
def upsert_ticker_config(ticker: str, variant: str, market: str,
                         preset_id: int = None, params: dict = None)
    """写入或更新标的-变体的当前配置。"""

def get_ticker_config(ticker: str, variant: str) -> dict | None
    """查询指定标的的当前配置。如果关联了 preset，合并 preset 参数后返回。"""

def delete_ticker_config(ticker: str, variant: str = None)
    """删除标的配置。variant 为 None 时删除该标的所有变体。"""

# ── 历史与回滚 ──
def record_history(ticker: str, variant: str, old_json: str, new_json: str,
                   preset_id: int = None, source: str = "ui")
    """写入一条变更历史。"""

def get_history(ticker: str, variant: str, limit: int = 20) -> list[dict]
    """获取变更历史，按 changed_at DESC 排序。"""

def rollback_to(history_id: int) -> dict | None
    """回滚到指定历史版本。返回回滚后的 params dict（old_json 内容）。"""
    # 读取 history_id 行的 old_json，然后 INSERT 一条新历史记录

# ── 运行态工具 ──
def collect_current_params(session_state) -> dict
    """从 st.session_state 采集当前所有活动参数（与导出逻辑同步的 key 集）。"""

def apply_params_to_session(params: dict, session_state)
    """将 params dict 写入 st.session_state，自动添加 _imp_ 备份 key。"""
```

---

## 6. 与现有代码集成

### 6.1 改动点

| 改动位置 | 行号范围 | 内容 | 行数 |
|:--|:--|:--|:--:|
| `filter_app/streamlit_app.py` import 区 | ~顶部 | `from config_db import *` | +1 |
| JSON 导入块 | ~L1931-1943 | 导入成功后调用 `import_to_db(session_state)` | +4 |
| 参数面板前（预设选择器） | ~L1920-1960 | 预设选择器 UI：selectbox + 操作按钮 + 回调 | +30 |
| 视图渲染后 / 参数面板底部 | ~L2260 | "保存为预设"按钮 + 弹窗 | +15 |
| DB 备份区前 | ~L2295-2320 | 配置历史 expander + 回滚按钮 | +25 |
| 导出块附近 | ~L2262-2295 | 调用 `upsert_ticker_config()` 同步 | +5 |
| 新建文件 | `filter_app/config_db.py` | 模块文件 | ~120 |

**总计新增约 80-100 行（应用层）+ 120 行（模块层）= ~200 行。**

### 6.2 不修改的部分

- 现有的 `_render_params()` 和 `_render_param_slider()` 完全不动
- 现有的配置文件 `db.py` 不动
- 现有的 JSON 导入/导出函数逻辑不动，仅追加 notify 调用
- 现有的测试文件全部保持可用

---

## 7. 实施路径

### Phase 1: 预设管理（可独立上线）

**目标：** 用户可以通过下拉框选择预设，一键填充参数。先不要求持久化到 ticker 级别。

**改动：**
1. 新建 `filter_app/config_db.py`，实现：
   - `list_presets()`、`get_preset()`、`save_preset()`、`delete_preset()`
   - `collect_current_params()`、`apply_params_to_session()`
2. 在 `streamlit_app.py` sidebar 插入预设选择器 UI：
   - `st.selectbox` 列出预设
   - `on_change` 回调触发 `apply_params_to_session()`
   - "保存当前配置" 按钮 → `save_preset()`
   - "删除预设" 按钮（带确认）
3. 初始化时机：`save_preset()` 首次写入时自动建表

**验证标准：**
- 选择预设后，所有滑块、下拉框、复选框自动更新为预设值
- 保存预设后，刷新页面再次选择可见
- 删除预设后，下拉框消失

### Phase 2: 标的配置持久化

**目标：** `config_ticker` 表启用。切换标的时自动匹配之前保存的预设。

**改动：**
1. 实现 `upsert_ticker_config()` 和 `get_ticker_config()`
2. 在 `main()` 的 ticker 切换逻辑处（`st.text_input` 更新后），调用 `get_ticker_config()`，自动选中匹配预设
3. 在导出侧边，追加 `upsert_ticker_config()` 同步

**验证标准：**
- 为 AAPL 选择"快速响应"预设并确认后，切换到 2382 再切回 AAPL，预设自动选中
- DB 文件 `data/market.db` 中 `config_ticker` 表有正确记录

### Phase 3: 历史与回滚

**目标：** 配置变更可追溯，支持一键回滚。

**改动：**
1. 实现 `record_history()`、`get_history()`、`rollback_to()`
2. 在 `save_preset()` 和 JSON 导入流程中自动写入历史
3. 添加 `st.expander` 展示历史列表
4. 每条记录后跟 "回滚" 按钮

**验证标准：**
- 每次保存预设或导入 JSON 后，`config_history` 表新增记录
- 回滚后所有参数恢复，且历史记录新增一条 `source='rollback'`
- 回滚后再回滚可双向操作

---

## 8. 风险评估

### 8.1 命名冲突 — 已规避

| 风险 | 措施 |
|:--|:--|
| 新建 `config/` 目录与 Streamlit 内置 `config.py` 冲突 | 模块文件放在 `filter_app/` 下，命名为 `config_db.py`（不叫 `config.py`）|
| 与 `db.py` 循环导入 | `config_db.py` 单方向引用 `db.py`，`db.py` 不引用 `config_db.py` |
| 与 `streamlit_app.py` 循环导入 | `streamlit_app.py` 引用 `config_db.py`，反之不引 |

### 8.2 SQLite 并发

- 当前架构为本地单用户模式，无需多进程并发
- `get_conn()` 已启用 WAL 模式、`busy_timeout=5000`，读写不阻塞
- 多表写入时建议使用事务包裹

### 8.3 迁移成本

- 应用层新增 ~80-100 行，模块 ~120 行，总计 ~200 行
- 零重构：不动现有 JSON 逻辑，DB 层为增量添加
- 零迁移：现有 JSON 文件无需转换，用户可选择逐步迁移到预设系统
- 若后续要批量迁移已有 JSON 到预设，可写一个一次性脚本 `tools/migrate_configs.py`

### 8.4 用户操作陷阱

| 陷阱 | 缓解措施 |
|:--|:--|
| 用户修改参数后忘记保存，切换预设丢失修改 | 切换预设前检查当前参数与预设是否一致，不一致时提示 |
| 预设被他人覆盖 | 单用户场景低风险；后续可加 `updated_at` 冲突检测 |
| 误删预设 | 删除操作弹窗确认 + `config_history` 保留最后一次快照可恢复 |
| 大量历史记录膨胀 | 默认保留最近 200 条，可配置上限 |

---

## 附录 A：未采纳方案对比

| 方案 | 未采纳原因 |
|:--|:--|
| 纯 JSON 文件 + Git 版本管理 | JSON 分散、对比困难、非技术用户无法操作 Git、多标的扩展性差 |
| MySQL/PostgreSQL | 本地单用户场景过度设计，增加环境依赖 |
| Redis / KV store | 参数需要持久化和历史追溯，不需要高性能缓存 |
| 全量 reload（每次切换预设刷新整个页面） | `st.rerun()` 已满足需求，无需额外开销 |
| 预设嵌套（预设继承另一个预设） | 复杂度高，需求不明确，推迟到有明确用例时 |

---

## 附录 B：与现有代码的关键对齐点

JSON 导出时 key 命名格式（flat dict with `v{i}_` prefix），`collect_current_params()` 必须与导出逻辑使用相同的 key 集。当前导出 key 集在 `streamlit_app.py` ~L2264-2295，新模块应直接复用该段逻辑或提取为公共函数，避免两份 key 定义不同步。

当前 `_imp_` 双 key 备份机制需要保留。`apply_params_to_session()` 必须同时写入主 key 和 `_imp_{key}`。
