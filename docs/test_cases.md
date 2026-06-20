# 多周期过滤策略系统 — 测试用例规格

**版本**: v1.2.0 | **应用类型**: Streamlit | **最后更新**: 2026-06-20

---

## 1. 概述

### 1.1 测试范围

本文档覆盖多周期过滤策略系统的完整测试规格，包含三大测试域：

| 测试域 | TC 组数 | 子用例数 | 覆盖范围 |
|--------|---------|----------|----------|
| UI 交互测试 | 6 | 35 | Streamlit 前端交互：折叠/展开、Checkbox 依赖链、配置导入导出、时间窗口导航、参数滑块边界、周期切换 |
| 数据计算测试 | 6 | 37 | 10 种滤波算法、Schmitt 触发器、抛物线拟合(2种方法)、PnL 回测引擎、时间对齐模块、边界条件 |
| 跨周期测试 | 7 | 7 | 周期映射、PnL 数据对齐、渲染顺序、高周期切换、session_state 缓存、多视图共存、边界条件 |

### 1.2 测试策略

1. **P0（阻塞级）**：核心计算逻辑正确性，必须全部通过才能发布
2. **P1（高优先级）**：主要交互流程与边界条件，每次回归必须覆盖
3. **P2（中优先级）**：极端边界与压力场景，大版本发布前覆盖

### 1.3 优先级定义

| 优先级 | 定义 | 阻塞发布 | 回归频率 |
|--------|------|----------|----------|
| P0 | 核心功能正确性，失败则系统不可用 | 是 | 每次提交 |
| P1 | 主要交互路径，失败则用户体验严重受损 | 是 | 每次回归 |
| P2 | 边界条件与压力场景 | 否 | 大版本发布 |

### 1.4 Bug 回归矩阵

| Bug ID | 描述 | 关联 TC | 验证通过标准 |
|--------|------|---------|------------|
| BUG-001 | 页面崩溃/无响应 | TC-UI-04, TC-UI-06, TC-DATA-01.6~7 | 前移/周期切换/空数据无崩溃 |
| BUG-002 | 时间窗口计算错误 | TC-UI-04d, TC-UI-06f, TC-DATA-05.1~3 | 所有8周期时间窗口正确，时区对齐无误 |
| BUG-003 | Checkbox 级联状态异常 | TC-UI-02a~e | 16种组合全部验证，disabled 状态正确 |
| BUG-004 | 配置导入导出失败 | TC-UI-03a~e | 往返测试通过，边界条件处理正确 |
| BUG-005 | 折叠后颜色/参数丢失 | TC-UI-01d, TC-UI-03d | 折叠后所有参数值(含颜色)保留 |
| BUG-006 | 折叠/展开滤波参数漂移 | TC-UI-01e | slider 极值折叠后不漂移 |
| BUG-007 | cross_pnl 依赖链断裂 | TC-UI-02d, TC-UI-03b, CROSS-04 | 依赖链 + 导入含 cross_pnl 字段正确 |
| BUG-008 | 极端参数显示异常 | TC-UI-05a~i | 极值无崩溃、无显示异常 |
| BUG-009 | 参数滑块越界 | TC-UI-05a~h | 所有 slider 在 min/max 范围内工作正常 |

---

## 2. UI 交互测试 (6个TC, 35个子用例)

### TC-UI-01: 展开/折叠稳定性

- **优先级**: P1
- **关联Bug**: BUG-003, BUG-006
- **前置条件**: Streamlit 应用已启动，已加载 AAPL 股票，默认 Savgol 滤波器，4 视图均已初始化

#### TC-UI-01a: 单视图折叠→展开

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 记录视图1的当前参数：n_pts、k_ε、σ_min、N_EWMA、滤波参数（窗口大小、多项式阶数）、颜色值、预测点数、止损阈值、show_strategy、show_cross_pnl 的 checkbox 状态 | 所有参数值已记录，无异常 |
| 2 | 点击视图1的 **"▲"按钮**（折叠按钮，位于参数栏最右侧） | 面板折叠：施密特参数、预测参数、策略参数、滤波参数四个 expander 全部收起 |
| 3 | 检查折叠后图表是否正常渲染 | 图表区域正常显示，无崩溃，无空白 |
| 4 | 再次点击同一视图的 **"▼"按钮**（展开按钮，位置不变仅为图标变化） | 面板展开：四个 expander 全部展开 |
| 5 | 检查所有参数值是否与步骤1记录的一致 | **所有参数值完全一致**，无重置、无偏移 |
| 6 | 检查图表是否正常 | 无 rerun 循环，图表正常 |

#### TC-UI-01b: 多视图同时折叠

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 依次记录视图1~4的所有参数值 | 记录完整 |
| 2 | 依次点击视图1、2、3、4的折叠按钮 | 四视图全部折叠 |
| 3 | 检查每个视图的 expander 状态 | 全部收起 |
| 4 | 检查底部2×2图表网格 | 4个图表窗口全部正常渲染，无重叠 |
| 5 | 依次点击视图1、2、3、4的展开按钮 | 四视图全部展开 |
| 6 | 逐一对照步骤1的记录 | 参数完全保留 |

#### TC-UI-01c: 全折叠→全展开循环（压力测试）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 记录视图1所有参数值 | 基线已记录 |
| 2 | 循环操作：折叠视图1 → 展开视图1 → 折叠 → 展开，重复5次 | 每次 rerun 正常，无 `st.rerun` 死循环 |
| 3 | 完成后检查所有参数与步骤1是否一致 | 参数完全一致，无漂移 |

#### TC-UI-01d: 折叠状态下修改全局参数

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 折叠视图1~4 | 四视图折叠 |
| 2 | 在侧边栏切换滤波器（savgol → kalman） | 参数面板切换，图表更新 |
| 3 | 逐个展开各视图 | 各视图已使用新滤波器参数，无残留旧参数 |

#### TC-UI-01e: 折叠/展开时 slider 值不漂移

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 设置视图1 n_pts = 50（最小值附近） | 滑块定位在50 |
| 2 | 折叠后展开 | n_pts 仍为50 |
| 3 | 设置 n_pts = 300（最大值） | 滑块定位在300 |
| 4 | 折叠后展开 | n_pts 仍为300 |
| 5 | 切换滤波器为 ALMA → 设置 sigma=15.3, offset=0.33 | 精确值输入 |
| 6 | 折叠后展开 | sigma, offset 精确值保留 |

- **验证方式**: 人工观察，截取折叠前后的参数截图对比

---

### TC-UI-02: Checkbox 依赖链

- **优先级**: P1
- **关联Bug**: BUG-003, BUG-007
- **前置条件**: 应用已加载，默认 Savgol 滤波，双滤波对比关闭（global_dual=false），策略参数不展开

#### TC-UI-02a: 独立控制（Schmitt 开关）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 确保视图1 show_sch = true | 施密特参数 expander 可见，预测 checkbox 可见，图表显示 sig_t 子图 |
| 2 | 取消勾选 **"施密特"** 复选框 | 施密特参数 expander 消失，预测 checkbox（行内）消失，图表取消 sig_t 子图（从5行降为4行） |
| 3 | 重新勾选 **"施密特"** | 恢复步骤1状态 |
| 4 | 检查其他视图 | 不受影响 |

#### TC-UI-02b: show_sch → show_pred 级联

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | show_sch = true, show_pred = true | 预测参数 expander 可见，图表有预测曲线（橙色实线+紫色虚线） |
| 2 | 保持 show_sch = true，取消 show_pred | 预测参数 expander 消失，策略参数 expander 消失，图表预测曲线消失 |
| 3 | 重新勾选 show_pred | 恢复步骤1状态 |
| 4 | show_sch = false → 此时 show_pred 行内 checkbox 不可见 | 预测 checkbox 不显示（受 show_sch 守卫），图表恢复到4行 |

