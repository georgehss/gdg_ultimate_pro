import asyncio, logging, signal, sys, os
from functools import wraps
from datetime import datetime
from core.config import HIOVE_EMAIL, HIOVE_PASSWORD
from api.broker_api import HioveBrokerAPI
from strategy_manager import StrategyManager
from bot.telegram_bot import TradingTelegramBot
from scraper.hiove_scraper import HioveScraper
from strategies.rsi_strategy import RSIStrategy
from strategies.ma_cross_strategy import MACrossStrategy
from strategies.engulf_ma_strategy import EngulfMAStrategy
from strategies.consensus_strategy import ConsensusStrategy
from core.database import init_db
from api.webhook_server import WebhookServer


# BLOCO PARA CORRIGIR O ERRO DO WINDOWS
if sys.platform == 'win32':
    from asyncio.proactor_events import _ProactorBasePipeTransport
    from asyncio.base_subprocess import BaseSubprocessTransport
    
    def silence_event_loop_closed(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except (RuntimeError, ValueError):
                pass
        return wrapper
        
    # Aplica o silenciador nas duas classes que dão dor de cabeça no Windows
    _ProactorBasePipeTransport.__del__ = silence_event_loop_closed(_ProactorBasePipeTransport.__del__)
    BaseSubprocessTransport.__del__ = silence_event_loop_closed(BaseSubprocessTransport.__del__)


if sys.stdout and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

os.makedirs("logs", exist_ok=True)

# Configura para imprimir no terminal E guardar num ficheiro
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/bot_hiove.log", encoding='utf-8'), # Guarda num ficheiro
        logging.StreamHandler(sys.stdout) # Mantém no terminal
    ]
)
logger = logging.getLogger(__name__)

