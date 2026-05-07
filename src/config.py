from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """从环境变量读取配置"""

    redis_url: str = "redis://localhost:6379/0"
    temporal_address: str = "localhost:7233"
    temporal_task_queue: str = "aiops-alerts"

    # Feishu 应用机器人凭据（开放平台 -> 应用 -> 凭证与基础信息）
    # 同一对凭据用于：① IM v1 发卡片  ② 长连接接收卡片回调
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    # 默认接收对象，chat_id / open_id / user_id / email / union_id
    feishu_receive_id: str = ""
    feishu_receive_id_type: str = "chat_id"

    zabbix_webhook_token: str = ""
    audit_log_path: str = "./audit.log"
    ansible_private_data_dir: str = "./ansible"
    ansible_inventory: str = "./ansible/inventory.ini"

    # LLM - Primary
    llm_primary_provider: str = "openai"
    llm_primary_base_url: str = "https://api.openai.com/v1"
    llm_primary_api_key: str = ""
    llm_primary_model: str = "gpt-4o"

    # LLM - Fallback
    llm_fallback_provider: str = "openai"
    llm_fallback_base_url: str = "http://localhost:8080/v1"
    llm_fallback_api_key: str = "not-needed"
    llm_fallback_model: str = "local-model"

    # LLM - General
    llm_timeout: float = 30
    llm_circuit_breaker_threshold: float = 0.3

    # extra="ignore"：允许 .env 里残留旧字段（如 FEISHU_WEBHOOK_URL）不报错，方便平滑切换
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
