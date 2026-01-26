"""重试逻辑模块"""
import asyncio
import logging
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, Optional

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse


from config import settings

logger = logging.getLogger(__name__)


def build_error_payload(
    error_type: str,
    message: str,
    code: int,
    retryable: bool,
    attempts: int,
    path: Optional[str] = None,
    upstream_status: Optional[int] = None,
    retry_after: Optional[float] = None,
    next_retry_in: Optional[float] = None,
) -> dict:
    payload = {
        "error": {
            "type": error_type,
            "message": message,
            "code": code,
            "retryable": retryable,
            "attempts": attempts,
        }
    }
    if path is not None:
        payload["error"]["path"] = path
    if upstream_status is not None:
        payload["error"]["upstream_status"] = upstream_status
    if retry_after is not None:
        payload["error"]["retry_after"] = retry_after
    if next_retry_in is not None:
        payload["error"]["next_retry_in"] = next_retry_in
    return payload


def build_error_response(
    error_type: str,
    message: str,
    code: int,
    retryable: bool,
    attempts: int,
    path: Optional[str] = None,
    upstream_status: Optional[int] = None,
    retry_after: Optional[float] = None,
    next_retry_in: Optional[float] = None,
) -> JSONResponse:
    payload = build_error_payload(
        error_type=error_type,
        message=message,
        code=code,
        retryable=retryable,
        attempts=attempts,
        path=path,
        upstream_status=upstream_status,
        retry_after=retry_after,
        next_retry_in=next_retry_in,
    )
    return JSONResponse(content=payload, status_code=code)


def parse_retry_after_seconds(retry_after: Optional[str]) -> Optional[float]:
    if not retry_after:
        return None
    retry_after = retry_after.strip()
    try:
        # 纯数字：秒
        seconds = float(retry_after)
        if seconds >= 0:
            return seconds
    except ValueError:
        pass

    # HTTP-date
    try:
        dt = parsedate_to_datetime(retry_after)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        seconds = (dt - now).total_seconds()
        return max(0.0, seconds)
    except Exception:
        return None


def compute_retry_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    status_code: Optional[int] = None,
    retry_after_seconds: Optional[float] = None,
) -> tuple[float, str]:
    if retry_after_seconds is not None:
        delay = min(max_delay, max(0.0, retry_after_seconds))
        strategy = "retry-after"
        return delay, strategy

    exp = base_delay * (2 ** attempt)
    exp = min(max_delay, exp)
    # full jitter
    delay = random.uniform(0, exp)
    strategy = "exponential full jitter" if status_code == 429 else "full jitter"
    return delay, strategy


def is_retryable_method(method: str) -> bool:
    method_upper = method.upper()
    non_idempotent = {"POST", "PATCH"}
    if method_upper in non_idempotent and not settings.retry_non_idempotent:
        return False
    return method_upper in settings.retry_methods


def is_retryable_status(status_code: int) -> bool:
    return status_code in settings.retry_status_codes


async def is_client_disconnected(request: Optional[Request]) -> bool:
    if request is None:
        return False
    try:
        return await request.is_disconnected()
    except Exception:
        return False


async def sleep_with_heartbeat(request: Optional[Request], delay: float) -> bool:
    if delay <= 0:
        return True
    interval = max(0.1, settings.client_heartbeat_interval)
    remaining = delay
    while remaining > 0:
        if await is_client_disconnected(request):
            return False
        step = min(interval, remaining)
        await asyncio.sleep(step)
        remaining -= step
    return True


