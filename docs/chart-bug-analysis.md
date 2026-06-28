# Chart Bug 定位分析报告

## 1. 问题范围

- **问题**: 图表渲染后显示空白，无 plotly 图表也 fallback 提示
- **影响文件**: `filter_app/components/charts.py`
- **影响函数**: `_render_plotly()` (第 17-201 行)
- **定位结果**: 问题精确隔离到 **H4 (commit `3d16b16`)**，H5 (commit `118e775`) 在 H4 bug 修复后本身逻辑无问题

## 2. 合并过程回溯

| 步骤 | Commit | 文件 | 内容 | 验证 |
|------|--------|------|------|------|
| 1 | `242d46e` | requirements.txt | pip-audit 依赖 | 导入通过 |
| 2 | `83b54b8` | sidebar.py | caption + sf2 None 修复 | 导入通过 |
| 3 | `6ed8b2c` | streamlit_app.py | 33个类型标注 | 导入通过 |
| 4a | `23d3123` | charts.py | H1-H3,H6-H10 类型标注+CDN标记 | 导入通过 |
| 4b | `3d16b16` | charts.py | **H4 Plotly fallback UI + JS检测** | 导入通过 |
| 4c | `118e775` | charts.py | H5 5秒setTimeout超时检查 | 导入通过 |

导入验证在 Python 层面全部通过，但 JavaScript 语法错误不会在 `import` 阶段被检测。

---

## 3. H4 详细分析 (Plotly fallback UI)

### 3.1 完整代码 Diff

`git diff 23d3123..3d16b16 -- filter_app/components/charts.py` 的关键变更:

```diff
 <div id="{div_id}"></div>
 <div id="date-tip-{div_id}"></div>
+<div id="plotly-fallback-{div_id}" style="display:none;padding:2rem;text-align:center;color:#888">
+  <p>Plotly.js 加载失败</p>
+  <p>请检查网络连接或联系管理员</p>
+</div>
 <script>
-- removed -->  (function() {{                         <!-- 被删除! -->
-              var figure = {figure_json};
+++ added +++  var _fallbackEl = document.getElementById('plotly-fallback-{div_id}');
+++ added +++  if (typeof Plotly === 'undefined') {{
+++ added +++      _fallbackEl.style.display = 'block';
+++ added +++      document.getElementById('{div_id}').style.display = 'none';
+++ added +++      return;
+++ added +++  }} else if (window._plotlyCdnFailed) {{
+++ added +++      // 从CDNJS fallback成功加载，清除标记
+++ added +++      delete window._plotlyCdnFailed;
+++ added +++  }}
+++ added +++  var figure = {figure_json};
     var config = {{
         responsive: true,
         ...
     }});
 }});
+    setTimeout(function() {{      <!-- H5 后续加入 -->
+        ...
+    }}, 5000);
 }})();
 </script>
```

### 3.2 代码结构对比（Python 字符串层面）

**合并前 (23d3123)** — 正确的自调用函数包裹:
```
<script>
(function() {{          ← Python 字符串中的 {{ 渲染为 JS 的 {
    var figure = {figure_json};
    ...
}})();                  ← }} 渲染为 }，然后 () 调用函数
</script>
```

**合并后 (3d16b16/118e775)** — 当前代码:
```
<script>
var _fallbackEl = document.getElementById('plotly-fallback-{div_id}');
if (typeof Plotly === 'undefined') {{
    _fallbackEl.style.display = 'block';
    document.getElementById('{div_id}').style.display = 'none';
    return;                               ← BUG #1: return 在全局作用域
}} else if (window._plotlyCdnFailed) {{
    delete window._plotlyCdnFailed;
}}
var figure = {figure_json};
    ...
}})();                                    ← BUG #2: 孤立的 })() 无对应开头
</script>
```

### 3.3 逐行问题分析

**问题 A: `(function() {` 被删除 (第 97 行位置)**

H4 将以下行整体删除:
```javascript
(function() {
```

删除这行的同时保留了末尾的 `})();` (第 196 行)，导致整个 JS 代码失去自调用函数包裹。

**问题 B: `return;` 在全局作用域 (第 101 行)**

`.format()` 处理后该行渲染为:
```javascript
if (typeof Plotly === 'undefined') {
    _fallbackEl.style.display = 'block';
    document.getElementById('plot-abc12345').style.display = 'none';
    return;   // ← ECMAScript 规范: return 只能在函数体内使用
}
```

`return` 语句只能在函数体内部使用。出现在全局 `<script>` 作用域是 **JavaScript 早期语法错误 (Early Error)**，会在解析阶段被拒绝，整个脚本都不会执行。

**问题 C: 孤立的 `})();` (第 196 行)**

`.format()` 处理后渲染为:
```javascript
})();
```

这行原本是自调用函数 `(function() { ... })();` 的闭合部分。H4 删除了开头 `(function() {`，但未删除此闭合，导致:
- 孤立的 `}` 关闭了一个不存在的代码块
- `()` 调用了一个不存在的函数表达式

这在解析阶段也是语法错误。

### 3.4 Python `.format()` 与 JS 花括号不冲突