#### TC-UI-02c: show_pred → show_strategy 级联

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | show_sch = true, show_pred = true | 策略参数 expander 可见 |
| 2 | 勾选 **"启用策略叠加"** | show_cross_pnl checkbox 变为可用（disabled=false），PnL 图表新增一行（6行或7行），显示做多/做空双曲线 |
| 3 | 取消 **"启用策略叠加"** | show_cross_pnl checkbox 变为禁用（disabled=true，但值保留），PnL 子图消失 |

#### TC-UI-02d: show_strategy → show_cross_pnl 级联

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 确保策略已启用（show_strategy = true） | show_cross_pnl checkbox 可用（disabled=false） |
| 2 | 勾选 **"显示高周期PnL参考"** | 图表底部新增一行跨周期 PnL 参考子图（7行），显示上一级时间框架的交易标记 |
| 3 | 取消 **"显示高周期PnL参考"** | 跨周期子图消失，图表恢复到6行 |
| 4 | 取消 show_strategy | show_cross_pnl 变为 disabled=true 但状态值保留不丢失 |
| 5 | 重新启用 show_strategy | show_cross_pnl 恢复到步骤2/3设置的值，非强制复位 |

#### TC-UI-02e: 全组合交叉验证（16种组合）

按以下组合逐一测试：

| # | show_sch | show_pred | show_strategy | show_cross_pnl | 预期子图行数 |
|---|----------|-----------|---------------|----------------|-------------|
| 1 | false | N/A | N/A | N/A | 4 |
| 2 | true | false | N/A | N/A | 5 |
| 3 | true | true | false | N/A | 5 |
| 4 | true | true | true | false | 6 |
| 5 | true | true | true | true | 7 |
| 6-16 | 其他组合 | (同上规律) | | | 见规则 |

组合规则说明：
- show_sch=false：无论下游如何，均显示4行（无Schmitt相关子图）
- show_pred=false：策略和跨周期被级联禁用
- show_strategy=false：跨周期不可用
- show_cross_pnl=true 但无高周期数据：降级到6行，不显示跨周期子图

验证要点：
1. 无崩溃或异常
2. 子图行数匹配上表
3. 被级联禁用的 checkbox 值在重新启用后保留

- **验证方式**: 人工观察 + 截图对照

---

### TC-UI-03: 配置导入导出往返

- **优先级**: P1
- **关联Bug**: BUG-004, BUG-005
- **前置条件**: 应用已加载，使用 AAPL 股票

#### TC-UI-03a: 导出 → 导入完整往返

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 配置以下非默认参数：视图1 n_pts=80, k_ε=0.25, σ_min=0.08, savgol 窗口=21, 阶数=3, 颜色=#ff6b6b | 所有参数已正确设置 |
| 2 | 视图1：show_strategy=true, stop_loss=3.5%, show_cross_pnl=true | 策略和跨周期已启用 |
| 3 | 视图3 tf 改为"周线" | 已切换 |
| 4 | 点击侧边栏 **"导出配置"** 按钮 | JSON 文件被下载 |
| 5 | 点击 **"刷新数据"** 按钮强制 reload，等待完成后手动修改以下参数：视图1 n_pts=200, k_ε=0.05, savgol 窗口=5 | 参数已变更 |
| 6 | 通过侧边栏 **"导入配置"** 上传步骤4导出的 JSON 文件 | 侧边栏显示 "配置已加载" |
| 7 | 逐一检查所有参数是否恢复为步骤1~3的值 | **完全还原**：n_pts=80, k_ε=0.25, σ_min=0.08, 窗口=21, 阶数=3, 颜色=#ff6b6b, show_strategy=true, stop_loss=3.5%, show_cross_pnl=true, 视图3 tf="周线" |
| 8 | 检查图表 | 图表与导出前完全一致 |

#### TC-UI-03b: 导入含 cross_pnl 参数的配置（v1.1.0 版本字段）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 使用含 `v{0..3}_cross_pnl=true` 字段的配置文件 | show_cross_pnl checkbox 在渲染时被正确设置为 true |
| 2 | 检查依赖链 | show_strategy 也必须为 true |
| 3 | 检查各视图 | 策略启用 + 跨周期启用 |

#### TC-UI-03c: 导入无 cross_pnl 字段的旧配置（降级兼容）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 手动构造旧格式 JSON（删除所有 `_cross_pnl` 键），导入 | 不崩溃，导入成功 |
| 2 | 检查 show_cross_pnl checkbox | 默认为 false |

#### TC-UI-03d: 导入 + 折叠后验证参数保留

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 导出当前配置 | JSON 文件保存 |
| 2 | 刷新页面（F5） | 所有参数恢复默认 |
| 3 | 导入步骤1的文件 | 配置加载 |
| 4 | 折叠→展开所有视图 | 导入的参数值完全保留，无丢失 |
| 5 | 切换股票代码为 MSFT，再切回 AAPL | 参数仍为导入值 |

#### TC-UI-03e: 导入配置边界条件

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 导入无效格式文件（JSON 语法错误） | 侧边栏显示 "导入失败: ..."，现有参数不受影响 |
| 2 | 导入缺少必填字段的 JSON（如仅含 `{}`） | 不崩溃，缺失字段用默认值 |
| 3 | 导入浮点精度过高的值（如 k_ε=0.12345678） | 值被 Streamlit slider 截断处理，不崩溃 |
| 4 | 同一配置文件连续导入两次 | 第二次导入应跳过（hash 比较），不重复执行 |

- **验证方式**: 人工逐项核对参数值 + 图表视觉对比

---

### TC-UI-04: 时间窗口导航

- **优先级**: P2
- **关联Bug**: BUG-001, BUG-002
- **前置条件**: 应用已加载 AAPL 股票，数据范围覆盖至少 6 个月以上历史数据

#### TC-UI-04a: 后移（回到最新）按钮

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 记录当前图表（day_offset=0） | 显示最新数据 |
| 2 | 步长设为 **5天**，点击 **"◀ 前移"** | day_offset 变为 5，图表前移5天。当前显示窗口末端 = data_end - 5天 |
| 3 | 再次前移2次（共3次，offset=15） | 图表逐次前移 |
| 4 | 点击 **"后移 ▶"** 2次 | offset 递减：15→10→5 |
| 5 | 点击 **"最新"** 按钮 | offset 归零，图表恢复到步骤1的最新数据 |
| 6 | 点击 **"最新"**（offset=0） | 按钮 disabled，显示 "已是最新" |

#### TC-UI-04b: 大跨度前移到数据边界

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 设置步长为 **180天** | 步长显示 180天 |
| 2 | 点击 **"◀ 前移"** | offset 增加 180 |
| 3 | 继续前移直到按钮 **"◀ 前移"变 disabled** | 到达数据起始边界，提示 "无更早数据" |
| 4 | 点击 **"最新"** | offset 归零，回到最新 |

#### TC-UI-04c: 不同步长切换

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 设置步长 **1天**，前移3次 | 每次前移1天，偏移累计3天 |
| 2 | 切换步长到 **30天** | 步长改变，当前 offset 不变 |
| 3 | 后移1次 | offset 从3变为0（`max(0, offset-step)` 保护） |

#### TC-UI-04d: 时间窗口 + 周期切换联合

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 视图1 周期="日线"，前移 to offset=30 | 日线图表显示30天前数据 |
| 2 | 视图1 切换到 "60分钟" | 60分钟图表显示对应窗口（offset 共享，按60分钟精度对齐） |
| 3 | 视图1 切换到 "1分钟" | 1分钟图表渲染，日期标记正常（按天分割线） |
| 4 | 点击 **"最新"** | 所有视图归零 |

