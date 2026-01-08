"""配置管理模块"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""
    
    # 服务器配置
    host: str = Field(default="0.0.0.0", description="服务器监听地址")
    port: int = Field(default=5000, description="服务器监听端口")
    
    # 代理目标配置
    target_base_url: str = Field(
        default="https://aiplatform.googleapis.com",
        description="目标 Google Vertex AI 的基础 URL"
    )
    
    # 重试配置
    max_retries: int = Field(default=5, description="最大重试次数")
    base_retry_delay: float = Field(default=1.0, description="基础重试延迟(秒)")
    request_timeout: float = Field(default=500.0, description="请求超时时间(秒)")
    connect_timeout: float = Field(default=5.0, description="连接建立超时时间(秒)")
    read_timeout: float = Field(default=60.0, description="读取数据超时时间(秒)")
    
    # 日志配置
    log_level: str = Field(default="INFO", description="日志级别")
    
    class Config:
        env_prefix = "PROXY_"  # 环境变量前缀
        case_sensitive = False


# 全局配置实例
settings = Settings()
