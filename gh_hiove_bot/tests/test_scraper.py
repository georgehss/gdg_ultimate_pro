import asyncio
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sys

# Adiciona a pasta 'src' ao caminho do Python
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from scraper.hiove_scraper import HioveScraper

# Carrega as senhas do .env
load_dotenv()
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def testar_ordem_e_resultado():
    meu_email = os.getenv("HIOVE_EMAIL")
    minha_senha = os.getenv("HIOVE_PASSWORD")
    
    if not meu_email or not minha_senha:
        logger.error("❌ Credenciais não encontradas! Verifique o arquivo .env.")
        return

    # is_demo=True garante que não gasta dinheiro real no teste
    scraper = HioveScraper(meu_email, minha_senha, is_demo=True)
    
    try:
        # 1. Faz Login e liga o sistema Anti-Inatividade
        await scraper.start()
        
        ativo_teste = "ETH/USDT"
        valor_teste = 5.0
        tempo_teste = "01:00"
        
        # 2. Configura a aba do ativo
        logger.info(f"🛠️ Configurando aba para {ativo_teste} (${valor_teste} para {tempo_teste})...")
        await scraper.setup_asset_page(symbol=ativo_teste, amount=valor_teste, close_time=tempo_teste)
        
        # 3. Dispara a Ordem e capta a hora exata
        direcao = "BUY"
        agora = datetime.now()
        hora_sinal = agora.strftime("%H:%M:%S")
        
        logger.info(f"🚀 Disparando ordem de {direcao} simulada às {hora_sinal}...")
        resultado_ordem = await scraper.place_order(symbol=ativo_teste, direction=direcao, amount=valor_teste)
        
        if resultado_ordem:
            logger.info("✅ Ordem enviada com sucesso na corretora!")
            
            # ========================================================
            # NOVO: CÁLCULO DINÂMICO DE ESPERA (REGRA DOS 30 SEGUNDOS)
            # ========================================================
            if agora.second <= 30:
                # Se clicou até o segundo 30, a operação pertence ao minuto atual e fecha na virada dele.
                # Exemplo: 10:35:15 -> Faltam 45s para virar o minuto + 5s de segurança = 50s de espera.
                segundos_espera = (60 - agora.second) + 5
                minuto_corretora = agora.strftime("%H:%M")
            else:
                # Se passou do segundo 30, a operação é empurrada para a vela do minuto seguinte.
                # Exemplo: 10:35:45 -> Faltam 15s para virar o minuto + 60s de vela + 5s de segurança = 80s de espera.
                segundos_espera = (60 - agora.second) + 60 + 5
                minuto_corretora = (agora + timedelta(minutes=1)).strftime("%H:%M")
            
            logger.info(f"⏱️ A corretora registrará esta ordem sob o minuto: {minuto_corretora}")
            logger.info(f"⏳ O bot vai dormir por exatos {segundos_espera} segundos para sincronizar com o fim da vela...")
            
            await asyncio.sleep(segundos_espera)
            
            # 4. Vai ao histórico conferir se a nossa nova lógica funciona perfeitamente
            logger.info("🔎 O tempo acabou! Checando resultado no histórico...")
            status, lucro = await scraper.check_trade_result(
                symbol=ativo_teste, 
                amount=valor_teste, 
                hora_sinal=hora_sinal
            )
            
            logger.info(f"🎯 RESULTADO FINAL DO TESTE: {status} | Lucro: ${lucro:.2f}")
        else:
            logger.error("❌ Falha ao clicar no botão de ordem.")
            
    except Exception as e:
        logger.error(f"❌ Erro crítico durante o teste: {e}")
        
    finally:
        logger.info("🧹 Encerrando o navegador de teste...")
        await scraper.close()

if __name__ == "__main__":
    asyncio.run(testar_ordem_e_resultado())