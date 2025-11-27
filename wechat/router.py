from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .models import WechatRequest, SingleRequest, ErrorResponse
from . import services
from .config import WECHAT_SOURCES
from crawler.models import CrawlResponse, CrawlItem

router = APIRouter()


@router.post("/api/wechat", response_model=CrawlResponse, responses={404: {"model": ErrorResponse}})
async def wechat_crawl(payload: WechatRequest) -> CrawlResponse:
    try:
        if payload.source == "all":
            data = []
            for src in [s.get("id") for s in WECHAT_SOURCES]:
                data.extend(await services.crawl_wechat_source(src))
        else:
            data = await services.crawl_wechat_source(payload.source)
        return CrawlResponse(data=data)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/api/wechat/single", response_model=CrawlResponse, responses={400: {"model": ErrorResponse}})
async def wechat_single(payload: SingleRequest) -> CrawlResponse:
	try:
		item = await services.crawl_single_article(str(payload.url))
		return CrawlResponse(data=[item] if item else [])
	except Exception as exc:
		raise HTTPException(status_code=400, detail=str(exc))