#### TC-UI-04e: 各周期日期标记正确

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 视图1="日线"，视图2="周线"，视图3="60分钟"，视图4="5分钟" | 各图表 X 轴日期标记符合周期特性：日线=每周一标记，周线=每月首周标记，60分钟/5分钟=每天分界线标记 |

- **验证方式**: 人工观察 + 对比偏移量与图表内容

---

### TC-UI-05: 参数滑块边界

- **优先级**: P2
- **关联Bug**: BUG-008, BUG-009
- **前置条件**: 应用已加载 AAPL，默认参数

#### TC-UI-05a: n_pts 边界

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | n_pts = 20（最小值） | 图表渲染，显示约20个数据点，无空白 |
| 2 | n_pts = 300（最大值） | 图表渲染，显示约300个数据点，无性能问题 |
| 3 | n_pts = 120（默认值） | 恢复到默认 |

#### TC-UI-05b: 滤波参数边界 — Savitzky-Golay

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 滤波器 = Savgol，窗口=5（最小值）+ 阶数=1（最小值） | 滤波曲线平滑，无异常 |
| 2 | 窗口=101（最大值）+ 阶数=5（最大值） | 滤波曲线过平滑，不崩溃 |
| 3 | 窗口=5 但阶数设置为5（此时 order >= window，代码自动 order = window-1 = 4） | 自动降阶处理，不崩溃 |

#### TC-UI-05c: Butterworth 边界

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 滤波器 = Butterworth，cutoff=1.0（最小值） | 强烈低通，曲线很平滑 |
| 2 | cutoff=45.0（最大值） | 弱滤波，曲线接近原始，无崩溃 |

#### TC-UI-05d: Kalman 参数边界

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 滤波器 = Kalman，Q=0.001（最小），R=10.0（最大） | 强烈平滑，滞后大，不崩溃 |
| 2 | Q=1.0（最大），R=0.01（最小） | 几乎无平滑，跟踪紧密，不崩溃 |

#### TC-UI-05e: ALMA 参数边界

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | offset=0.0, sigma=1.0 | ALMA 计算正常，无除零 |
| 2 | offset=1.0, sigma=20.0 | 计算正常 |

#### TC-UI-05f: Schmitt 参数边界

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | k_ε=0.01（最小），σ_min=0.01（最小），N_EWMA=10（最小） | Schmitt 触发器非常敏感，信号频繁切换，不崩溃 |
| 2 | k_ε=0.50（最大），σ_min=0.20（最大），N_EWMA=120（最大） | Schmitt 迟钝，信号少，不崩溃 |

#### TC-UI-05g: 预测参数边界

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 预测点数=1（最小） | 预测曲线极短，不崩溃 |
| 2 | 预测点数=50（最大） | 预测曲线很长，残差子图正常，不崩溃 |

#### TC-UI-05h: 止损阈值边界

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 止损阈值=0.5%（最小） | 策略极易止损，交易频繁，不崩溃 |
| 2 | 止损阈值=10.0%（最大） | 策略极少止损，不崩溃 |

#### TC-UI-05i: 极值组合（压力测试）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 视图1: n_pts=20, Savgol窗口=101, 阶数=5 | 极小窗口 + 极大滤波核，不崩溃 |
| 2 | 视图2: 双滤波对比开启 | 同时渲染两个滤波 |
| 3 | 所有4视图同时使用极端参数 | 页面整体渲染无异常，无崩溃 |

- **验证方式**: 人工观察 + 控制台无报错

---

### TC-UI-06: 周期切换

- **优先级**: P1
- **关联Bug**: BUG-002
- **前置条件**: 应用已加载 AAPL，所有周期数据已获取（8/8 成功）

#### TC-UI-06a: 默认周期视图

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 检查默认4视图周期 | 视图1=日线, 视图2=60分钟, 视图3=15分钟, 视图4=5分钟 |
| 2 | 每个视图图表渲染正常 | 各周期图表正确显示数据且子图完整 |

#### TC-UI-06b: 视图级 TF 切换

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 视图1 周期下拉框从 "日线" 改为 "周线" | 图表立即切换为周线数据，X轴日期标记变为月首标记 |
| 2 | 视图1 改为 "月线" | 月线显示正常，日期标记为年 |
| 3 | 视图1 改为 "1分钟" | 1分钟图表渲染正常，日期标记为每日分界线 |
| 4 | 切换回 "日线" | 恢复日线视图，参数值（n_pts、schmitt、预测参数等）保留 |

#### TC-UI-06c: 高周期不可用时跨周期子图不显示

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 视图1 tf="季线" | TF_HIERARCHY["季线"] = None，无高周期 |
| 2 | 视图1 启用策略、启用跨周期PnL | 跨周期 checkbox 虽被勾选，但图表**不渲染第7行跨周期子图** |
| 3 | 检查图表行数 | 6行（有策略），无跨周期错误 |

#### TC-UI-06d: 跨周期 PnL 缓存时序验证

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 视图1="5分钟", 视图2="15分钟", 视图3="60分钟", 视图4="日线" | 渲染顺序确保高周期先渲染（日线→60分钟→15分钟→5分钟），PnL 数据被缓存到 session_state |
| 2 | 检查低周期视图（5分钟）的跨周期子图 | 如果启用，正确显示 "日线PnL参考" 子图（因日线已先渲染并缓存） |
| 3 | 视图4 从"日线"改为"周线" | 重新排序渲染，周线先渲染，5分钟视图的跨周期参考变为周线 |

#### TC-UI-06e: 跨周期 PnL 时区对齐

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 配置：视图4（日线）启用策略和跨周期PnL | 日线无时区（tz-naive） |
| 2 | 配置：视图1（5分钟或60分钟）启用跨周期PnL | 日内数据有时区（HKT），对齐函数内部去时区 |
| 3 | 检查5分钟视图的跨周期子图 | 事件标记正确对齐到日线数据，无时区错位 |

#### TC-UI-06f: 全部 8 个周期轮换

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 视图1 → 1分钟 | 渲染正常 |
| 2 | 视图1 → 5分钟 | 渲染正常 |
| 3 | 视图1 → 15分钟 | 渲染正常 |
| 4 | 视图1 → 60分钟 | 渲染正常 |
| 5 | 视图1 → 日线 | 渲染正常 |
| 6 | 视图1 → 周线 | 渲染正常 |
| 7 | 视图1 → 月线 | 渲染正常 |
| 8 | 视图1 → 季线 | 渲染正常 |
| 9 | 遍历周期过程中无崩溃，切换后参数保留 | 全部通过 |

- **验证方式**: 人工逐周期切换 + 控制台无报错

---

## 3. 数据计算测试 (6个TC, 37个子用例)

### TC-DATA-01: 滤波算法测试 (10个子用例)

覆盖 10 种滤波算法的核心计算逻辑：SMA、EMA、WMA、ALMA、Kalman、Butterworth、Median 及各算法边界条件。

#### TC-DATA-01.1: SMA — Constant Signal

- **优先级**: P0
- **函数**: `apply_sma`
- **输入**: `signal = [5.0, 5.0, 5.0, 5.0, 5.0]`, `t = [0,1,2,3,4]`, `window=3`
- **预期输出**: `window=3`（偶数自动+1），kernel = `[1/3, 1/3, 1/3]`，`np.convolve(signal, kernel, mode="same")`。中心值 = 5.0，边缘值因零填充近似 5.0，全部值有限且接近 5.0（浮点容差内）
- **验证**: `not np.any(np.isnan(result))` 且 `result[2] == 5.0`

#### TC-DATA-01.2: SMA — Window > Signal Length

- **优先级**: P1
- **函数**: `apply_sma`
- **输入**: `signal = [1.0, 2.0, 3.0]`, `window=10`
- **预期输出**: `window = min(window, len(signal))` → window=3，输出长度=3，无 NaN，无错误
- **验证**: `len(result) == 3` 且 `not np.any(np.isnan(result))`

