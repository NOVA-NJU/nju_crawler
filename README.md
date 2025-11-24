# nju_crawler

## 快速开始（开发环境搭建）

1. 创建并激活虚拟环境：
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   ```

2. 安装依赖：
   ```powershell
   pip install -r requirements.txt
   ```

3. 配置环境变量（推荐使用 .env 文件）
   - 在项目根目录新建 `.env` 文件，内容参考如下：
     ```env
     CRAWL_INTERVAL=3600
     REQUEST_TIMEOUT=30
     MAX_RETRIES=3
     AUTO_CRAWL_ENABLED=true
     VECTOR_SYNC_ENABLED=false
     TESSERACT_CMD=
     TESSDATA_DIR=
     CRAWLER_DB_PATH=./data/crawler.db
     ```
   - 所有配置项均可通过 `.env` 文件注入，无需硬编码在 config.py。
   - 推荐使用 [python-dotenv](https://github.com/theskumar/python-dotenv) 自动加载 `.env` 文件。

4. 启动主应用（独立服务模式）：
   ```powershell
   python main.py
   # 或
   uvicorn main:app --reload
   ```

5. 打开 API 文档：
   - Swagger UI: http://127.0.0.1:8000/docs
   - ReDoc: http://127.0.0.1:8000/redoc

## 目录结构说明
- `crawler/`：爬虫模块主包，包含所有核心代码
- `main.py`：主应用入口，可直接运行
- `requirements.txt`：依赖列表
- `venv/`：虚拟环境目录（自动生成，无需提交到Git）
- `.env`：环境变量配置文件（敏感信息请勿提交到Git）

## 项目目录结构

```
nju_crawler/
├─ main.py                # 主应用入口
├─ requirements.txt       # 依赖列表
├─ .env                   # 环境变量配置
├─ crawler/               # 学院官网爬虫模块
│    ├─ __init__.py
│    ├─ config.py
│    ├─ models.py
│    ├─ services.py
│    ├─ router.py
│    └─ lifecycle.py
├─ wechat/                # 微信公众号爬虫模块
│    ├─ __init__.py
│    ├─ config.py
│    ├─ models.py
│    ├─ services.py
│    └─ router.py
├─ storage/               # 公共数据库与API模块
│    ├─ __init__.py
│    ├─ config.py
│    ├─ database.py
│    └─ router.py         # 统一查询API
└─ ...
```

- `crawler/`：学院官网爬虫相关代码
- `wechat/`：公众号爬虫相关代码
- `storage/`：数据库与通用API，crawler/wechat均可调用
- `main.py`：主应用入口，统一挂载各模块路由

## 协作开发建议
- 每位开发者建议使用虚拟环境，避免依赖冲突
- 新增依赖请写入 `requirements.txt`，并及时同步
- 数据库配置请勿提交敏感信息到 Git，可用 `.env` 管理
- 统一通过 `main.py` 或集成到其他 FastAPI 项目
- 如需迁移历史数据，可用 CSV 导出/导入工具

---
如有问题请联系项目维护者.