确认: 代码使用 `.format(div_id=div_id, figure_json=figure_json)` 而非 f-string。

- `{div_id}` → 替换为 Python 变量值 (如 `plot-a1b2c3d4`)
- `{figure_json}` → 替换为 JSON 字符串
- `{{` 和 `}}` → 分别渲染为单个 `{` 和 `}`(JS 花括号)

**花括号转义本身没有错误**。所有 JS 代码的 `{{` / `}}` 转义在此文件中是统一的，问题仅在于 H4 删除了 `(function() {{` 这行。

### 3.5 影响链

```
H4 删除 (function() {
  ↓
JS 解析阶段遇到两处语法错误:
  1. return; at global scope (line 101 等效位置)
  2. })(); orphaned closer (line 196 等效位置)
  ↓
整个 <script> 块解析失败
  ↓
Plotly.newPlot() 从未被调用 → 图表不渲染
fallback 的 style.display='block' 也从未执行 → fallback UI 不显示
  ↓
用户看到: 一个尺寸为 height px 的空白 iframe
```

---

## 4. H5 详细分析 (5 秒超时)

### 4.1 完整代码 Diff

`git diff 3d16b16..118e775 -- filter_app/components/charts.py`:

```diff
     }});
+    // Safety check: if Plotly still not loaded after 5s, show fallback
+    setTimeout(function() {{
+        if (typeof Plotly === 'undefined') {{
+            _fallbackEl.style.display = 'block';
+            document.getElementById('{div_id}').style.display = 'none';
+        }}
+    }}, 5000);
 }})();
```

### 4.2 H5 代码本身无语法错误

`.format()` 处理后:
```javascript
    // Safety check: if Plotly still not loaded after 5s, show fallback
    setTimeout(function() {
        if (typeof Plotly === 'undefined') {
            _fallbackEl.style.display = 'block';
            document.getElementById('plot-abc12345').style.display = 'none';
        }
    }, 5000);
```

- `{{` → `{` 转义正确
- `{div_id}` 替换正确
- 花括号配对正确 (function, if 各一对)
- `setTimeout` 闭包捕获了 `_fallbackEl` 变量
- `5000` 毫秒 = 5 秒，时序合理

### 4.3 H5 的位置问题

H5 代码插入位置在 `Plotly.newPlot().then()` 回调的闭合 `}});` 之后、自调用函数的闭合 `}})();` 之前。

如果自调用函数 `(function() {` 存在，H5 在这里是正确的——它在函数内部的最后位置，5 秒后执行兜底检查。

但因为 H4 删除了自调用函数开头，H5 同样被暴露在全局作用域中。H5 的 `setTimeout` 本身在全局作用域执行是合法的，但因为 H4 bug 导致整个脚本解析失败，H5 的代码也永远不会执行。

### 4.4 H5 的潜在逻辑竞态（修复后需注意）

即使 H4 修复后，H5 存在一个细微的时序问题:

**场景**: Plotly CDN 加载很慢（>5 秒），但最终加载成功了。

1. 页面加载，Plotly CDN 开始下载
2. `if (typeof Plotly === 'undefined')` → true → 显示 fallback，`return;` 退出
3. **但此时**，CDNJS 的 `onerror` fallback 机制还没触发(因为主 CDN 还在加载中,没 error)
4. 脚本因 `return;` 退出了,后续 `var figure`, `Plotly.newPlot()` 等代码未执行
5. 5 秒后 setTimeout 触发，`typeof Plotly === 'undefined'` 仍为 true → 再次设置 fallback (已是 fallback 状态)
6. 即使 6 秒后 Plotly 加载完成，由于 `newPlot()` 从未被调用，图表仍为空白

**这不是当前 bug，但如果 H4 修复时需要重新设计 fallback 的早期检查逻辑**（使用 `return`），则这个时序竞态需要注意。建议不要在脚本顶层用 `return` 退出，而是把 fallback 检查放在 `Plotly.newPlot()` 调用前的一个条件分支中。

---

## 5. 根因判断

### 根本原因

**H4 (`3d16b16`) 在添加 fallback 检查代码时，错误地删除了 `(function() {` 这一行，破坏了 JavaScript 的自调用函数结构。**

```python
# 被删除的行（Python 源码）
{"(function() {{"}   # 删除后，以下两处语法错误导致整个 JS 脚本解析失败
```

### 为什么图表不能正常显示

1. **JS 脚本解析失败** → 浏览器在解析 `<script>` 块时遇到 `return;` 不在函数内，抛出 `SyntaxError`
2. **所有 JS 代码不执行** → `Plotly.newPlot()` 从未被调用
3. **Fallback UI 也不显示** → `_fallbackEl.style.display = 'block'` 从未执行
4. **结果**: 用户看到一个空白区域——既无图表，也 fallback 提示

### 为什么 H5 不是根因

H5 代码本身语法正确。H5 的 setTimeout 逻辑如果在 H4 修复后部署，会作为一个有效的兜底检查。但因为 H4 的 bug 先导致整个脚本崩溃，H5 的代码也无法生效。

---

## 6. 修复建议

### 6.1 修复方案

恢复 `(function() {` ，将 fallback 检查代码置于函数体内:

