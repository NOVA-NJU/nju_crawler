from __future__ import annotations

from pydantic import BaseModel, HttpUrl
from typing import List


class WechatRequest(BaseModel):
	"""请求体：指定要抓取的 wechat 源 id，或 "all" 表示全部"""
	source: str


class SingleRequest(BaseModel):
	"""请求体：抓取单个文章链接"""
	url: HttpUrl


class ErrorResponse(BaseModel):
	error: str
	code: str = "400"


class WechatResponse(BaseModel):
	code: str = "200"
	data: List[dict]

