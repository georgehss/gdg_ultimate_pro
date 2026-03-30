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
    
    def silence_event_loop_closed(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except (RuntimeError, ValueError):
                pass
        return wrapper
        
    _ProactorBasePipeTransport.__del__ = silence_event_loop_closed(_ProactorBasePipeTransport.__del__)


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

    broker = HioveBrokerAPI(scraper=scraper, user_config=config)
    broker.limit_reached_cb = tg_bot.send_limit_reached_menu  # Conecta a função de parada suave
    await broker.init_session()
    manager = StrategyManager()

    tg_bot.manager = manager
    tg_bot.broker = broker

    perfil_escolhido = config.get("profile", "Balanceado")
    webhook = None
    strategy_task = None

    if config["mode"] in ["strat_rsi", "strategy"]:
        for ativo in config["assets"]:
            strat = RSIStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=config.get("timeframe", "1m"), profile=perfil_escolhido)
            strat.trade_amount, strat.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat)
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert(f"✅ Estratégia RSI ({perfil_escolhido}) iniciada!")

    elif config["mode"] == "strat_ma":
        for ativo in config["assets"]:
            strat = MACrossStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=config.get("timeframe", "1m"), profile=perfil_escolhido)
            strat.trade_amount, strat.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat)
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert(f"✅ Estratégia MA Cross ({perfil_escolhido}) iniciada!")

    elif config["mode"] == "strat_engulf":
        for ativo in config["assets"]:
            strat = EngulfMAStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=config.get("timeframe", "1m"), profile=perfil_escolhido)
            strat.trade_amount, strat.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat)
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert(f"✅ Estratégia Engolfo MA ({perfil_escolhido}) iniciada!")

    elif config["mode"] == "strat_consensus":
        for ativo in config["assets"]:
            strat = ConsensusStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=config.get("timeframe", "1m"), profile=perfil_escolhido)
            strat.trade_amount, strat.trade_duration = config["amount"], config["duration"]
            manager.add_strategy(strat)
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert(f"✅ Estratégia de Consenso ({perfil_escolhido}) iniciada!")

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


async def main():
    logger.info("Iniciando o sistema central do Robô...")
    await init_db()

    tg_bot = TradingTelegramBot(strategy_manager=None, broker=None)
    await tg_bot.start_polling()

    global_stop_event = asyncio.Event()

    def handle_sigint():
        logger.warning("Sinal de interrupção forçada no Terminal recebido (CTRL+C).")
        global_stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, handle_sigint)
        except NotImplementedError: pass

    logger.info("Aguardando o usuário iniciar via Telegram (Envie /start lá)...")

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

        # Se pararam pelo terminal (Ctrl+C), quebra o loop infinito e morre de vez
        if global_stop_event.is_set():
            break

        # Se concluiu o /start no telegram, roda a sessão!
        if tg_bot.setup_event.is_set():
            await run_session(tg_bot, global_stop_event)
            
            # Quando a sessão terminar (via comando /stop), resetamos os eventos para permitir uma nova sessão
            tg_bot.setup_event.clear()
            tg_bot.stop_session_event.clear()
            tg_bot.manager = None
            tg_bot.broker = None

    logger.info("Desligando conexões permanentes (Telegram)...")
    await tg_bot.stop()
    logger.info("Processo principal encerrado de forma segura.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot interrompido manualmente pelo usuário.")