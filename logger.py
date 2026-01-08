"""日志配置模块"""
import logging
import sys
from typing import Optional

from config import settings


def setup_logging(level: Optional[str] = None) -> None:
    """配置应用日志系统
    
    Args:
        level: 日志级别，默认从配置读取
    """
    log_level = level or settings.log_level
    
    # 配置日志格式
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # 配置根日志记录器
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # 设置第三方库的日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
