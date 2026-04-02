import asyncio
import logging
import os
from datetime import datetime
from dotenv import load_dotenv
import sys

# Adiciona a pasta 'src' ao caminho do Python
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from scraper.hiove_scraper import HioveScraper

# Carrega as senhas do .env
load_dotenv()
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def fluxo_operacao(scraper, ativo_teste, valor_inicial, direcao, max_gales=3, fator_gale=2.2):
    """Função isolada que gerencia a entrada, espera, checagem e MARTINGALE VELA de UM ativo específico."""
    
    valor_atual = valor_inicial
    
    # O loop vai de 0 (Entrada Normal) até max_gales (ex: 1, 2, 3)
    for passo in range(max_gales + 1):
        agora = datetime.now()
        hora_sinal = agora.strftime("%H:%M:%S")
        
        nome_fase = "Entrada Normal" if passo == 0 else f"Martingale {passo}"
        logger.info(f"🚀 [{ativo_teste}] ({nome_fase}) Disparando ordem de {direcao} simulada às {hora_sinal} | Valor: ${valor_atual:.2f}...")
        
        # O trade_lock do scraper (se houver) e o controle assíncrono organizarão os cliques
        resultado_ordem = await scraper.place_order(symbol=ativo_teste, direction=direcao, amount=valor_atual)
        
        if resultado_ordem:
            logger.info(f"✅ [{ativo_teste}] Ordem de {nome_fase} enviada com sucesso na corretora!")
            
            # CÁLCULO DINÂMICO DE ESPERA (REGRA DOS 30 SEGUNDOS) COM +8s DE SEGURANÇA
            agora = datetime.now()
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
                amount=valor_atual, 
                hora_sinal=hora_sinal
            )
            
            logger.info(f"🎯 RESULTADO [{ativo_teste}] ({nome_fase}): {status} | Financeiro: ${lucro:.2f}")
            
            # --- TOMA A DECISÃO COM BASE NO RESULTADO ---
            if status == "WIN":
                logger.info(f"🏆 [{ativo_teste}] WIN alcançado no {nome_fase}! Encerrando fluxo com vitória.")
                break # Sai do loop de martingale
                
            elif status == "EMPATE":
                logger.info(f"➖ [{ativo_teste}] EMPATE no {nome_fase}. Encerrando fluxo (não fazemos gale no empate).")
                break # Sai do loop
                
            elif status == "LOSS":
                if passo < max_gales:
                    # Multiplica o valor para recuperar o prejuízo (arredondado para 2 casas decimais)
                    valor_atual = round(valor_atual * fator_gale, 2)
                    logger.warning(f"⚠️ [{ativo_teste}] LOSS confirmado! Preparando o próximo tiro (Gale {passo+1}) com ${valor_atual:.2f} para a próxima vela...")
                    # Como ele não caiu no 'break', o loop for vai girar e fazer a entrada imediatamente
                else:
                    logger.error(f"🛑 [{ativo_teste}] HIT (Loss Total)! Todos os {max_gales} Martingales falharam. Fim da linha para esta operação.")
                    
            else:
                logger.error(f"❓ [{ativo_teste}] Status da corretora não identificado (Erro de Leitura). Interrompendo martingales por segurança.")
                break # Sai do loop por motivo de segurança
                
        else:
            logger.error(f"❌ [{ativo_teste}] Falha ao clicar no botão de ordem no {nome_fase}.")
            break # Cancela a operação atual inteira


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
            # Cria uma tarefa assíncrona para cada ativo, agora com a função pronta para Martingale
            # Nota: O fator_gale padrão é 2.2 e o max_gales é 3 (como configurado na assinatura da função)
            tarefas.append(fluxo_operacao(scraper, ativo, valor_teste, direcao))
            
        # Executa as 3 operações simultaneamente.
        await asyncio.gather(*tarefas)
            
    except Exception as e:
        logger.error(f"❌ Erro crítico durante o teste: {e}")
        
    finally:
        logger.info("🧹 Encerrando o navegador de teste...")
        await scraper.close()

if __name__ == "__main__":
    asyncio.run(testar_ordem_e_resultado())