#### TC-DATA-01.3: EMA — Constant Signal

- **优先级**: P0
- **函数**: `apply_ema`
- **输入**: `signal = [10.0, 10.0, 10.0, 10.0, 10.0]`, `span=10`
- **预期输出**: `DataFrame.ewm(span=10, adjust=False).mean()` → `[10.0, 10.0, 10.0, 10.0, 10.0]`（常值信号EMA衰减到均值）
- **验证**: `np.allclose(result, 10.0)`

#### TC-DATA-01.4: WMA — Noisy Sinusoid Smoothing

- **优先级**: P1
- **函数**: `apply_wma`
- **输入**: `signal = sin(2pi * 0.05 * t) + N(0, 0.5)` (100点), `window=11`
- **预期输出**: (1) 长度=100，(2) 无 NaN，(3) 方差低于输入（平滑后），(4) 粗糙度（二阶差分平方和）< 输入粗糙度
- **验证**: `len(result) == 100`，`np.var(result) < np.var(signal)`，`np.all(np.isfinite(result))`

#### TC-DATA-01.5: ALMA — Window=1

- **优先级**: P1
- **函数**: `apply_alma`
- **输入**: `signal = [1,5,3,8,2]`, `window=1`
- **预期输出**: m=0, s=1/6, weights=[1], convolve 结果 = 原信号 `[1,5,3,8,2]`
- **验证**: `np.array_equal(result, signal)`

#### TC-DATA-01.6: 所有滤波器 — 空数组输入

- **优先级**: P1
- **函数**: 全部 10 种滤波函数
- **输入**: `signal = np.array([])`, `t = np.array([])`
- **预期输出**: 返回空数组或抛出可捕获异常，不产生系统级错误（segfault 等）。使用 `t[1]-t[0]` 的滤波器（Kalman）可能抛出 `IndexError`，属于可接受行为
- **验证**: 每个函数用 try/except 包裹，无不控制的崩溃

#### TC-DATA-01.7: 所有滤波器 — 全 NaN 信号

- **优先级**: P1
- **函数**: 全部 10 种滤波函数
- **输入**: `signal = np.full(10, np.nan)`, `t = np.arange(10, dtype=float)`
- **预期输出**: 所有函数不抛未捕获异常。NaN 传播是预期行为。部分滤波器（EMA, SMA, WMA）返回全 NaN，Kalman 可能产生有穷值（从 `signal[0]=NaN` 初始化），可接受
- **验证**: try/except 包裹，无崩溃

#### TC-DATA-01.8: Kalman — Constant Signal, dt Extraction Robustness

- **优先级**: P2
- **函数**: `apply_kalman`
- **输入**: `signal = np.full(50, 5.0)`, `t = np.arange(50, dtype=float)`, Q=0.01, R=0.1, dt=1.0
- **预期输出**: Kalman 增益收敛，滤波输出趋近 5.0，全部值有穷
- **验证**: `np.all(np.isfinite(result))`，`abs(result[-1] - 5.0) < 0.1`

#### TC-DATA-01.9: Butterworth — Cutoff at Nyquist

- **优先级**: P2
- **函数**: `apply_butterworth`
- **输入**: `signal = sin(2pi * 0.05 * t)` (100点), `order=4`, `cutoff=50.0`（>= nyquist=0.5）
- **预期输出**: `cutoff = nyquist * 0.99 = 0.495`（自动钳制），输出长度=输入长度，全部有穷值
- **验证**: `len(result) == len(signal)`，`np.all(np.isfinite(result))`

#### TC-DATA-01.10: Median — Window=3 Impulse Removal

- **优先级**: P1
- **函数**: `apply_median`
- **输入**: `signal = [1, 100, 2]`（中心脉冲），`window=3`
- **预期输出**: 中值 `[1,100,2]` = 2，脉冲被完全去除。中心元素 `result[1] == 2.0`
- **验证**: `result[1] == 2.0`

---

### TC-DATA-02: Schmitt 触发器测试 (6个子用例)

#### TC-DATA-02.1: Schmitt Trigger — v>0, a=0 (All Zero in Deadband)

- **优先级**: P0
- **函数**: `_schmitt_trigger`
- **输入**: n=100, `v = np.ones(n) * 0.1`（常正速度）, `a = np.zeros(n)`（零加速度）, ewma_span=20, k_eps=0.15, sigma_min=0.05
- **预期输出**: `sig_t` 全零（|a|=0 < eps_t = k_eps * sigma_min = 0.0075，死区保持），`eps_t >= 0.0075`，数组长度=n
- **验证**: `np.all(schmitt["sig"] == 0)`

#### TC-DATA-02.2: Schmitt Trigger — Acceleration > Threshold, v>0 (Long Trigger)

- **优先级**: P0
- **函数**: `_schmitt_trigger`
- **输入**: n=100, `v = np.ones(n) * 0.1`, `a = np.ones(n) * 0.1`（正加速度超阈值）, 其他参数同上
- **预期输出**: `sig_t[0] = 0`（初始状态），EWMA 稳定后 eps_t → 0.0075，`a > eps_t AND v > 0` → 状态切换到 +1。最终 `sig_t[i] == 1` 在触发后持续
- **验证**: `np.any(schmitt["sig"] == 1)` 且首次触发后持续为 +1

#### TC-DATA-02.3: Schmitt Trigger — Hysteresis Validation

- **优先级**: P0
- **函数**: `_schmitt_trigger`
- **输入**: n=150, v=常数0.1, a分三个相位：(1) a[20:70]=0.1 触发做多，(2) a[70:100]=-0.005（|a| < eps，滞回保持），(3) a[100:130]=-0.02（a < -eps，退出）
- **预期输出**: (1) 相位1触发后 `sig_t=1`，(2) 相位2 a=-0.005 时 `|a|=0.005 < eps=0.0075`，滞回保持 `sig_t=1` 不翻转，(3) 相位3 a=-0.02 < -eps，退出到 `sig_t=0`
- **验证**: `schmitt["sig"][80] == 1`（滞回保持），`schmitt["sig"][120] == 0`（退出）

#### TC-DATA-02.4: Schmitt Trigger — n < ewma_span Returns None

- **优先级**: P1
- **函数**: `_schmitt_trigger`
- **输入**: `v = np.array([0.1, 0.2])`, `a = np.array([0.01, 0.02])`, `ewma_span=10`（n=2 < span=10）
- **预期输出**: 返回 `None`
- **验证**: `result is None`

#### TC-DATA-02.5: Schmitt Trigger — NaN Values in v/a

- **优先级**: P2
- **函数**: `_schmitt_trigger`
- **输入**: n=100, v[50]=NaN, a[50]=NaN
- **预期输出**: 含 NaN 时保持前一状态（`sig_t[i] = current_state`），不翻转。不崩溃
- **验证**: `schmitt["sig"][50] == schmitt["sig"][49]`（状态保持）

#### TC-DATA-02.6: Schmitt Trigger — v=0, a=0 (Stationary)

- **优先级**: P1
- **函数**: `_schmitt_trigger`
- **输入**: `v = np.zeros(100)`, `a = np.zeros(100)`
- **预期输出**: sigma_v[0]=0, EWMA=0, eps_t = k_eps * sigma_min。所有 sig_t=0，静止数据无伪信号
- **验证**: `np.all(schmitt["sig"] == 0)`

---

### TC-DATA-03: 抛物线拟合测试 (5个子用例)

#### TC-DATA-03.1: _fit_parabolic — Known Parabola

