# 已知问题

## 测试覆盖限制

### filter_app/streamlit_app.py (0% 单元覆盖)
- **原因**: ~1223行 UI 入口代码（含提取的辅助函数, main()本身~350行），依赖 Streamlit 运行时
- **现状**: 核心逻辑已拆分至 components/ + services/，各有较高覆盖
- **AppTest**: 交互测试 (29 tests, 含侧边栏/预设/控件/P0回归) 已就绪

### sidebar.py (7% 单元覆盖)
- **原因**: 97% 代码为 Streamlit widget 调用
- **现状**: 常量/参数逻辑已单元测试 (19 tests)
- **计划**: AppTest 交互测试

### charts.py _render_plotly (未覆盖)
- **原因**: 依赖 Streamlit 运行时 + Plotly HTML 生成
- **计划**: AppTest 输出验证

## 预存 Bug

### pandas Series truthiness (已修复)
- **位置**: filter_app/streamlit_app.py _load_chart_data, _render_chart
- **问题**: `if err:` 对 pandas Series 抛 ValueError
- **修复**: 改为 `if err is not None:`

## 工程化待办

- [x] AppTest 交互测试扩展 (预设选择、ticker输入)
- [ ] pre-commit hooks 配置
- [ ] CI 覆盖率门禁 (已设置 --cov-fail-under=45)
