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
    max_retries: int = Field(default=10, description="最大重试次数")
    base_retry_delay: float = Field(default=1.0, description="基础重试延迟(秒)")
    max_retry_delay: float = Field(default=20.0, description="最大重试延迟上限(秒)")
    respect_retry_after: bool = Field(default=True, description="是否尊重上游 Retry-After")
    retry_non_idempotent: bool = Field(default=True, description="是否允许对非幂等方法重试")
    retry_status_codes: list[int] = Field(
        default_factory=lambda: [429, 500, 502, 503, 504],
        description="需要重试的上游状态码"
    )
    retry_methods: list[str] = Field(
        default_factory=lambda: ["GET", "PUT", "DELETE", "PATCH", "POST"],
        description="允许重试的 HTTP 方法"
    )

    # 超时配置
    request_timeout: float = Field(default=500.0, description="总请求超时时间(秒)")
    connect_timeout: float = Field(default=5.0, description="连接建立超时时间(秒)")
    read_timeout: float = Field(default=60.0, description="读取数据超时时间(秒)")
    write_timeout: float = Field(default=30.0, description="写入数据超时时间(秒)")
    pool_timeout: float = Field(default=10.0, description="连接池等待超时时间(秒)")

    # 连接池与协议
    http2_enabled: bool = Field(default=True, description="是否启用 HTTP/2")
    max_keepalive_connections: int = Field(default=50, description="保持活动连接的最大数量")
    max_connections: int = Field(default=200, description="最大并发连接数")
    keepalive_expiry: float = Field(default=30.0, description="空闲连接保持时间(秒)")

    # 流式传输
    stream_chunk_size: int = Field(default=65536, description="流式传输的 chunk 大小(字节)")

    # 客户端心跳检查
    client_heartbeat_interval: float = Field(
        default=1.0,
        description="客户端心跳检查间隔(秒)，用于检测断开并停止重试/上游连接"
    )
    
    # 日志配置
    log_level: str = Field(default="INFO", description="日志级别")
    
    class Config:
        env_prefix = "PROXY_"  # 环境变量前缀
        case_sensitive = False


# 全局配置实例
settings = Settings()