- **优先级**: P0
- **函数**: `_fit_parabolic`
- **输入**: `x = [0,1,2,3,4]`, `y = 2*x^2 - 3*x + 5`（已知系数 a=2, b=-3, c=5）, start=0, end=4
- **预期输出**: `np.polyfit(x, y, 2)` → `a ~ 2.0, b ~ -3.0, c ~ 5.0`（浮点容差内），y_fit 精确等于 y
- **验证**: `np.isclose(result["a"], 2.0)` 且 `np.allclose(result["y_fit"], y)`

#### TC-DATA-03.2: _fit_parabolic — Fewer Than 3 Points Returns None

- **优先级**: P1
- **函数**: `_fit_parabolic`
- **输入**: `x = [0, 1]`, `y = [5, 7]`, start=0, end=1（n=2 < 3）
- **预期输出**: `None`
- **验证**: `result is None`

#### TC-DATA-03.3: _fit_physics_parabola — Known Parabola Anchored at Endpoint

- **优先级**: P0
- **函数**: `_fit_physics_parabola`
- **输入**: `x = [0,1,2,3,4]`, `y = 2*(x-4)^2 + 10`（顶点在 x=4, y=10）, start=0, end=4
- **预期输出**: 端点锚定作为顶点 (x0=4, y0=10)，a ~ 1.701, b=0, c=10。y_fit 近似原抛物线（端点锚定约束下无法恢复精确a=2）
- **验证**: `result is not None`, `np.isclose(result["x0"], 4.0)`, `np.isclose(result["c"], 10.0)`

#### TC-DATA-03.4: _fit_physics_parabola — Collinear Data (denom → 0 安全)

- **优先级**: P1
- **函数**: `_fit_physics_parabola`
- **输入**: `x = [0,1,2]`, `y = [5,5,5]`（常值）, start=0, end=2。或 collinear y=mx+b
- **预期输出**: a 接近 0（平坦抛物线），y_fit 近似线性趋势。不返回 None
- **验证**: `result is not None`, `np.isclose(result["a"], 0.0, atol=1e-10)`

#### TC-DATA-03.5: Comparison of physics vs poly2 on Same Data

- **优先级**: P2
- **函数**: `_fit_parabolic` vs `_fit_physics_parabola`
- **输入**: `x = [0,1,2,3,4,5]`, `y = 0.5*(x-2)^2 + 3`（顶点在x=2）, start=0, end=5
- **预期行为**: poly2 (3-dof) 精确拟合 a=0.5, b=-2.0, c=5.0。physics (1-dof) 锚定端点为顶点，产生不同抛物线。两方法外推方向不同，y_fit 不同
- **验证**: 两种方法 `y_fit` 数组不同，外推方向有差异

---

### TC-DATA-04: PnL 回测引擎测试 (5个子用例)

#### TC-DATA-04.1: PnL — Empty pairs Returns Initial Value

- **优先级**: P0
- **函数**: `_compute_strategy_pnl`
- **输入**: t=arange(5), filtered=[100,101,102,101,100], sig_t 全零, all_pairs=[], pred_pairs=[], stop_loss=2.0, n_extend=5
- **预期输出**: `long_pnl = [100,100,100,100,100]`, `short_pnl = [100,100,100,100,100]`, `trade_records = []`
- **验证**: `np.all(long_pnl == 100.0)`, `np.all(short_pnl == 100.0)`, `len(trade_records) == 0`

#### TC-DATA-04.2: PnL — Known Long Trade with Take Profit

- **优先级**: P0
- **函数**: `_compute_strategy_pnl`
- **输入**: t=arange(20), filtered 稳步上升 `100 + i*0.5`, sig_t 含 long 对 (5,15), pred_pairs 含上行预测, stop_loss=10%（宽止损）
- **预期输出**: 1 条交易记录，type="long"，exit_reason="take_profit"，return_pct > 0，long_pnl[15:] > 100，short_pnl 全 100
- **验证**: `len(trade_records) == 1`, `trade_records[0]["type"] == "long"`, `trade_records[0]["exit_reason"] == "take_profit"`, `trade_records[0]["return_pct"] > 0`

#### TC-DATA-04.3: PnL — Stop Loss Trigger in Protection Period

- **优先级**: P1
- **函数**: `_compute_strategy_pnl`
- **输入**: 建仓后价格急跌（entry_price=105 → 下一根 bar=85），stop_loss=5%，保护期 n_extend=3
- **预期输出**: 止损在 index 11 触发（85 < 96*0.95=91.2），exit_reason="stop_loss"，return_pct 为负
- **验证**: `trade_records[0]["exit_reason"] == "stop_loss"`, `trade_records[0]["exit_idx"] == 11`

#### TC-DATA-04.4: PnL — Sequence of Trades (Long then Short)

- **优先级**: P2
- **函数**: `_compute_strategy_pnl`
- **输入**: 两个连续交易对（先 long 后 short），stop_loss=10%
- **预期输出**: 2 条交易记录，short_capital 独立于 long_pnl（各自 100 起始），long_pnl 和 short_pnl 值不同
- **验证**: `len(trade_records) == 2`, `long_pnl != short_pnl`（一般情况下）

#### TC-DATA-04.5: PnL — Extreme Stop Loss (0.5% and 1000%)

- **优先级**: P2
- **函数**: `_compute_strategy_pnl`
- **输入**: `stop_loss_pct=0.5`（极紧止损，大概率快速触发）和 `stop_loss_pct=1000`（几乎不会止损）
- **预期输出**: 极紧止损快速触发（exit_reason="stop_loss"），极大止损不触发（正常退出）。函数不崩溃
- **验证**: 无崩溃，exit_reason 反映实际触发条件

---

### TC-DATA-05: 时间对齐模块测试 (3个子用例)

#### TC-DATA-05.1: Time Alignment — HKT Intraday + Naive Daily

- **优先级**: P0
- **函数**: `_align_pnl_to_current_tf`
- **输入**: 高周期 HKT tz-aware 日内数据（`Asia/Hong_Kong`）+ 低周期 naive 日数据。含一条 long 交易
- **预期输出**: (1) `_normalize_dates` 去时区，(2) 前向填充正确（每根日线 bar 取当日最后高周期 PnL），(3) entry/exit marker 位置正确
- **验证**: `aligned_long[0] == 100.5`, `aligned_long[1] == 102.0`, `np.isnan(aligned_long[2])`, entry_marker bar 索引正确

#### TC-DATA-05.2: Time Alignment — No Temporal Overlap

- **优先级**: P1
- **函数**: `_align_pnl_to_current_tf`
- **输入**: higher_dates = `["2024-06-01", "2024-06-02"]`, current_dates = `["2024-01-01"]`（高周期在未来，无交集）
- **预期输出**: 所有 aligned 值为 NaN，marker 为空
- **验证**: `np.all(np.isnan(result["aligned_long"]))`, `len(result["entry_markers"]) == 0`

#### TC-DATA-05.3: Time Alignment — Higher PnL Shorter than Current

- **优先级**: P2
- **函数**: `_align_pnl_to_current_tf`
- **输入**: higher_dates=2 点，current_dates=10 点。高周期仅覆盖低周期的前 2 天
- **预期输出**: 前 2 个位置取到 PnL 值，后 8 个位置为 NaN。marker 在界内
- **验证**: `np.sum(~np.isnan(aligned_long)) <= len(higher_dates)`

---

### TC-DATA-06: 边界条件测试 (8个子用例)

#### TC-DATA-06.1: _find_all_pairs — Empty Signal

- **优先级**: P1
- **函数**: `_find_all_pairs`
- **输入**: `sig_t = np.array([])`（n=0 < 3）
- **预期输出**: `[]`
- **验证**: `result == []`

#### TC-DATA-06.2: _find_all_pairs — No Non-Zero Entries

- **优先级**: P1
- **函数**: `_find_all_pairs`
- **输入**: `sig_t = np.zeros(10)`（全零信号）
- **预期输出**: `[]`
- **验证**: `result == []`

