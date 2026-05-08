"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: config.py
@DateTime: 2026-05-08 22:50:00
@Docs: 定义应用环境变量配置
"""

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

    # Phase 3: Policy 层
    # AIOps 模式：live=按 policy 决策执行（默认）；shadow=只观察不真自动执行（调试新规则用）
    aiops_mode: str = "live"
    # 主机分级（逗号分隔的 IP 列表）；不在两者中的主机默认归 dev
    # VM3 (192.168.198.130) 是测试机，留空使其归 dev
    production_hosts: str = ""
    staging_hosts: str = ""
    # Policy 配置文件路径（容器内绝对路径）
    policy_config_path: str = "/app/src/policy/policies.yaml"

    # Phase 4: 多 Agent 协同
    alert_rate_limit_per_min: int = 100
    max_pending_workflows: int = 50
    correlator_window_sec: int = 30

    # Phase 7: Hermes 知识层
    hermes_db_url: str = "postgresql://temporal:temporal@postgres:5432/temporal"

    # extra="ignore"：允许 .env 里残留旧字段（如 FEISHU_WEBHOOK_URL）不报错，方便平滑切换
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
