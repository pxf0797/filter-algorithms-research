# Filter Comparison Web Tool

交互式滤波指标对比可视化工具。基于 Plotly.js，在浏览器中对比多个股票代码的滤波信号（施密特触发器、预测曲线等），支持交互式缩放、信号高亮和视图切换。

## 使用方式

```bash
# 1. 生成数据（如需要更新示例数据）
python generate_data.py

# 2. 启动本地 HTTP 服务
python3 -m http.server

# 3. 浏览器打开
open http://localhost:8000/index.html
```

**依赖**：需要互联网连接（从 CDN 加载 Plotly.js）。
