import asyncio, logging, signal, sys
from core.config import HIOVE_EMAIL, HIOVE_PASSWORD
from api.broker_api import HioveBrokerAPI
from strategy_manager import StrategyManager
from bot.telegram_bot import TradingTelegramBot
from scraper.hiove_scraper import HioveScraper
from strategies.rsi_strategy import RSIStrategy
from strategies.ma_cross_strategy import MACrossStrategy
from strategies.engulf_ma_strategy import EngulfMAStrategy
from core.database import init_db

# IMPORTANTE: Importa o novo servidor que acabámos de criar
from api.webhook_server import WebhookServer

if sys.stdout and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    logger.info("A iniciar o sistema do Bot de Trading...")

    # INICIALIZA A BASE DE DADOS AQUI
    await init_db()

    tg_bot = TradingTelegramBot(strategy_manager=None, broker=None)
    await tg_bot.start_polling()

    logger.info("⏳ A aguardar pré-configuração do utilizador via Telegram (Envie /start lá)...")
    await tg_bot.setup_event.wait() 
    
    config = tg_bot.user_config
    logger.info(f"🟢 Configurações recebidas: {config}")

    scraper = HioveScraper(email=HIOVE_EMAIL, password=HIOVE_PASSWORD, is_demo=config["is_demo"])
    await scraper.start()

    # ==========================================
    # NOVIDADE: CAPTURAR SALDO INICIAL E ALERTAR
    # ==========================================
    saldo_inicial = await scraper.get_balance()
    config["saldo_inicial"] = saldo_inicial # Guarda o saldo inicial na memória
    
    tipo_conta_str = "DEMO 🟢" if config["is_demo"] else "REAL 🔴"
    await tg_bot.send_alert(
        f"💰 *Saldo Inicial Capturado!*\n"
        f"▫️ Conta: {tipo_conta_str}\n"
        f"▫️ Balanço Atual: ${saldo_inicial:.2f}"
    )
    # ==========================================

    if config["assets"]:
        logger.info("A criar separadores individuais para cada ativo escolhido...")
        for ativo in config["assets"]:
            # Enviamos o amount e o duration logo na hora de abrir a aba!
            await scraper.setup_asset_page(
                symbol=ativo,
                amount=config["amount"],
                close_time=config["duration"]
            )

    broker = HioveBrokerAPI(scraper=scraper, user_config=config)
    await broker.init_session()
    manager = StrategyManager()

    tg_bot.manager = manager
    tg_bot.broker = broker

    # ---------------------------------------------------------
    # DECISÃO DO MODO DE OPERAÇÃO (ESTRATÉGIA INTERNA OU MT5)
    # ---------------------------------------------------------
    webhook = None
    strategy_task = None

    if config["mode"] == "strat_rsi" or config["mode"] == "strategy":
        # MODO 1: Estratégia de RSI
        logger.info("A preparar Estratégia RSI...")
        for ativo in config["assets"]:
            strat = RSIStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=60)
            strat.trade_amount = config["amount"]
            strat.trade_duration = config["duration"]
            manager.add_strategy(strat)
            
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert("✅ Estratégia automática de RSI iniciada!")

    elif config["mode"] == "strat_ma":
        # MODO 2: Estratégia de MA Cross
        logger.info("A preparar Estratégia MA Cross...")
        for ativo in config["assets"]:
            strat = MACrossStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=60)
            strat.trade_amount = config["amount"]
            strat.trade_duration = config["duration"]
            manager.add_strategy(strat)
            
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert("✅ Estratégia automática de MA Cross iniciada!")

    elif config["mode"] == "strat_engulf":
        logger.info("A preparar Estratégia Engolfo MA...")
        for ativo in config["assets"]:
            strat = EngulfMAStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=60)
            strat.trade_amount = config["amount"]
            strat.trade_duration = config["duration"]
            manager.add_strategy(strat)
            
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert("✅ Estratégia automática de Engolfo MA iniciada!")

    elif config["mode"] == "live":
        # MODO 3: SINAIS DO MT5 (WEBHOOK)
        logger.info("A ligar a conexão com o MetaTrader 5...")
        webhook = WebhookServer(scraper=scraper, telegram_bot=tg_bot, user_config=config)
        await webhook.start(port=8080)
        
        # Tarefa vazia apenas para manter a compatibilidade de encerramento
        strategy_task = asyncio.create_task(asyncio.sleep(0))
        await tg_bot.send_alert("📡 Conexão MT5 Pronta! A escutar sinais do seu MetaTrader...")

    else:
        await tg_bot.send_alert(f"⚠️ Modo {config['mode']} não implementado ou em desenvolvimento.")
        strategy_task = asyncio.create_task(asyncio.sleep(0))

    # Graceful Shutdown
    stop_event = asyncio.Event()

    def handle_sigint():
        logger.info("Sinal de paragem recebido. A encerrar...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_sigint)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        logger.info("A fechar as conexões ativas...")
        await manager.stop_all()
        
        # Desliga o Webhook do MT5 ao fechar
        if webhook:
            await webhook.stop()
            
        await tg_bot.stop()
        await broker.close()
        await scraper.close()
        
        if strategy_task and not strategy_task.done():
            strategy_task.cancel()
            
        logger.info("Sistema encerrado com segurança.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot interrompido manualmente pelo utilizador.")