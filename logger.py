"""日志配置模块"""
import logging
import sys
from typing import Optional
from urllib.parse import unquote

from uvicorn.logging import AccessFormatter, DefaultFormatter

from config import settings


class UvicornAccessUrlDecodeFilter(logging.Filter):
    """解码 uvicorn access 日志里的 URL 编码路径。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.args, tuple) and len(record.args) >= 3:
                args = list(record.args)
                if isinstance(args[2], str):
                    args[2] = unquote(args[2])
                    record.args = tuple(args)
        except Exception:
            # 解码失败时保持原始日志
            pass
        return True


def setup_logging(level: Optional[str] = None) -> None:
    """配置应用日志系统
    
    Args:
        level: 日志级别，默认从配置读取
    """
    log_level = level or settings.log_level
    
    # 配置日志格式
    log_format = "%(levelprefix)s %(asctime)s - %(name)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # 配置根日志记录器（使用 uvicorn 的彩色 formatter）
    formatter = DefaultFormatter(fmt=log_format, datefmt=date_format, use_colors=True)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        handlers=[handler]
    )
    
    # 设置第三方库的日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # 统一 uvicorn 日志格式，避免出现 "INFO: ..." 风格
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.setLevel(logging.INFO)
    uvicorn_access.propagate = False
    access_format = "%(levelprefix)s %(asctime)s - %(name)s - %(client_addr)s - \"%(request_line)s\" %(status_code)s"
    access_handler = logging.StreamHandler(sys.stdout)
    access_handler.setFormatter(AccessFormatter(fmt=access_format, datefmt=date_format, use_colors=True))
    access_handler.addFilter(UvicornAccessUrlDecodeFilter())
    uvicorn_access.handlers = [access_handler]

    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.setLevel(getattr(logging, log_level.upper()))
    uvicorn_error.propagate = False
    uvicorn_error.handlers = [handler]