#### TC-DATA-06.3: _find_all_pairs — Single Segment (Unpaired)

- **优先级**: P1
- **函数**: `_find_all_pairs`
- **输入**: `sig_t = [0,0,1,1,1,0,0]`（仅一段 +1 信号，无配对段）
- **预期输出**: `[]`（segments < 2）
- **验证**: `result == []`

#### TC-DATA-06.4: _find_all_pairs — Long-Short Sparse Pair

- **优先级**: P1
- **函数**: `_find_all_pairs`
- **输入**: `sig_t = [0,1,0,0,-1,0]`（一 long 一 short）
- **预期输出**: `[(1, 4)]`（v1 != v2 配对）
- **验证**: `result == [(1, 4)]`

#### TC-DATA-06.5: _find_all_pairs — Merging Adjacent Same-Sign Segments

- **优先级**: P1
- **函数**: `_find_all_pairs`
- **输入**: `sig_t = [0,1,0,1,0,0,-1]`（两段 +1 中间隔中性零）
- **预期输出**: 合并为 `[(1,3,+1), (6,6,-1)]`，配对 `[(1, 6)]`
- **验证**: `result == [(1, 6)]`

#### TC-DATA-06.6: _fit_parabolic — Collinear Data

- **优先级**: P2
- **函数**: `_fit_parabolic`
- **输入**: `x=[0,1,2,3,4]`, `y=[1,2,3,4,5]`（y=x+1，完全共线）
- **预期输出**: a ~ 0（无二次项），b ~ 1，c ~ 1。y_fit 复现 y
- **验证**: `np.isclose(result["a"], 0.0, atol=1e-10)`, `np.isclose(result["b"], 1.0)`, `np.isclose(result["c"], 1.0)`

#### TC-DATA-06.7: _compute_strategy_pnl — All-NaN Filtered

- **优先级**: P2
- **函数**: `_compute_strategy_pnl`
- **输入**: filtered 全 NaN，sig_t 含 long 对，pred_pairs 含预测
- **预期输出**: entry_price=NaN → `continue`（跳过），无交易。long_pnl = short_pnl = 全 100.0
- **验证**: `np.all(long_pnl == 100.0)`, `len(trade_records) == 0`

#### TC-DATA-06.8: _align_pnl_to_current_tf — Empty higher_dates

- **优先级**: P1
- **函数**: `_align_pnl_to_current_tf`
- **输入**: higher_dates 为空，current_dates 有 2 点
- **预期输出**: aligned 全 NaN，marker 为空（`len(higher_dates) == 0` 提前返回）
- **验证**: `np.all(np.isnan(result["aligned_long"]))`, `len(result["entry_markers"]) == 0`

---

### TC-DATA 汇总表

| TC-ID | 类别 | 函数 | 优先级 | 关键边界 |
|-------|------|------|--------|----------|
| TC-DATA-01.1 | 滤波 | apply_sma | P0 | 常值信号 |
| TC-DATA-01.2 | 滤波 | apply_sma | P1 | window > len |
| TC-DATA-01.3 | 滤波 | apply_ema | P0 | 常值信号 |
| TC-DATA-01.4 | 滤波 | apply_wma | P1 | 噪声正弦平滑 |
| TC-DATA-01.5 | 滤波 | apply_alma | P1 | window=1 |
| TC-DATA-01.6 | 滤波 | All | P1 | 空数组 |
| TC-DATA-01.7 | 滤波 | All | P1 | 全NaN信号 |
| TC-DATA-01.8 | 滤波 | apply_kalman | P2 | 常值收敛 |
| TC-DATA-01.9 | 滤波 | apply_butterworth | P2 | Nyquist边界 |
| TC-DATA-01.10 | 滤波 | apply_median | P1 | 脉冲去除 |
| TC-DATA-02.1 | Schmitt | _schmitt_trigger | P0 | 死区 |
| TC-DATA-02.2 | Schmitt | _schmitt_trigger | P0 | 超阈值触发 |
| TC-DATA-02.3 | Schmitt | _schmitt_trigger | P0 | 滞回验证 |
| TC-DATA-02.4 | Schmitt | _schmitt_trigger | P1 | n < span |
| TC-DATA-02.5 | Schmitt | _schmitt_trigger | P2 | NaN输入 |
| TC-DATA-02.6 | Schmitt | _schmitt_trigger | P1 | 静止信号 |
| TC-DATA-03.1 | 拟合 | _fit_parabolic | P0 | 已知抛物线 |
| TC-DATA-03.2 | 拟合 | _fit_parabolic | P1 | <3点 |
| TC-DATA-03.3 | 拟合 | _fit_physics_parabola | P0 | 端点锚定 |
| TC-DATA-03.4 | 拟合 | _fit_physics_parabola | P1 | 共线数据 |
| TC-DATA-03.5 | 拟合 | 两者对比 | P2 | 方法交叉对比 |
| TC-DATA-04.1 | PnL | _compute_strategy_pnl | P0 | 空交易对 |
| TC-DATA-04.2 | PnL | _compute_strategy_pnl | P0 | 止盈退出 |
| TC-DATA-04.3 | PnL | _compute_strategy_pnl | P1 | 保护期止损 |
| TC-DATA-04.4 | PnL | _compute_strategy_pnl | P2 | 连续交易独立资金 |
| TC-DATA-04.5 | PnL | _compute_strategy_pnl | P2 | 极端止损值 |
| TC-DATA-05.1 | 对齐 | _align_pnl_to_current_tf | P0 | HKT→naive 对齐 |
| TC-DATA-05.2 | 对齐 | _align_pnl_to_current_tf | P1 | 无时间重叠 |
| TC-DATA-05.3 | 对齐 | _align_pnl_to_current_tf | P2 | 高周期较短 |
| TC-DATA-06.1 | 边界 | _find_all_pairs | P1 | 空信号 |
| TC-DATA-06.2 | 边界 | _find_all_pairs | P1 | 全零 |
| TC-DATA-06.3 | 边界 | _find_all_pairs | P1 | 单段信号 |
| TC-DATA-06.4 | 边界 | _find_all_pairs | P1 | 多空稀疏对 |
| TC-DATA-06.5 | 边界 | _find_all_pairs | P1 | 同号段合并 |
| TC-DATA-06.6 | 边界 | _fit_parabolic | P2 | 共线数据 |
| TC-DATA-06.7 | 边界 | _compute_strategy_pnl | P2 | 全NaN filtered |
| TC-DATA-06.8 | 边界 | _align_pnl_to_current_tf | P1 | 空 higher_dates |

---

## 4. 跨周期测试 (7个TC)

跨周期 PnL 参考子图功能验证，覆盖周期映射、数据对齐、渲染顺序、缓存、多视图和边界条件。

### CROSS-01: 周期映射

- **优先级**: P0
- **关联Bug**: BUG-002
- **前置条件**: 应用已启动，已加载 AAPL 股票，8 个周期的 TF_HIERARCHY 映射已定义

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 检查 `TF_HIERARCHY` 映射表：1分钟→5分钟→15分钟→60分钟→日线→周线→月线→季线 | 8个周期链式映射正确，每个 tf 的上一级周期为表中左侧相邻周期 |
| 2 | 检查季线的 `TF_HIERARCHY["季线"]` | 值为 `None`（季线为最高周期，无上一级） |
| 3 | 视图1 tf="季线"，开启 show_strategy + show_cross_pnl | 季度子图**不显示跨周期 PnL 参考**（因高周期=None），图表行数为6 |
| 4 | 视图1 tf="月线"，开启 show_strategy + show_cross_pnl | 月线图表显示"季线 PnL 参考"子图（TF_HIERARCHY["月线"]="季线"），图表行数为7 |
| 5 | 遍历所有 7 个有高周期的 tf，逐一验证跨周期子图显示正确的高周期名称和 PnL 数据 | 每个 tf 显示的高周期名称与 TF_HIERARCHY 一致 |

