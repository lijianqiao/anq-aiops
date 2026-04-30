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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
