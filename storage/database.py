"""
爬虫数据持久化的SQLite数据库操作工具。
负责表结构初始化、连接管理、记录插入/查询/同步等。
所有关键函数和字段均有详细注释。
"""
from __future__ import annotations  # 兼容未来类型注解语法

import json
import glob
from datetime import datetime

import sqlite3  # 标准库SQLite操作
from contextlib import contextmanager  # 上下文管理器，简化连接关闭
from pathlib import Path  # 路径处理
from typing import Generator, Iterable, Optional  # 类型注解

from storage.config import DATABASE_PATH  # 数据库文件路径配置

# 数据库表结构定义，包含爬取记录所有字段
SCHEMA = """
CREATE TABLE IF NOT EXISTS crawled_records (
    id TEXT PRIMARY KEY,              -- 唯一ID
    title TEXT NOT NULL,              -- 标题
    url TEXT NOT NULL,                -- 详情页链接
    publish_time TEXT,                -- 发布时间
    source_id TEXT,                   -- 来源ID
    source_name TEXT,                 -- 来源名称
    attachments TEXT,                 -- 附件JSON
    content TEXT,                     -- 详情页内容
    created_at TEXT DEFAULT CURRENT_TIMESTAMP -- 创建时间
);
CREATE INDEX IF NOT EXISTS idx_crawled_records_url ON crawled_records(url); -- 加速URL查询
"""

def query_records(source_ids: list, start_time: str, end_time: str) -> list:
    """
    查询指定 source_ids（列表）相关的所有记录，时间范围为 start_time 到 end_time。
    - 对于以 'wechat_' 开头的 source_id，直接作为查询条件。
    - 对于其他 source_id，查找 config/sources/ 目录下对应的 json 文件，加载其中定义的所有 id。
    返回结果为 JSON 格式的列表。
    """
    all_ids = []
    
    # 分离直接查询的ID和需要查找配置文件的ID
    direct_ids = [sid for sid in source_ids if sid.startswith("wechat_")]
    config_ids = [sid for sid in source_ids if not sid.startswith("wechat_")]
    
    # 1. 处理直接查询的ID
    all_ids.extend(direct_ids)
    
    # 2. 处理需要查找配置文件的ID
    if config_ids:
        config_files = []
        for src in config_ids:
            config_files.extend(glob.glob(f"config/sources/{src}.json"))
        
        for file in config_files:
            try:
                with open(file, encoding="utf-8") as f:
                    data = json.load(f)
                for src in data.get("sources", []):
                    sid = src.get("id")
                    if sid:
                        all_ids.append(sid)
            except Exception:
                continue
                
    if not all_ids:
        return []
        
    # 3. 查询数据库
    results = []
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.execute(
            f"""
            SELECT id, title, url, publish_time, source_id, source_name, attachments, content, created_at
            FROM crawled_records
            WHERE source_id IN ({','.join(['?']*len(all_ids))})
              AND publish_time >= ? AND publish_time <= ?
            ORDER BY publish_time DESC
            """,
            (*all_ids, start_time, end_time)
        )
        columns = [desc[0] for desc in cursor.description]
        for row in cursor.fetchall():
            results.append(dict(zip(columns, row)))
    return results

def get_failed_records() -> list[dict]:
    """
    查询所有标题和正文都为空的记录（即抓取失败的记录）。
    返回包含 url, source_id, source_name, date (publish_time) 的字典列表。
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT url, source_id, source_name, publish_time, title
            FROM crawled_records
            WHERE (title IS NULL OR title = '') OR (content IS NULL OR content = '')
            """
        )
        results = []
        for row in cursor.fetchall():
            results.append({
                "url": row[0],
                "source_id": row[1],
                "source_name": row[2],
                "date": row[3],
                "title": row[4]
            })
        return results

def get_failed_wechat_records() -> list[dict]:
    """
    查询所有标题或正文为空的微信公众号文章记录。
    仅针对 source_id 以 'wechat_' 开头的记录。
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT id, url, source_id, source_name, publish_time, title
            FROM crawled_records
            WHERE source_id LIKE 'wechat_%'
              AND ((title IS NULL OR title = '') OR (content IS NULL OR content = ''))
            """
        )
        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "url": row[1],
                "source_id": row[2],
                "source_name": row[3],
                "publish_time": row[4],
                "title": row[5]
            })
        return results

def initialize() -> None:
    """
    初始化数据库文件和表结构，确保可用。
    若目录不存在则自动创建。
    """
    path = Path(DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)

def record_exists(record_id: str, url: Optional[str] = None) -> bool:
    """
    判断指定ID或URL的记录是否已存在。
    用于去重，避免重复入库。
    
    策略A：如果记录存在，但内容为空（上次抓取失败），则视为不存在，允许覆盖。
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        if url:
            cursor = conn.execute("SELECT content, title FROM crawled_records WHERE id=? OR url=?", (record_id, url))
        else:
            cursor = conn.execute("SELECT content, title FROM crawled_records WHERE id=?", (record_id,))
            
        rows = cursor.fetchall()
        if not rows:
            return False
        
        # 检查所有匹配的记录（可能有多条，比如旧ID和新ID）
        # 只要有一条记录内容有效，就视为存在
        for row in rows:
            content, title = row
            if content and title:
                return True
            
        # 所有匹配记录都为空，允许覆盖
        return False

def delete_record(record_id: str) -> None:
    """
    根据ID删除记录。
    用于清理无效或已删除的文章。
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("DELETE FROM crawled_records WHERE id=?", (record_id,))
        conn.commit()

def store_document(item_id: str, content: str, metadata: dict) -> None:
    """
    存储文档内容及元数据到本地SQLite。
    item_id: 唯一ID
    content: 详情页内容
    metadata: 需包含 title, url, publish_time, source_id, source_name, attachments (JSON字符串)
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO crawled_records
            (id, title, url, publish_time, source_id, source_name, attachments, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                metadata.get("title", ""),
                metadata.get("url", ""),
                metadata.get("publish_time"),
                metadata.get("source_id", ""),
                metadata.get("source_name", ""),
                metadata.get("attachments"),
                content,
            ),
        )
        conn.commit()