```python
# 当前代码 (第 97-106 行附近):
# <script>
# var _fallbackEl = ...
# if (typeof Plotly === 'undefined') {{
#     ...
#     return;
# }} ...

# 修复为:
# <script>
# (function() {{
# var _fallbackEl = document.getElementById('plotly-fallback-{div_id}');
# if (typeof Plotly === 'undefined') {{
#     _fallbackEl.style.display = 'block';
#     document.getElementById('{div_id}').style.display = 'none';
#     return;
# }} else if (window._plotlyCdnFailed) {{
#     delete window._plotlyCdnFailed;
# }}
# var figure = {figure_json};
```

**具体修改**: 在第 97 行 `<script>` 之后、第 98 行 `var _fallbackEl` 之前，插入:
```
(function() {{
```

其余代码不变。

### 6.2 修复后的完整 JS 结构

```javascript
<script>                           // 第 97 行
(function() {                      // ← 补回这行（Python 中为 (function() {{）
var _fallbackEl = ...;             // 第 98 行
if (typeof Plotly === 'undefined') { // 第 99 行
    _fallbackEl.style.display = 'block';
    document.getElementById('...').style.display = 'none';
    return;                        // ← 现在在函数体内，合法
} else if (window._plotlyCdnFailed) {
    delete window._plotlyCdnFailed;
}
var figure = {...};
var config = {...};
Plotly.newPlot('...', ...).then(function(gd) {
    // ... 鼠标悬浮逻辑 ...
});
// Safety check: if Plotly still not loaded after 5s, show fallback
setTimeout(function() {
    if (typeof Plotly === 'undefined') {
        _fallbackEl.style.display = 'block';
        document.getElementById('...').style.display = 'none';
    }
}, 5000);
})();                             // ← 现在有对应的开头 (function() {
</script>
```

### 6.3 具体编辑操作

文件: `/Users/xfpan/claude/filter_research/filter_app/components/charts.py`

在第 97 行 `<script>` 之后添加一行 `(function() {{`:

定位:
```python
<script>
var _fallbackEl = document.getElementById('plotly-fallback-{div_id}');
```

改为:
```python
<script>
(function() {{
var _fallbackEl = document.getElementById('plotly-fallback-{div_id}');
```

### 6.4 验证方法

1. **静态检查**: 确认 `(function() {{` 和 `}})();` 配对，且之间无裸 `return;` 在全局作用域
2. **Python 导入测试**: `python -c "from filter_app.components.charts import _render_plotly"` — 应通过
3. **渲染验证**: 启动 Streamlit 应用，查看图表是否正常显示
4. **JS 语法检查**: 将 `.format()` 处理后的 HTML 中 `<script>` 内容复制到浏览器 Console 或 Node.js 执行，确认无语法错误

### 6.5 补充优化（可选）

当前 fallback 检查使用 `return;` 退出整个自调用函数。如果修复后这个 `return;` 导致后续的 `Plotly.newPlot()` 不执行（即主 CDN 加载慢于脚本执行），可以考虑:

```javascript
(function() {
var _fallbackEl = ...;
var plotlyReady = typeof Plotly !== 'undefined';

if (!plotlyReady && !window._plotlyCdnFailed) {
    // Plotly 尚未加载，但 CDNJS fallback 也未失败 → 等待 onerror 触发
    // 使用 setTimeout 轮询，而非立即 return
} else if (!plotlyReady && window._plotlyCdnFailed) {
    // 两个 CDN 都失败了
    _fallbackEl.style.display = 'block';
    document.getElementById('...').style.display = 'none';
    return;
}

var figure = {...};
// ...
})();
```

但这属于功能增强，非 bug 修复范畴。当前优先修复结构性语法错误。

---

## 7. 附: 完整 Commit 链

| Commit | 描述 | 类型 |
|--------|------|------|
| `53bb6dd` | feat: P1-P5 剩余差距修复 | 基础 |
| `d0f659b` | chore: 合并非功能性变更 | 基础 |
| `242d46e` | chore: add pip-audit dependency | Step 1 |
| `83b54b8` | fix: sidebar caption refactor + sf2 None crash fix | Step 2 |
| `6ed8b2c` | refactor: add return type annotations to all functions | Step 3 |
| `23d3123` | refactor: charts type annotations + CDN标记 (排除fallback UI/超时) | Step 4a |
| `3d16b16` | **feat: Plotly.js 加载失败 fallback UI + JS检测 (H4)** | **Step 4b (BUG引入)** |
| `118e775` | feat: Plotly 5秒超时安全检查 (H5) | Step 4c |

## 8. 结论

**一句话总结**: H4 在添加 Plotly fallback 检测代码时，误删了 JS 自调用函数的开头 `(function() {`，导致 `return;` 出现在全局作用域和 `})();` 成为孤立语句，两处 JavaScript 语法错误使整个脚本解析失败，图表和 fallback UI 均无法渲染。

**修复难度**: 极低 — 一行代码（在 `<script>` 后补回 `(function() {{`）

**修复后风险**: H5 的 setTimeout 逻辑在修复后正常工作，无需额外修改。
