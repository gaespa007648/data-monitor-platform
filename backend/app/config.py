from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "data_monitor"
    db_user: str = "data_monitor"
    db_password: SecretStr
    jwt_secret: SecretStr = Field(min_length=32)
    seed_demo_users: bool = False
    demo_admin_password: SecretStr = SecretStr("")
    demo_viewer_password: SecretStr = SecretStr("")

    @property
    def database_url(self) -> URL:
        return URL.create(
            "mysql+asyncmy", username=self.db_user,
            password=self.db_password.get_secret_value(), host=self.db_host,
            port=self.db_port, database=self.db_name, query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
