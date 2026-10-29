import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    database_url: str = os.getenv('DATABASE_URL', 'sqlite:///./duewise.db')
    environment: str = os.getenv('APP_ENV', 'development')
    encryption_key: str = os.getenv('ENCRYPTION_KEY', '')
    redis_url: str = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
    email_mode: str = os.getenv('EMAIL_MODE', 'preview')
    email_api_key: str = os.getenv('EMAIL_API_KEY', '')
    email_from: str = os.getenv('EMAIL_FROM', '')
    email_webhook_secret: str = os.getenv('EMAIL_WEBHOOK_SECRET', '')
    allowed_recipients: str = os.getenv('EMAIL_ALLOWED_RECIPIENTS', '')
    demo: bool = os.getenv('DEMO_MODE', 'false').lower() == 'true'


config = Config()