- **验证方式**: 人工观察子图标题和行数

---

### CROSS-02: PnL 数据对齐

- **优先级**: P0
- **关联Bug**: BUG-001, BUG-002
- **前置条件**: 视图4=日线（启用策略+跨周期），视图1=5分钟（启用跨周期），二者存在时间重叠

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 确保日线数据已获取（tz-naive），5分钟数据已获取（HKT tz-aware） | 两个周期的日期时间戳可用 |
| 2 | 在低周期（5分钟）视图中启用 show_cross_pnl=true | 跨周期对齐函数被调用 |
| 3 | 检查 `_normalize_dates` 是否正确去时区：HKT 时间戳 `2024-01-02 09:30:00+08:00` → `2024-01-02 09:30:00`（naive） | 去时区后无 TZ 信息，datetime 比较正常 |
| 4 | 检查前向填充逻辑：对于每根 5 分钟 bar，取 `<= bar时间` 的最新日线 PnL 值 | 日线 PnL 按日期向前填充到 5 分钟 bar，在当天日内保持常值 |
| 5 | 检查交易 marker 位置：entry/exit 标记落在正确的 5 分钟 bar 上 | marker 位置与高周期交易记录对齐，无错位 |

- **验证方式**: 人工观察跨周期子图 PnL 阶梯形状与 marker 位置，对照 TC-DATA-05.1 数据验证

---

### CROSS-03: 渲染顺序

- **优先级**: P1
- **关联Bug**: BUG-007
- **前置条件**: 4 视图均启用策略和跨周期 PnL

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 确认渲染顺序逻辑：视图按 tf 降序排列（季线→月线→...→1分钟），高周期先渲染 | 高周期视图在其 PnL 数据写入 `session_state["_pnl_{tf}"]` 后才轮到低周期视图 |
| 2 | 检查低周期视图（如 5 分钟）渲染时能读取到高周期（日线）的 `_pnl_{tf}` 缓存 | 跨周期子图正常显示日线 PnL 参考，无空白 |
| 3 | 人为调换渲染顺序（修改视图 tf 分配使低周期先于高周期） | 低周期视图首次渲染时 `_pnl_{tf}` 缓存不存在 → 跨周期子图降级隐藏或显示空数据，但系统不崩溃 |
| 4 | 触发 st.rerun 后再次检查 | 第二次渲染时缓存已就绪，跨周期子图正常显示 |

- **验证方式**: 人工观察渲染顺序日志（控制台 print）与跨周期子图显示状态

---

### CROSS-04: 高周期切换

- **优先级**: P1
- **关联Bug**: BUG-003, BUG-007
- **前置条件**: 4 视图均已配置策略和跨周期

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 视图4 tf="日线"，视图1 tf="5分钟"且 show_cross_pnl=true | 5分钟跨周期子图标题显示"日线 PnL 参考" |
| 2 | 将视图4 tf 从"日线"切换为"周线" | (1) 日线 PnL 缓存被清除，(2) 5分钟视图的跨周期子图自动更新为"周线 PnL 参考"（因周线先渲染并缓存） |
| 3 | 将视图1 show_strategy=false | show_cross_pnl 变为 disabled=true，跨周期子图隐藏，但 `_pnl_{tf}` 缓存数据不清除（保留在 session_state 中） |
| 4 | 恢复 show_strategy=true, show_cross_pnl=true | 跨周期子图恢复，显示当前高周期的 PnL 数据 |

- **验证方式**: 人工观察子图标题切换 + 控制台无报错

---

### CROSS-05: session_state 缓存

- **优先级**: P1
- **关联Bug**: BUG-003, BUG-006
- **前置条件**: 至少有一个高周期视图已渲染并产生 PnL 数据

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 检查 `session_state["_pnl_{tf}"]` 结构完整性（以 `_pnl_日线` 为例） | 包含以下字段：`aligned_long`（np.array）、`aligned_short`（np.array）、`entry_markers`（list）、`exit_markers`（list）、`higher_tf`（str）、`higher_trades`（list） |
| 2 | 折叠所有视图 | `_pnl_{tf}` 缓存不被清除（与折叠/展开无关，仅与 st.rerun 生命周期绑定） |
| 3 | 展开所有视图 | 低周期视图仍能正常读取缓存的跨周期 PnL，子图正常显示 |
| 4 | 切换股票代码（AAPL → MSFT） | 所有 `_pnl_{tf}` 缓存被清除（因数据源变更），新股票的 PnL 缓存重新生成 |

- **验证方式**: 人工观察 + 在代码中添加 `st.write(st.session_state)` 查看缓存内容

---

### CROSS-06: 多视图共存

- **优先级**: P2
- **前置条件**: 4 视图分别配置不同周期，各自独立启用/禁用跨周期 PnL

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 配置：视图1=5分钟（show_cross_pnl=true），视图2=15分钟（show_cross_pnl=false），视图3=60分钟（show_cross_pnl=true），视图4=日线（show_cross_pnl=true） | 各视图跨周期子图独立显示/隐藏，互不干扰 |
| 2 | 检查视图1（5分钟）跨周期子图 | 显示高周期（15分钟）的 PnL 参考 |
| 3 | 检查视图2（15分钟） | 无跨周期子图（show_cross_pnl=false） |
| 4 | 检查视图3（60分钟）跨周期子图 | 显示高周期（日线）的 PnL 参考 |
| 5 | 检查视图4（日线）跨周期子图 | 显示高周期（周线）的 PnL 参考 |
| 6 | 验证各视图 PnL 数据不交叉污染 | 视图1 的跨周期 PnL ≠ 视图3 的跨周期 PnL（来源 tf 不同） |
| 7 | 同时折叠/展开多个视图 | 无渲染错误，各视图缓存独立 |

- **验证方式**: 人工观察 4 个跨周期子图的独立性和正确性

---

### CROSS-07: 边界条件

- **优先级**: P1
- **关联Bug**: BUG-001, BUG-004
- **前置条件**: 应用已启动

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 使用无交易记录的股票（如极短期数据，Schmitt 未触发任何信号） | show_cross_pnl=true 但 `_find_all_pairs` 返回 [] → 跨周期子图显示"无交易记录"或空子图，不崩溃 |
| 2 | 设置两个周期数据无时间重叠：高周期数据日期范围 [2020-01-01, 2020-06-30]，当前周期 [2024-01-01, 2024-06-30] | `_align_pnl_to_current_tf` 返回全 NaN → 跨周期子图空或无数据提示，不崩溃 |
| 3 | 仅启用 show_cross_pnl 但高周期的策略被禁用（show_strategy=false） | 高周期无策略交易 → `_pnl_{tf}` 的 aligned 数据为全 100.0（初始值）→ 跨周期子图显示无变化的直线，不崩溃 |
| 4 | 仅显示参考线（高周期 PnL 值），但无 marker（entry/exit 交易点均在高周期不存在） | 跨周期子图显示纯 PnL 曲线（常值 100 的水平线），无 marker 标注，不崩溃 |

- **验证方式**: 人工观察边界场景下无崩溃、无异常堆栈

---

## 5. 集成测试 (端到端场景)

### INT-01: 完整导入→配置→折叠→展开→验证流程

