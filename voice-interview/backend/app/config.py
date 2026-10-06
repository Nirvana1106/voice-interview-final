import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    pg_host: str = os.getenv("PGHOST", "db")
    pg_user: str = os.getenv("PGUSER", "interview")
    pg_password: str = os.getenv("PGPASSWORD", "")
    pg_database: str = os.getenv("PGDATABASE", "interview")
    model_base_url: str = os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model_api_key: str = os.getenv("MODEL_API_KEY", "")
    model_name: str = os.getenv("MODEL_NAME", "gpt-4o-mini")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    demo_password_1: str = os.getenv("DEMO_PASSWORD_1", "InterviewDemo123!")
    demo_password_2: str = os.getenv("DEMO_PASSWORD_2", "InterviewDemo456!")


settings = Settings()
