# 抖音视频工作台前端

本阶段仅提供 Vue 3 基础设施和 FastAPI 连通性检测页面。

## 启动

先启动后端：

```powershell
C:\Users\ASUS\.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

再启动前端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。

开发模式下，Axios 请求 `/backend/api/*`，由 Vite 代理到
`http://127.0.0.1:8000/api/*`，因此不需要修改 FastAPI CORS 配置。
