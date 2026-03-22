import asyncio, logging, time
from .base_strategy import BaseStrategy
from .rsi_strategy import RSIStrategy
from .ma_cross_strategy import MACrossStrategy
from .engulf_ma_strategy import EngulfMAStrategy

logger = logging.getLogger(__name__)

class ConsensusStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60):
        super().__init__(name=f"Consensus_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.symbol = symbol
        self.timeframe = timeframe
        
        # Instancia as 3 estratégias silenciosamente
        self.strat_rsi = RSIStrategy(broker, telegram_alert_cb, symbol, timeframe)
        self.strat_ma = MACrossStrategy(broker, telegram_alert_cb, symbol, timeframe)
        self.strat_engulf = EngulfMAStrategy(broker, telegram_alert_cb, symbol, timeframe)
        
        self.trade_amount = 1.0
        self.trade_duration = "01:00"

    async def analyze_market(self):
        # Executa as 3 análises de forma assíncrona ao mesmo tempo
        results = await asyncio.gather(
            self.strat_rsi.analyze_market(),
            self.strat_ma.analyze_market(),
            self.strat_engulf.analyze_market()
        )

        votes_buy = 0
        votes_sell = 0

        # Conta os votos de cada estratégia que não devolveu "None"
        for res in results:
            if res:
                direction, msg = res
                if direction == "BUY": votes_buy += 1
                elif direction == "SELL": votes_sell += 1

        # Avalia a Maioria (Harmonia e Prevenção de Conflitos)
        if votes_buy > votes_sell:
            return "BUY", votes_buy
        elif votes_sell > votes_buy:
            return "SELL", votes_sell
        else:
            if votes_buy > 0: # Ex: 1 BUY e 1 SELL (Conflito direto)
                logger.info(f"[{self.name}] ⚠️ Empate/Conflito detectado (1 BUY, 1 SELL). Operação abortada por segurança.")
            return None

    async def execute(self):
        self.is_running = True
        logger.info(f"[{self.name}] 🤝 Estratégia de CONSENSO (3 em 1) iniciada.")
        
        while self.is_running:
            try:
                agora = time.time()
                segundos_atuais = agora % 60
                espera = 60 - segundos_atuais
                await asyncio.sleep(espera + 1.0) 
                
                # Pergunta ao coordenador qual foi a decisão
                resultado = await self.analyze_market()
                
                if resultado:
                    direction, num_votos = resultado
                    log_msg = f"🤝 [CONSENSO] Sinal de {direction} aprovado em {self.symbol} com {num_votos} voto(s)!"
                    logger.info(log_msg)
                    
                    # Dispara UMA ÚNICA ORDEM para a corretora
                    await self.broker.place_order_and_monitor(
                        symbol=self.symbol, 
                        direction=direction, 
                        amount=self.trade_amount, 
                        duration=self.trade_duration, 
                        telegram_alert_cb=self.telegram_alert
                    )
                    
            except Exception as e:
                logger.error(f"[{self.name}] Erro no loop principal de consenso: {e}")