async def run_session(tg_bot, global_stop_event):
    """Encapsula a execução de uma única sessão do robô."""
    config = tg_bot.user_config
    # NOVO: Grava a data e hora exatas em que a sessão começou
    config["session_start"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"🟢 Iniciando nova sessão com configurações: {config}")

    scraper = HioveScraper(email=HIOVE_EMAIL, password=HIOVE_PASSWORD, is_demo=config["is_demo"])
    await scraper.start()

    saldo_inicial = await scraper.get_balance()
    config["saldo_inicial"] = saldo_inicial
    tipo_conta_str = "DEMO 🟢" if config["is_demo"] else "REAL 🔴"
    await tg_bot.send_alert(f"💰 *Saldo Inicial Capturado!*\n▫️ Conta: {tipo_conta_str}\n▫️ Balanço Atual: ${saldo_inicial:.2f}")

    if config["assets"]:
        logger.info("Criando separadores individuais para cada ativo...")
        for ativo in config["assets"]:
            await scraper.setup_asset_page(symbol=ativo, amount=config["amount"], close_time=config["duration"])
            
            
            # PROTEÇÃO: FECHAR POP-UP NAS ABAS DOS ATIVOS
            if config["is_demo"]:
                aba_do_ativo = scraper.pages.get(ativo)
                if aba_do_ativo:
                    logger.info(f"[{ativo}] Verificando pop-up de desempenho na aba do ativo...")
                    await scraper.handle_demo_performance_popup(target_page=aba_do_ativo)

    broker = HioveBrokerAPI(scraper=scraper, user_config=config)

    broker.limit_reached_cb = tg_bot.send_limit_reached_menu  # Conecta a função de parada suave
    await broker.init_session()
    manager = StrategyManager()

    tg_bot.manager = manager
    tg_bot.broker = broker

    perfil_escolhido = config.get("profile", "Balanceado")
    webhook = None
    strategy_task = None

    # PEGA OS FILTROS DO CONFIG PARA REPASSAR ÀS ESTRATÉGIAS
    filtros = config.get("active_filters", {})

    if config["mode"] in ["strat_rsi", "strategy"]:
        for ativo in config["assets"]:
            strat = RSIStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=config.get("timeframe", "1m"), profile=perfil_escolhido, custom_params=config.get("custom_params"), active_filters=filtros)
            strat.trade_amount, strat.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat)
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert(f"✅ Estratégia RSI ({perfil_escolhido}) iniciada!")

    elif config["mode"] == "strat_ma":
        for ativo in config["assets"]:
            strat = MACrossStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=config.get("timeframe", "1m"), profile=perfil_escolhido, custom_params=config.get("custom_params"), active_filters=filtros)
            strat.trade_amount, strat.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat)
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert(f"✅ Estratégia MA Cross ({perfil_escolhido}) iniciada!")

    elif config["mode"] == "strat_engulf":
        for ativo in config["assets"]:
            strat = EngulfMAStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=config.get("timeframe", "1m"), profile=perfil_escolhido, custom_params=config.get("custom_params"), active_filters=filtros)
            strat.trade_amount, strat.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat)
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert(f"✅ Estratégia Engolfo MA ({perfil_escolhido}) iniciada!")

    elif config["mode"] == "strat_consensus":
        # Lê as escolhas do bot (se não existir, usa um fallback seguro)
        ativas = config.get("active_strategies", ["rsi", "ma", "engulf"])
        votos_necessarios = config.get("min_votes_required", 2)
        
        for ativo in config["assets"]:
            strat = ConsensusStrategy(
                broker=broker, 
                telegram_alert_cb=tg_bot.send_alert, 
                symbol=ativo, 
                timeframe=config.get("timeframe", "1m"), 
                profile=perfil_escolhido,
                custom_params=config.get("custom_params"),
                active_strategies=ativas,            
                min_votes_required=votos_necessarios,
                active_filters=filtros # REPASSA OS FILTROS PARA O CONSENSO AQUI
            )
            strat.trade_amount, strat.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat)
            
        strategy_task = asyncio.create_task(manager.start_all())
        
        # Cria uma mensagem bonitinha de inicialização
        nomes = [s.upper() for s in ativas]
        await tg_bot.send_alert(f"✅ Estratégia de Consenso ({perfil_escolhido}) iniciada!\n▫️ Ativas: {', '.join(nomes)}\n▫️ Exige Mín. {votos_necessarios} votos.")

    elif config["mode"] == "strat_portfolio":
        for ativo in config["assets"]:
            # 1. Instancia o Especialista em Tendência (Engolfo)
            strat_tendencia = EngulfMAStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=config.get("timeframe", "1m"), profile=perfil_escolhido, custom_params=config.get("custom_params"), active_filters=filtros)
            strat_tendencia.trade_amount, strat_tendencia.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat_tendencia)

            # 2. Instancia o Especialista em Lateralidade (RSI)
            strat_lateral = RSIStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=config.get("timeframe", "1m"), profile=perfil_escolhido, custom_params=config.get("custom_params"), active_filters=filtros)
            strat_lateral.trade_amount, strat_lateral.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat_lateral)
            
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert(f"✅ Portfólio Duplo ({perfil_escolhido}) iniciado!\n▫️ Engolfo (Tendências) e RSI (Lateralidade) a operar independentemente no mesmo ativo.")

    elif config["mode"] == "live":
        webhook = WebhookServer(scraper=scraper, telegram_bot=tg_bot, user_config=config)
        await webhook.start(port=8080)
        strategy_task = asyncio.create_task(asyncio.sleep(0))
        await tg_bot.send_alert("📡 Conexão MT5 Pronta! A escutar sinais do seu MetaTrader...")

    else:
        await tg_bot.send_alert(f"⚠️ Modo {config['mode']} não implementado.")
        strategy_task = asyncio.create_task(asyncio.sleep(0))

    # ==========================================
    # GESTÃO DA PARADA (Múltiplas opções de Break)
    # ==========================================
    # Aguarda até que o usuário peça para parar no Telegram OU o terminal seja fechado
    stop_session_task = asyncio.create_task(tg_bot.stop_session_event.wait())
    gstop_task = asyncio.create_task(global_stop_event.wait())

    # Fica em "pause" aqui até um dos dois eventos disparar
    await asyncio.wait([stop_session_task, gstop_task], return_when=asyncio.FIRST_COMPLETED)
    
    # Cancela a tarefa que não foi usada
    for task in [stop_session_task, gstop_task]:
        if not task.done(): task.cancel()

    # ==========================================
    # TEARDOWN: Limpeza dos recursos
    # ==========================================
    logger.info("Iniciando processo de desligamento da sessão...")
    await manager.stop_all()
    if webhook:
        await webhook.stop()
        
    if strategy_task and not strategy_task.done():
        strategy_task.cancel()

    # REQUISITO PRINCIPAL: Fechar as abas dos ativos antes de encerrar
    await scraper.close_asset_tabs()

    # Fecha o navegador inteiro e a conexão com a corretora para evitar lixo na memória
    await broker.close()
    await scraper.close()
    logger.info("Sessão finalizada com sucesso. Memória limpa.")

