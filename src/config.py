from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """从环境变量读取配置"""

    redis_url: str = "redis://localhost:6379/0"
    temporal_address: str = "localhost:7233"
    temporal_task_queue: str = "aiops-alerts"
    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