async def stream_response(

    response: httpx.Response,
    url: str,
    request: Optional[Request] = None,
) -> AsyncIterator[bytes]:
    """流式传输响应内容。

    关键点：在生成器内部用 try/finally 确保上游 response 一定被关闭。
    """
    try:
        async for chunk in response.aiter_bytes(chunk_size=settings.stream_chunk_size):
            if request is not None:
                try:
                    if await request.is_disconnected():
                        logger.info(f"Client disconnected, closing upstream stream for {url}")
                        break
                except Exception:
                    pass
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
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.StreamError, httpx.ReadTimeout) as e:
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
    max_delay = settings.max_retry_delay
    allow_retry = is_retryable_method(method)
    
    for attempt in range(max_retries + 1):
        if await is_client_disconnected(request):
            logger.info(f"Client disconnected before attempt {attempt + 1} for {url}")
            return Response(status_code=499)
        try:
            # 构建请求
            built_request = client.build_request(
                method=method,
                url=url,
                headers=headers,
                content=content
            )
            
            # 发送请求
            response = await client.send(built_request, stream=True)

            if await is_client_disconnected(request):
                logger.info(f"Client disconnected after upstream response for {url}")
                await response.aclose()
                return Response(status_code=499)
            
            # 检查是否需要重试 (可配置状态码)
            if is_retryable_status(response.status_code):
                retry_after_header = response.headers.get("retry-after")
                retry_after_seconds = (
                    parse_retry_after_seconds(retry_after_header)
                    if settings.respect_retry_after
                    else None
                )
                if allow_retry and attempt < max_retries:
                    await response.aclose()
                    delay, strategy = compute_retry_delay(
                        attempt=attempt,
                        base_delay=base_delay,
                        max_delay=max_delay,
                        status_code=response.status_code,
                        retry_after_seconds=retry_after_seconds,
                    )

                    logger.warning(
                        f"Retryable error {response.status_code} for {url}. "
                        f"Strategy: {strategy}. Retrying in {delay:.2f}s (Attempt {attempt + 1}/{max_retries})"
                    )
                    should_continue = await sleep_with_heartbeat(request, delay)
                    if not should_continue:
                        logger.info(f"Client disconnected during retry wait for {url}")
                        return Response(status_code=499)
                    continue
                if allow_retry:
                    await response.aclose()
                    is_rate_limited = response.status_code == 429
                    return build_error_response(
                        error_type="rate_limited" if is_rate_limited else "upstream_error",
                        message="Too Many Requests: upstream rate limited." if is_rate_limited else "Bad Gateway: upstream server error.",
                        code=429 if is_rate_limited else 502,
                        retryable=False,
                        attempts=attempt + 1,
                        path=request.url.path,
                        upstream_status=response.status_code,
                        retry_after=retry_after_seconds,
                    )
                
            # 请求成功或不可重试的错误，直接返回
            logger.info(f"Request to {url} finished with status {response.status_code}")
            
            # 处理响应头
            excluded_headers = {'content-encoding', 'content-length', 'transfer-encoding', 'connection', 'host'}
            resp_headers = {
                k: v for k, v in response.headers.items()
                if k.lower() not in excluded_headers
            }
            
            resp_headers["x-proxy-retry-attempts"] = str(attempt)
            return StreamingResponse(
                content=stream_response(response, url, request=request),
                status_code=response.status_code,
                headers=resp_headers,
                media_type=response.headers.get("content-type")
            )

        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
            if allow_retry and attempt < max_retries:
                delay, strategy = compute_retry_delay(
                    attempt=attempt,
                    base_delay=base_delay,
                    max_delay=max_delay,
                )
                logger.warning(
                    f"Network error {e} for {url}. "
                    f"Strategy: {strategy}. Retrying in {delay:.2f}s (Attempt {attempt + 1}/{max_retries})"
                )
                should_continue = await sleep_with_heartbeat(request, delay)
                if not should_continue:
                    logger.info(f"Client disconnected during retry wait for {url}")
                    return Response(status_code=499)
            else:
                logger.warning(f"Max retries exceeded for {url} due to network error: {e}")
                return build_error_response(
                    error_type="network_error",
                    message="Bad Gateway: upstream server disconnected or network error.",
                    code=502,
                    retryable=False,
                    attempts=attempt + 1,
                    path=request.url.path,
                )

        except Exception as e:
            logger.error(f"Unexpected error for {url}: {e}", exc_info=True)
            raise

    # 理论上不应该到这里，因为上面都会 return 或 raise
    return build_error_response(
        error_type="retry_exhausted",
        message="Bad Gateway: max retries exceeded.",
        code=502,
        retryable=False,
        attempts=max_retries + 1,
        path=request.url.path,
    )

