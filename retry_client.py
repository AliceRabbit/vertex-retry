"""重试逻辑模块"""
import asyncio
import logging
import random
from typing import AsyncIterator, Optional

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from config import settings

logger = logging.getLogger(__name__)


async def stream_response(
    response: httpx.Response,
    url: str,
    request: Optional[Request] = None,
) -> AsyncIterator[bytes]:
    """流式传输响应内容。

    关键点：在生成器内部用 try/finally 确保上游 response 一定被关闭。
    """
    try:
        async for chunk in response.aiter_bytes(chunk_size=8192):
            yield chunk
    except asyncio.CancelledError:
        # 客户端断开连接时，ASGI 通常会取消正在进行的 streaming 任务
        if request is not None:
            try:
                if await request.is_disconnected():
                    logger.info(f"Client disconnected while streaming {url}")
                else:
                    logger.info(f"Streaming task cancelled for {url}")
            except Exception:
                logger.info(f"Streaming task cancelled for {url}")
        else:
            logger.info(f"Streaming task cancelled for {url}")
        raise
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.StreamError) as e:
        # 可能是上游断流，也可能是下游断开导致的读取中断；通过 request.is_disconnected() 区分
        is_client_disconnect = False
        if request is not None:
            try:
                is_client_disconnect = await request.is_disconnected()
            except Exception:
                is_client_disconnect = False

        if is_client_disconnect:
            logger.info(f"Client disconnected while streaming {url}: {e}")
        else:
            logger.warning(f"Upstream stream interrupted for {url}: {e}")
    except Exception as e:
        is_client_disconnect = False
        if request is not None:
            try:
                is_client_disconnect = await request.is_disconnected()
            except Exception:
                is_client_disconnect = False

        if is_client_disconnect:
            logger.info(f"Client disconnected while streaming {url}: {e}")
        else:
            logger.error(f"Stream interrupted with unexpected error for {url}: {e}", exc_info=True)
    finally:
        try:
            await response.aclose()
        except Exception as e:
            logger.debug(f"Failed to close upstream response for {url}: {e}")

async def send_with_retry(
    client: httpx.AsyncClient,
    request: Request,
    method: str,
    url: str,
    headers: dict,
    content: bytes
) -> Response:
    """发送 HTTP 请求，支持自动重试
    
    Args:
        client: 共享的 httpx client 实例
        method: HTTP 方法
        url: 请求 URL
        headers: 请求头
        content: 请求体
        
    Returns:
        FastAPI StreamingResponse 对象
    """
    max_retries = settings.max_retries
    base_delay = settings.base_retry_delay
    
    for attempt in range(max_retries + 1):
        try:
            # 构建请求
            request = client.build_request(
                method=method,
                url=url,
                headers=headers,
                content=content
            )
            
            # 发送请求
            response = await client.send(request, stream=True)
            
            # 检查是否需要重试 (5xx 或 429)
            if response.status_code >= 500 or response.status_code == 429:
                if attempt < max_retries:
                    await response.aclose()
                    
                    # 区分重试策略
                    if response.status_code == 429:
                        # 429 触发指数退避: 1s, 2s, 4s, 8s...
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        strategy = "exponential backoff"
                    else:
                        # 5xx 触发快速重试: 首次 0.5s, 之后线性增加
                        # 0.5s, 1.0s, 1.5s, 2.0s...
                        if attempt == 0:
                            delay = 0.5 + random.uniform(0, 0.2)
                        else:
                            delay = base_delay * (1 + attempt * 0.5) + random.uniform(0, 0.5)
                        strategy = "fast retry"

                    logger.warning(
                        f"Retryable error {response.status_code} for {url}. "
                        f"Strategy: {strategy}. Retrying in {delay:.2f}s (Attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue
                
            # 请求成功或不可重试的错误，直接返回
            logger.info(f"Request to {url} finished with status {response.status_code}")
            
            # 处理响应头
            excluded_headers = {'content-encoding', 'content-length', 'transfer-encoding', 'connection', 'host'}
            resp_headers = {
                k: v for k, v in response.headers.items()
                if k.lower() not in excluded_headers
            }
            
            return StreamingResponse(
                content=stream_response(response, url, request=request),
                status_code=response.status_code,
                headers=resp_headers,
                media_type=response.headers.get("content-type")
            )

        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
            if attempt < max_retries:
                # 网络错误使用快速重试策略
                if attempt == 0:
                    delay = 0.5 + random.uniform(0, 0.2)
                else:
                    delay = base_delay * (1 + attempt * 0.5) + random.uniform(0, 0.5)
                    
                logger.warning(
                    f"Network error {e} for {url}. "
                    f"Strategy: fast retry. Retrying in {delay:.2f}s (Attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(f"Max retries exceeded for {url} due to network error: {e}")
                return Response(
                    content="Bad Gateway: upstream server disconnected or network error.",
                    status_code=502,
                )
        except Exception as e:
            logger.error(f"Unexpected error for {url}: {e}", exc_info=True)
            raise

    # 理论上不应该到这里，因为上面都会 return 或 raise
    return Response(
        content="Bad Gateway: max retries exceeded.",
        status_code=502,
    )
