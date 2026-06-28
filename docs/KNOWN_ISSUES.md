# 已知问题

## 测试覆盖限制

### streamlit_app.py (0% 单元覆盖)
- **原因**: 869行 UI 代码，依赖 Streamlit 运行时
- **现状**: 通过 AppTest 覆盖冒烟测试 (6 tests)
- **计划**: 逐步增加 AppTest 交互测试

### sidebar.py (7% 单元覆盖)
- **原因**: 97% 代码为 Streamlit widget 调用
- **现状**: 常量/参数逻辑已单元测试 (19 tests)
- **计划**: AppTest 交互测试

### charts.py _render_plotly (未覆盖)
- **原因**: 依赖 Streamlit 运行时 + Plotly HTML 生成
- **计划**: AppTest 输出验证

## 预存 Bug

### pandas Series truthiness (已修复)
- **位置**: streamlit_app.py _load_chart_data, _render_chart
- **问题**: `if err:` 对 pandas Series 抛 ValueError
- **修复**: 改为 `if err is not None:`

## 工程化待办

- [ ] AppTest 交互测试扩展 (预设选择、ticker输入)
- [ ] pre-commit hooks 配置
- [ ] CI 覆盖率门禁 (已设置 --cov-fail-under=45)