async def monitorar_terminal(global_stop_event):
    """
    Fica ouvindo o terminal do sistema de forma assíncrona (não bloqueante)
    até que o usuário digite 'exit'.
    """
    loop = asyncio.get_running_loop()
    while not global_stop_event.is_set():
        # Usa um executor para que o readline não congele as outras operações assíncronas do robô
        comando = await loop.run_in_executor(None, sys.stdin.readline)
        
        if comando.strip().lower() == "exit":
            logger.info("⚠️ Comando 'exit' detectado no terminal. Iniciando desligamento seguro...")
            global_stop_event.set()
            break  

async def main():
    logger.info("Iniciando o sistema central do Robô...")
    await init_db()

    tg_bot = TradingTelegramBot(strategy_manager=None, broker=None)
    await tg_bot.start_polling()

    global_stop_event = asyncio.Event()

    # ==========================================
    # BLOQUEIO DO CTRL+C 
    # ==========================================
    def handle_sigint(*args):
        # Apenas avisa, mas NÃO seta o global_stop_event
        print("\n[AVISO] Sinal CTRL+C bloqueado. Digite 'exit' e pressione Enter para encerrar o bot.")

    # Intercepta os sinais tradicionais do OS 
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: 
            loop.add_signal_handler(sig, handle_sigint)
        except NotImplementedError: 
            pass
            
    # Reforço global contra o KeyboardInterrupt (muito útil em Windows)
    signal.signal(signal.SIGINT, handle_sigint)

    # Inicia a tarefa que escuta a digitação do usuário
    terminal_task = asyncio.create_task(monitorar_terminal(global_stop_event))

    logger.info("Aguardando o usuário iniciar via Telegram (Envie /start lá)...")
    logger.info("💡 DICA: Digite 'exit' neste terminal para desligar tudo de vez.")

    # ==========================================
    # LOOP INFINITO DO SERVIDOR CENTRAL
    # ==========================================
    while not global_stop_event.is_set():
        setup_task = asyncio.create_task(tg_bot.setup_event.wait())
        gstop_task = asyncio.create_task(global_stop_event.wait())

        # Fica aguardando até alguém mandar o /start e concluir o setup
        await asyncio.wait([setup_task, gstop_task], return_when=asyncio.FIRST_COMPLETED)
        
        for task in [setup_task, gstop_task]:
            if not task.done(): task.cancel()

        # Se pararam pelo terminal (comando 'exit' disparado), quebra o loop infinito
        if global_stop_event.is_set():
            break

        # Se concluiu o /start no telegram, roda a sessão!
        if tg_bot.setup_event.is_set():
            await run_session(tg_bot, global_stop_event)
            
            tg_bot.setup_event.clear()
            tg_bot.stop_session_event.clear()
            tg_bot.manager = None
            tg_bot.broker = None

    # Cancela a tarefa de terminal para a memória não ficar pendurada
    if not terminal_task.done():
        terminal_task.cancel()

    logger.info("Desligando conexões permanentes (Telegram)...")
    await tg_bot.stop()
    logger.info("Processo principal encerrado de forma segura.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Pega qualquer resíduo se o usuário esmagar o CTRL+C agressivamente
        print("\nBot interrompido/cancelado (Por favor, nas próximas vezes use o comando 'exit').")