- **优先级**: P0
- **前置条件**: 应用已加载 AAPL 股票，已准备一份含跨周期参数的配置文件

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 上传配置文件（含 custom n_pts, k_ε, 颜色, show_cross_pnl 等字段） | 侧边栏提示"配置已加载"，所有参数恢复 |
| 2 | 逐视图检查参数：n_pts, k_ε, σ_min, N_EWMA, 滤波窗口/阶数, 颜色, show_sch, show_pred, show_strategy, stop_loss, show_cross_pnl | 全部与配置文件中值一致 |
| 3 | 检查各视图图表 | 图表渲染正常，子图行数正确 |
| 4 | 折叠所有视图 | 4 视图折叠 |
| 5 | 切换滤波器类型（Savgol → Kalman） | 参数面板刷新 |
| 6 | 展开所有视图 | 导入的参数值全部保留（除滤波器相关参数被全局覆盖外） |
| 7 | 切换股票代码到 MSFT | 数据重新加载 |
| 8 | 重新导入原始 AAPL 配置文件 | 参数恢复，图表正常 |
| 9 | 确认无崩溃 | 全流程无异常 |

---

### INT-02: 4 视图同时运行的稳定性

- **优先级**: P1
- **前置条件**: 应用已加载 AAPL，4 视图分别设置为 5分钟/15分钟/60分钟/日线

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 全部视图启用 Sch + Pred + Strategy + CrossPnL | 4 个 7 行图表正常渲染 |
| 2 | 设置不同参数：各视图 n_pts 分别为 50/100/150/200，Savgol 窗口分别为 11/21/31/41 | 各图表独立渲染 |
| 3 | 启用双滤波对比（global_dual=true） | 8 条滤波曲线同时渲染，无性能问题 |
| 4 | 快速连续切换 5 组不同股票代码（AAPL/MSFT/GOOGL/TSLA/600115.SS） | 每次切换正常加载，无崩溃，无 session_state 残留 |
| 5 | 滚动页面上下多次 | 图表无闪烁，参数面板无错位 |
| 6 | 连续操作 5 分钟以上（压力测试） | 内存无泄漏（Streamlit 进程内存稳定），无崩溃 |

---

### INT-03: 跨周期 + 时间窗口 + 周期切换联合

- **优先级**: P2
- **前置条件**: 4 视图均配置不同周期和跨周期 PnL

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 视图1 tf="60分钟", show_cross_pnl=true; 视图4 tf="日线"（高周期） | 60分钟跨周期子图显示"日线 PnL 参考" |
| 2 | 前移 time_offset 到 30 天前 | 两视图同步前移，跨周期子图的 PnL 数据窗口与前移一致 |
| 3 | 将视图1 tf 从"60分钟"切换为"15分钟" | 跨周期子图的高周期从"日线"变为"60分钟"（TF_HIERARCHY["15分钟"]="60分钟"） |
| 4 | 将视图4 tf 从"日线"切换为"周线" | 视图1 跨周期子图不受影响（其高周期=60分钟，与视图4 无关） |
| 5 | 点击"最新"按钮 | 所有视图归零，跨周期子图数据重新对齐到最新 |

---

## 附录

### A. 测试环境准备

| 项目 | 要求 |
|------|------|
| 浏览器 | Chrome 最新版 / Edge 最新版 |
| Python | 3.10+，已安装 `streamlit`, `numpy`, `pandas`, `scipy`, `plotly`, `yfinance` |
| 股票代码 | AAPL（美股，推荐，数据最全）、600115.SS（A股）、3690.HK（港股） |
| 网络 | 需可访问 Yahoo Finance API |
| 配置文件 | 准备一份包含所有非默认参数的 JSON 配置文件（含 `_cross_pnl` 字段），用于 TC-UI-03 和 INT-01 |
| 数据 | 确保各周期数据已成功获取（启动后侧边栏显示 "8/8 成功"） |

### B. Bug 回归检查清单

执行完所有测试用例后，逐项核查：

| Bug ID | 描述 | 关键 TC | 通过标准 | 结果 |
|--------|------|---------|----------|------|
| BUG-001 | 页面崩溃/无响应 | TC-UI-04a~e, TC-UI-06f, TC-DATA-01.6, TC-DATA-01.7, CROSS-07 | 无崩溃，无白屏 | ☐ |
| BUG-002 | 时间窗口计算错误 | TC-UI-04d, TC-UI-06f, TC-DATA-05.1~3, CROSS-01, CROSS-02 | 所有周期时间窗口正确，时区对齐 | ☐ |
| BUG-003 | Checkbox 级联状态异常 | TC-UI-02a~e, CROSS-04, CROSS-05 | 16种组合全过，disabled 状态正确 | ☐ |
| BUG-004 | 配置导入导出失败 | TC-UI-03a~e, CROSS-07 | 往返测试通过，边界处理正确 | ☐ |
| BUG-005 | 折叠后颜色/参数丢失 | TC-UI-01d, TC-UI-03d | 颜色和参数值不变 | ☐ |
| BUG-006 | 折叠/展开滤波参数漂移 | TC-UI-01e, CROSS-05 | slider 极值不漂移 | ☐ |
| BUG-007 | cross_pnl 依赖链断裂 | TC-UI-02d, TC-UI-03b, CROSS-03, CROSS-04 | 依赖链 + 导入正确 | ☐ |
| BUG-008 | 极端参数显示异常 | TC-UI-05a~i | 极值无显示异常 | ☐ |
| BUG-009 | 参数滑块越界 | TC-UI-05a~h | slider 范围内工作正常 | ☐ |

### C. 测试执行顺序建议

按依赖关系排序执行，前置 TC 通过后再执行后续 TC：

**阶段 1 — 数据计算验证（先确保计算层正确）**
1. TC-DATA-01（滤波算法）→ 验证核心计算
2. TC-DATA-02（Schmitt 触发器）→ 验证信号生成
3. TC-DATA-03（抛物线拟合）→ 验证预测模型
4. TC-DATA-04（PnL 引擎）→ 验证回测逻辑
5. TC-DATA-05（时间对齐）→ 验证跨周期数据对齐
6. TC-DATA-06（边界条件）→ 验证鲁棒性

**阶段 2 — UI 交互验证（计算层正确后再验交互）**
7. TC-UI-01（折叠稳定性）→ 最基础的渲染问题
8. TC-UI-02（Checkbox 依赖链）→ 确认参数依赖正确
9. TC-UI-06（周期切换）→ 确保数据层正常
10. TC-UI-04（时间窗口导航）→ 验证数据窗口操作
11. TC-UI-05（参数滑块边界）→ 排除极端值崩溃
12. TC-UI-03（导入导出往返）→ 覆盖全流程

**阶段 3 — 跨周期集成验证**
13. CROSS-01（周期映射）→ 确认映射链
14. CROSS-02（PnL 数据对齐）→ 确认对齐逻辑
15. CROSS-03（渲染顺序）→ 确认缓存依赖
16. CROSS-04（高周期切换）→ 确认动态切换
17. CROSS-05（session_state 缓存）→ 确认缓存完整性
18. CROSS-06（多视图共存）→ 确认隔离性
19. CROSS-07（边界条件）→ 确认鲁棒性

**阶段 4 — 端到端集成**
20. INT-01（完整导入→配置→折叠→展开→验证流程）
21. INT-02（4视图同时运行稳定性）
22. INT-03（跨周期+时间窗口+周期切换联合）

### D. 已知风险

- **st.rerun() 限流**: 快速连击折叠按钮可能触发 Streamlit 的 rerun 限流，操作时应间隔 1-2 秒
- **Yahoo Finance API 速率限制**: n_pts 切换后需等待数据重新获取，快速切换可能触发 TTL 缓存
- **跨周期 PnL 依赖渲染顺序**: 必须先确保高周期已渲染并缓存，低周期才能显示跨周期参考。在阶段 3 测试时注意此依赖
- **session_state 生命周期**: `_pnl_{tf}` 缓存仅在同一 Streamlit session 内有效。刷新页面（F5）后缓存清空
- **时区处理**: 美股数据中日内周期含 HKT 时区，日线及以上周期为 tz-naive。对齐时依赖 `_normalize_dates` 去时区操作
