"""Google Vertex AI 反向代理服务器

提供带有自动重试、请求过滤和流式响应支持的代理服务
"""
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from config import settings
from logger import setup_logging
from request_processor import RequestProcessor
from retry_client import build_error_response, send_with_retry


# 初始化日志系统
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理应用生命周期和 HTTP 客户端连接池"""
    # 启动时创建 client
    timeout_config = httpx.Timeout(
        settings.request_timeout,
        connect=settings.connect_timeout,
        read=settings.read_timeout,
        write=settings.write_timeout,
        pool=settings.pool_timeout
    )
    # 限制连接池大小，避免过多连接
    limits = httpx.Limits(
        max_keepalive_connections=settings.max_keepalive_connections,
        max_connections=settings.max_connections,
        keepalive_expiry=settings.keepalive_expiry,
    )
    
    logger.info("Initializing HTTP client with connection pooling...")
    client = httpx.AsyncClient(
        timeout=timeout_config,
        limits=limits,
        http2=settings.http2_enabled,
        trust_env=True,
    )
    app.state.http_client = client
    
    yield
    
    # 关闭时清理
    logger.info("Closing HTTP client...")
    await client.aclose()


# 创建 FastAPI 应用
app = FastAPI(
    title="Vertex AI Proxy",
    description="Google Vertex AI API 反向代理服务器，支持自动重试和请求过滤",
    version="2.0.0",
    lifespan=lifespan
)


@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/health/", methods=["GET", "HEAD"])
async def health() -> Response:
    return JSONResponse({"status": "ok"})


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(request: Request, path: str = "") -> Response:
    """代理请求到 Vertex AI API
    
    Args:
        request: FastAPI 请求对象
        path: API 路径
        
    Returns:
        代理响应
    """
    if path.rstrip("/") == "health":
        return JSONResponse({"status": "ok"})

    # 构建目标 URL
    target_url = f"{settings.target_base_url}/{path}"
    if request.query_params:
        target_url += f"?{request.query_params}"
    
    logger.info(f"Proxying request to {target_url}")
    
    # 准备请求头（过滤掉 Host 以避免冲突）
    headers = dict(request.headers)
    headers.pop("host", None)
    
    # 处理请求体
    request_data = await RequestProcessor.process_body(request, path)
    headers.pop("content-length", None)
    
    # 获取共享的 HTTP 客户端
    client: httpx.AsyncClient = request.app.state.http_client
    
    try:
        # 使用函数式 retry 逻辑
        return await send_with_retry(
            client=client,
            request=request,
            method=request.method,
            url=target_url,
            headers=headers,
            content=request_data
        )
    except Exception as e:
        logger.error(f"Unhandled exception in proxy: {e}", exc_info=True)
        return build_error_response(
            error_type="internal_error",
            message=f"Internal Server Error: {str(e)}",
            code=500,
            retryable=False,
            attempts=0,
            path=request.url.path,
        )



if __name__ == '__main__':
    import uvicorn
    
    logger.info(
        f"Starting proxy server on {settings.host}:{settings.port} -> {settings.target_base_url}"
    )
    
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower()
    )
