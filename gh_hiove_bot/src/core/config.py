import os
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env
load_dotenv()

# Credenciais do Bot do Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# Credenciais da Corretora Hiove (Para o Scraper)
HIOVE_EMAIL = os.getenv("HIOVE_EMAIL")
HIOVE_PASSWORD = os.getenv("HIOVE_PASSWORD")

# Token de Segurança do Webhook
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN")