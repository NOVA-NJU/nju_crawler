# nju_crawler

南京大学教育资讯聚合与抓取平台，支持：
- 学院官网资讯抓取
- 微信公众号会话刷新与采集
- 统一 API 查询与存储

## 目录结构

```text
nju_crawler/
├─ main.py
├─ requirements.txt
├─ config/
│  └─ sources/
├─ crawler/
├─ wechat/
├─ storage/
├─ scripts/
│  ├─ refresh_wechat_session.py
│  └─ refresh_wechat_session_gui.py
└─ dist/
   └─ refresh_wechat_session_gui.exe
```

## 快速开始（开发环境）

1. 创建虚拟环境

```bash
python -m venv venv
```

Windows:

```bash
.\venv\Scripts\activate
```

2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 启动服务

```bash
python main.py
```

API 文档：
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

## 微信 Session 刷新工具（推荐给无 Python 环境用户）

本仓库提供 GUI 可执行文件：

- `dist/refresh_wechat_session_gui.exe`

对应 Release 下载：
- [WeChat Session GUI EXE (2026-03-17)](https://github.com/NOVA-NJU/nju_crawler/releases/tag/v2026.03.17-wechat-session-gui)

### 使用方式

1. 双击运行 `refresh_wechat_session_gui.exe`
2. 点击“开始扫码并更新 Session”
3. 在弹出的浏览器页面直接扫码登录（无需再保存二维码图片）
4. 程序拿到 session 后会自动关闭浏览器，并上传到配置的接口

### 产物位置

- `session.json` 默认保存到：`<exe 同目录>/cfg/session.json`
- 运行错误日志：`<exe 同目录>/refresh_wechat_session_error.log`

## 环境变量

### Session 文件位置

- `WECHAT_SESSION_DIR`：会话目录（默认 `<exe目录>/cfg`）
- `WECHAT_SESSION_FILE`：会话文件完整路径（优先级高于 `WECHAT_SESSION_DIR`）

### 上传配置

- `WECHAT_SESSION_SYNC_URLS`：上传地址列表，逗号分隔
- `WECHAT_SESSION_SYNC_AUTH_TOKEN`：Bearer Token
- `WECHAT_SESSION_SYNC_HEADERS`：额外请求头 JSON
- `WECHAT_SESSION_UPLOAD_MODE`：`json` 或 `file`
- `WECHAT_SESSION_FILE_FIELD`：`file` 模式表单字段名
- `WECHAT_SESSION_SYNC_TIMEOUT`：上传超时秒数

### 浏览器优先顺序

- `WECHAT_LOGIN_BROWSERS`：默认 `edge,firefox`

示例：

```env
WECHAT_LOGIN_BROWSERS=edge,firefox
WECHAT_SESSION_SYNC_URLS=https://example.com/api/session,https://example2.com/api/session
WECHAT_SESSION_UPLOAD_MODE=json
WECHAT_SESSION_SYNC_TIMEOUT=60
```

## 常见问题

### 1. 点击开始后不弹浏览器

- 先关闭已运行的旧版 exe 进程再重试
- 查看错误日志：`refresh_wechat_session_error.log`

### 2. 扫码后还提示失败

- 检查网络是否可访问 `https://mp.weixin.qq.com/`
- 检查目标上传接口是否可达、鉴权是否正确

### 3. 浏览器驱动异常

- 默认优先 Edge，失败回退 Firefox
- 可通过 `WECHAT_LOGIN_BROWSERS` 调整顺序

## 开发说明

- 请勿提交敏感信息（token、cookie、私钥）
- 新增依赖请同步 `requirements.txt`
- 建议通过 PR 合并变更并保留变更说明

