import asyncio
import logging
import os
from dotenv import load_dotenv
from src.scraper.hiove_scraper import HioveScraper

# Carrega as senhas do .env
load_dotenv()
logging.basicConfig(level=logging.INFO)

async def testar_login_e_conta():
    meu_email = os.getenv("HIOVE_EMAIL")
    minha_senha = os.getenv("HIOVE_PASSWORD")
    
    # is_demo=True garante que ele vai forçar a seleção da conta Demo!
    # (Se um dia quiser operar dinheiro real, basta mudar para is_demo=False aqui)
    scraper = HioveScraper(meu_email, minha_senha, is_demo=True)
    
    # 1. Faz o Login e automaticamente vai trocar para a conta Demo
    await scraper.start()
    
    # Deixa o navegador aberto por 15 segundos para você acompanhar
    await asyncio.sleep(15)
    
    await scraper.close()

if __name__ == "__main__":
    asyncio.run(testar_login_e_conta())