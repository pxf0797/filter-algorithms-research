# web_tool

独立的 Web 股票筛选工具（纯前端 JavaScript + HTML），与 `filter_app/` **无代码引用关系**。

## 与 filter_app 的关系

- **filter_app/** — Python Streamlit 应用，多周期滤波分析 + 回测，功能更完整
- **web_tool/** — 前端原型的筛选器验证工具，功能与 `filter_app` 的部分筛选逻辑重叠

两者功能有重叠（均涉及股票筛选），但代码独立、无相互引用。`web_tool/` 可视为早期原型或轻量替代。

## 文件说明

- `index.html` — 主页面
- `sample_data.json` — 示例数据
- `verify_filters.js` — 筛选器验证逻辑
- `generate_data.py` — 数据生成脚本
