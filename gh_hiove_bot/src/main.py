import asyncio, logging, signal, sys
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

    # ==========================================
    # DEFINIÇÃO DOS PERFIS DE ESTRATÉGIA
    # ==========================================
    perfil_escolhido = config.get("profile", "Balanceado")
    
    # Parâmetros para RSI
    rsi_profiles = {
        "Conservador": {"rsi_period": 14, "rsi_overbought": 75, "rsi_oversold": 25, "long_ma_period": 200},
        "Balanceado":  {"rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30, "long_ma_period": 100},
        "Agressivo":   {"rsi_period": 9,  "rsi_overbought": 65, "rsi_oversold": 35, "long_ma_period": 50},
        "Customizado": {} # Pega os defaults da classe
    }
    
    # Parâmetros para MA Cross
    ma_profiles = {
        "Conservador": {"fast_period": 14, "slow_period": 50, "cooldown_bars": 5, "atr_sep_mult": 0.20},
        "Balanceado":  {"fast_period": 9,  "slow_period": 21, "cooldown_bars": 3, "atr_sep_mult": 0.15},
        "Agressivo":   {"fast_period": 5,  "slow_period": 13, "cooldown_bars": 1, "atr_sep_mult": 0.05},
        "Customizado": {}
    }

    # Parâmetros para Engolfo
    engulf_profiles = {
        "Conservador": {"ma_period": 14, "long_ma_period": 100, "epsilon_price": 0.0001},
        "Balanceado":  {"ma_period": 8,  "long_ma_period": 59,  "epsilon_price": 0.0},
        "Agressivo":   {"ma_period": 5,  "long_ma_period": 21,  "epsilon_price": -0.0001}, # Aceita engolfos quase imperfeitos
        "Customizado": {}
    }
    # ==========================================

    # ---------------------------------------------------------
    # DECISÃO DO MODO DE OPERAÇÃO (ESTRATÉGIA INTERNA OU MT5)
    # ---------------------------------------------------------
    webhook = None
    strategy_task = None

    if config["mode"] == "strat_rsi" or config["mode"] == "strategy":
        # MODO 1: Estratégia de RSI
        logger.info(f"A preparar Estratégia RSI (Perfil: {perfil_escolhido})...")
        kwargs = rsi_profiles.get(perfil_escolhido, {})
        for ativo in config["assets"]:
            strat = RSIStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=60, **kwargs)
            strat.trade_amount = config["amount"]
            strat.trade_duration = config["duration"]
            manager.add_strategy(strat)
            
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert("✅ Estratégia automática de RSI iniciada!")

    elif config["mode"] == "strat_ma":
        # MODO 2: Estratégia de MA Cross
        logger.info(f"A preparar Estratégia MA Cross (Perfil: {perfil_escolhido})...")
        kwargs = ma_profiles.get(perfil_escolhido, {})
        for ativo in config["assets"]:
            strat = MACrossStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=60, **kwargs)
            strat.trade_amount = config["amount"]
            strat.trade_duration = config["duration"]
            manager.add_strategy(strat)
            
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert("✅ Estratégia automática de MA Cross iniciada!")

    elif config["mode"] == "strat_engulf":
        logger.info(f"A preparar Estratégia de Engolfo MA (Perfil: {perfil_escolhido})...")
        kwargs = engulf_profiles.get(perfil_escolhido, {})
        for ativo in config["assets"]:
            strat = EngulfMAStrategy(broker, tg_bot.send_alert, symbol=ativo, timeframe=60, **kwargs)
            strat.trade_amount = config["amount"]
            strat.trade_duration = config["duration"]
            manager.add_strategy(strat)
            
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert("✅ Estratégia automática de Engolfo MA iniciada!")

    elif config["mode"] == "strat_consensus":
        logger.info(f"A preparar Estratégia de Consenso (Perfil: {perfil_escolhido})...")
        
        # Pega as configurações de todas as estratégias com base no perfil
        r_kwargs = rsi_profiles.get(perfil_escolhido, {})
        m_kwargs = ma_profiles.get(perfil_escolhido, {})
        e_kwargs = engulf_profiles.get(perfil_escolhido, {})

        for ativo in config["assets"]:
            # Passa os 3 pacotes de parâmetros para a classe de Consenso
            strat = ConsensusStrategy(
                broker, 
                tg_bot.send_alert, 
                symbol=ativo, 
                timeframe=60,
                rsi_kwargs=r_kwargs,
                ma_kwargs=m_kwargs,
                engulf_kwargs=e_kwargs
            )
            strat.trade_amount = config["amount"]
            strat.trade_duration = config["duration"]
            manager.add_strategy(strat)
            
        strategy_task = asyncio.create_task(manager.start_all())
        await tg_bot.send_alert(f"✅ Estratégia automática de Consenso ({perfil_escolhido}) iniciada!")

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