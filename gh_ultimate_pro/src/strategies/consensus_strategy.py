import asyncio, logging, time
from .base_strategy import BaseStrategy
from .rsi_strategy import RSIStrategy
from .ma_cross_strategy import MACrossStrategy
from .engulf_ma_strategy import EngulfMAStrategy

logger = logging.getLogger(__name__)

class ConsensusStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe="1m", min_votes_required=1, profile="Balanceado", active_strategies=None, custom_params=None):
        super().__init__(name=f"Consensus_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.symbol = symbol
        self.timeframe_str = timeframe
        
        # Se nenhuma estratégia for passada, usa as 3 por padrão
        if active_strategies is None:
            active_strategies = ["rsi", "ma", "engulf"]
        self.active_strategies = active_strategies
        
        # Trava de segurança: impede que a exigência de votos seja maior que o número de estratégias ativas
        if min_votes_required > len(self.active_strategies):
            logger.warning(f"[{self.name}] min_votes_required ({min_votes_required}) é maior que as estratégias ativas ({len(self.active_strategies)}). Ajustando para {len(self.active_strategies)}.")
            self.min_votes_required = len(self.active_strategies)
        else:
            self.min_votes_required = min_votes_required 
        
        if timeframe == "5m": self.timeframe_seconds = 300
        elif timeframe == "15m": self.timeframe_seconds = 900
        else: self.timeframe_seconds = 60
        
        self.strategies = []
        self.strat_names = []
        
        async def dummy_alert(msg): pass
        
        # Instancia dinamicamente apenas as estratégias selecionadas
        if "rsi" in self.active_strategies:
            strat = RSIStrategy(broker, dummy_alert, symbol, timeframe, profile=profile, custom_params=custom_params)
            self.strategies.append(strat)
            self.strat_names.append("RSI Pro")
            
        if "ma" in self.active_strategies:
            strat = MACrossStrategy(broker, dummy_alert, symbol, timeframe, profile=profile, custom_params=custom_params)
            self.strategies.append(strat)
            self.strat_names.append("MA Cross Pro")
            
        if "engulf" in self.active_strategies:
            strat = EngulfMAStrategy(broker, dummy_alert, symbol, timeframe, profile=profile, custom_params=custom_params)
            self.strategies.append(strat)
            self.strat_names.append("Price Action Pro")
        
        self.trade_amount = 1.0
        self.trade_duration = "01:00"

    async def analyze_market(self):
        if not self.strategies:
            logger.error(f"[{self.name}] Nenhuma estratégia foi selecionada para o consenso!")
            return None

        # Executa as análises dinamicamente apenas para as estratégias instanciadas
        tasks = [strat.analyze_market() for strat in self.strategies]
        results = await asyncio.gather(*tasks)

        votes_buy = []
        votes_sell = []

        # Verifica quem votou no quê
        for i, res in enumerate(results):
            if res:
                direction, msg = res
                if direction == "BUY": 
                    votes_buy.append(self.strat_names[i])
                elif direction == "SELL": 
                    votes_sell.append(self.strat_names[i])

        num_buy = len(votes_buy)
        num_sell = len(votes_sell)

        # Avaliação Inteligente de Conflito Global
        if num_buy > 0 and num_sell > 0:
            logger.warning(f"[{self.name}] ⚠️ CONFLITO GLOBAL: {votes_buy} mandaram Comprar, mas {votes_sell} mandaram Vender. Operação abortada por segurança.")
            return None
            
        # Aprovação de Compra com Relatório
        if num_buy >= self.min_votes_required:
            aprovadores = ", ".join(votes_buy)
            return "BUY", num_buy, aprovadores
            
        # Aprovação de Venda com Relatório
        elif num_sell >= self.min_votes_required:
            aprovadores = ", ".join(votes_sell)
            return "SELL", num_sell, aprovadores
            
        return None

    async def execute(self):
        self.is_running = True
        modo_str = f"Hub Sniper ({self.min_votes_required}+ Votos sem conflito)"
        logger.info(f"[{self.name}] 🤝 Orquestrador Inteligente iniciado. Estratégias ativas: {self.active_strategies} | Modo: {modo_str}")
        
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
                    
                    # Mensagem profissional formatada para o Telegram
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