import asyncio, logging, time
from .base_strategy import BaseStrategy
from .rsi_strategy import RSIStrategy
from .ma_cross_strategy import MACrossStrategy
from .engulf_ma_strategy import EngulfMAStrategy

logger = logging.getLogger(__name__)

class ConsensusStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe="1m", min_votes_required=1, profile="Balanceado"):
        super().__init__(name=f"Consensus_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.symbol = symbol
        self.timeframe_str = timeframe
        
        if timeframe == "5m": self.timeframe_seconds = 300
        elif timeframe == "15m": self.timeframe_seconds = 900
        else: self.timeframe_seconds = 60
        self.min_votes_required = min_votes_required 
        
        # Instancia as 3 estratégias silenciosamente repassando o perfil escolhido!
        self.strat_rsi = RSIStrategy(broker, telegram_alert_cb, symbol, timeframe, profile=profile)
        self.strat_ma = MACrossStrategy(broker, telegram_alert_cb, symbol, timeframe, profile=profile)
        self.strat_engulf = EngulfMAStrategy(broker, telegram_alert_cb, symbol, timeframe, profile=profile)
        
        async def dummy_alert(msg): pass
        self.strat_rsi.telegram_alert = dummy_alert
        self.strat_ma.telegram_alert = dummy_alert
        self.strat_engulf.telegram_alert = dummy_alert
        
        self.trade_amount = 1.0
        self.trade_duration = "01:00"

    async def analyze_market(self):
        # Executa as 3 análises de forma assíncrona ao mesmo tempo
        results = await asyncio.gather(
            self.strat_rsi.analyze_market(),
            self.strat_ma.analyze_market(),
            self.strat_engulf.analyze_market()
        )

        votes_buy = []
        votes_sell = []

        # Nomes das estratégias para o relatório
        strat_names = ["RSI Pro", "MA Cross Pro", "Price Action Pro"]

        # Verifica quem votou no quê
        for i, res in enumerate(results):
            if res:
                direction, msg = res
                if direction == "BUY": 
                    votes_buy.append(strat_names[i])
                elif direction == "SELL": 
                    votes_sell.append(strat_names[i])

        num_buy = len(votes_buy)
        num_sell = len(votes_sell)

        # NOVO: Avaliação Inteligente de Conflito Global
        if num_buy > 0 and num_sell > 0:
            logger.warning(f"[{self.name}] ⚠️ CONFLITO GLOBAL: {votes_buy} mandaram Comprar, mas {votes_sell} mandaram Vender. Operação abortada por segurança.")
            return None
            
        # NOVO: Aprovação de Compra com Relatório
        if num_buy >= self.min_votes_required:
            aprovadores = ", ".join(votes_buy)
            return "BUY", num_buy, aprovadores
            
        # NOVO: Aprovação de Venda com Relatório
        elif num_sell >= self.min_votes_required:
            aprovadores = ", ".join(votes_sell)
            return "SELL", num_sell, aprovadores
            
        return None

    async def execute(self):
        self.is_running = True
        modo_str = "Hub Sniper (1+ Votos sem conflito)" if self.min_votes_required == 1 else "Conservador Extremo (2+ Votos)"
        logger.info(f"[{self.name}] 🤝 Orquestrador Inteligente iniciado. Modo: {modo_str}")
        
        import time
        while self.is_running:
            try:
                agora = time.time()
                segundos_atuais = agora % self.timeframe_seconds
                espera = self.timeframe_seconds - segundos_atuais
                await asyncio.sleep(espera + 1.0)
                
                # Pergunta ao coordenador qual foi a decisão
                resultado = await self.analyze_market()
                
                if resultado:
                    direction, num_votos, aprovadores = resultado
                    
                    # NOVO: Mensagem profissional formatada para o Telegram
                    emoji = "🟢 WIN" if direction == "BUY" else "🔴 LOSS" # Aproveitando o estilo que já usas
                    dir_icon = "🈯️ COMPRA" if direction == "BUY" else "🈲 VENDA"
                    
                    log_msg = (
                        f"🤝 *CONSENSO APROVADO!*\n"
                        f"▫️ Ativo: {self.symbol}\n"
                        f"▫️ Direção: {dir_icon}\n"
                        f"▫️ Votos a favor: {num_votos}\n"
                        f"▫️ Aprovado por: {aprovadores}"
                    )
                    
                    logger.info(log_msg.replace('\n', ' | ').replace('*', ''))
                    
                    # O Orquestrador envia o alerta principal
                    await self.telegram_alert(log_msg)
                    
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