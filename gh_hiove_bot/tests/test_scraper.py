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

async def fluxo_operacao(scraper, ativo_teste, valor_teste, direcao):
    """Função isolada que gerencia a entrada, espera e checagem de UM ativo específico."""
    agora = datetime.now()
    hora_sinal = agora.strftime("%H:%M:%S")
    
    logger.info(f"🚀 [{ativo_teste}] Disparando ordem de {direcao} simulada às {hora_sinal}...")
    
    # Graças ao self.trade_lock dentro do scraper, o bot organizará os cliques sozinho
    resultado_ordem = await scraper.place_order(symbol=ativo_teste, direction=direcao, amount=valor_teste)
    
    if resultado_ordem:
        logger.info(f"✅ [{ativo_teste}] Ordem enviada com sucesso na corretora!")
        
        # CÁLCULO DINÂMICO DE ESPERA (REGRA DOS 30 SEGUNDOS) COM +8s DE SEGURANÇA
        if agora.second <= 30:
            segundos_espera = (60 - agora.second) + 8
        else:
            segundos_espera = (60 - agora.second) + 60 + 8
            
        logger.info(f"⏳ [{ativo_teste}] O bot vai dormir por exatos {segundos_espera} segundos...")
        
        # O bot dorme apenas para esta operação, as outras continuam rodando em paralelo
        await asyncio.sleep(segundos_espera)
        
        logger.info(f"🔎 [{ativo_teste}] O tempo acabou! Checando resultado no histórico...")
        status, lucro = await scraper.check_trade_result(
            symbol=ativo_teste, 
            amount=valor_teste, 
            hora_sinal=hora_sinal
        )
        
        logger.info(f"🎯 RESULTADO FINAL DO TESTE [{ativo_teste}]: {status} | Lucro: ${lucro:.2f}")
    else:
        logger.error(f"❌ [{ativo_teste}] Falha ao clicar no botão de ordem.")


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
        
        ativos = ["XRP/USDT", "SOL/USDT", "ETH/USDT"]
        valor_teste = 2.0
        tempo_teste = "01:00"
        direcao = "BUY"
        
        # ========================================================
        # 2. CONFIGURAÇÃO SEQUENCIAL (Para não bugar o Playwright)
        # ========================================================
        logger.info("🛠️ Iniciando a configuração das abas sequencialmente...")
        for ativo in ativos:
            logger.info(f"⚙️ Configurando aba para {ativo} (${valor_teste} para {tempo_teste})...")
            await scraper.setup_asset_page(symbol=ativo, amount=valor_teste, close_time=tempo_teste)
            await asyncio.sleep(1) # Pausa breve entre configurações para segurança
            
        logger.info("✅ Todas as 3 abas configuradas com sucesso! Preparando para atirar...")
        await asyncio.sleep(2) # Respiro antes do disparo múltiplo
        
        # ========================================================
        # 3. DISPARO E MONITORAMENTO CONCORRENTE (Ao mesmo tempo)
        # ========================================================
        tarefas = []
        for ativo in ativos:
            # Cria uma tarefa assíncrona para cada ativo
            tarefas.append(fluxo_operacao(scraper, ativo, valor_teste, direcao))
            
        # Executa as 3 operações simultaneamente.
        # O self.trade_lock da classe HioveScraper fará a fila de cliques e leitura do histórico
        await asyncio.gather(*tarefas)
            
    except Exception as e:
        logger.error(f"❌ Erro crítico durante o teste: {e}")
        
    finally:
        logger.info("🧹 Encerrando o navegador de teste...")
        await scraper.close()

if __name__ == "__main__":
    asyncio.run(testar_ordem_e_resultado())