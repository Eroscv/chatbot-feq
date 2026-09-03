from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Whapi
    whapi_token: str
    whapi_channel_id: str
    whapi_base_url: str = "https://gate.whapi.cloud"

    # CRM — preenchido quando definido
    crm_api_key: str = ""
    crm_base_url: str = ""
    pipeline_id: str = ""
    stage_id: str = ""

    # Whitelist de números autorizados (separados por vírgula).
    # Se vazio, todos os números são atendidos.
    allowed_phones: str = ""

    log_level: str = "INFO"
    port: int = 8001


def load_settings() -> Settings:
    return Settings()
