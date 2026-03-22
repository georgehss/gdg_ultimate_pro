import asyncio, logging, time
import pandas as pd
import pandas_ta as ta
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class RSIStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60, 
                 rsi_period=14, rsi_overbought=70, rsi_oversold=30, 
                 long_ma_period=100, ema_period=9,
                 entry_mode="CROSSBACK", confirm_candle=True):
        
        super().__init__(name=f"RSIPro_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.symbol = symbol
        self.timeframe = timeframe
        
        # Parâmetros Base RSI
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.entry_mode = entry_mode.upper() 
        self.confirm_candle = confirm_candle
        
        # Filtros Pro (Tendência e Volatilidade)
        self.long_ma_period = long_ma_period
        self.ema_period = ema_period
        
        self.trade_amount = 1.0
        self.trade_duration = "01:00"
        self.last_signal = None # Trava de repetição
        self.last_signal_time = None

    async def analyze_market(self):
        logger.info(f"[{self.name}] Analisando RSI Pro ({self.rsi_period}) + SMMA {self.long_ma_period} + Volatilidade...")
        
        limit_klines = max(150, self.long_ma_period + 50)
        klines = await self.broker.get_klines(symbol=self.symbol, interval="1m", limit=limit_klines)
        
        if not klines:
            return None
            
        try:
            df = pd.DataFrame(klines)
            df['openPrice'] = pd.to_numeric(df['openPrice'])
            df['closePrice'] = pd.to_numeric(df['closePrice'])
            df['time'] = pd.to_numeric(df['time'])
            
            # 1) Indicadores
            df['rsi'] = ta.rsi(df['closePrice'], length=self.rsi_period)
            df['ema'] = ta.ema(df['closePrice'], length=self.ema_period)
            df['smma_long'] = ta.rma(df['closePrice'], length=self.long_ma_period)
            
            # Volatilidade (tamanho do corpo da vela para evitar sinais fracos)
            df['body_size'] = abs(df['closePrice'] - df['openPrice'])
            df['avg_body'] = df['body_size'].rolling(window=10).mean()
            
            df.dropna(inplace=True)
            if len(df) < 3:
                return None

            idx1 = -2 # Vela recém-fechada
            idx2 = -3 # Vela anterior
            
            current_candle_time = df['time'].iloc[idx1]
            if self.last_signal_time == current_candle_time:
                return None
                
            # Dados Vela 1 (Atual)
            c1 = df['closePrice'].iloc[idx1]
            o1 = df['openPrice'].iloc[idx1]
            rsi1 = df['rsi'].iloc[idx1]
            ema1 = df['ema'].iloc[idx1]
            smma1 = df['smma_long'].iloc[idx1]
            body1 = df['body_size'].iloc[idx1]
            avg_body1 = df['avg_body'].iloc[idx2]
            
            # Dados Vela 2 (Anterior - Para CROSSBACK)
            rsi2 = df['rsi'].iloc[idx2]
            
            # 2) Reset Inteligente da Trava
            # Libera o bot para operar novamente apenas quando o RSI volta à zona "neutra" (40-60)
            if self.last_signal is not None:
                if 40 < rsi1 < 60:
                    self.last_signal = None
                    logger.debug(f"[{self.name}] RSI neutro ({rsi1:.1f}). Trava liberada.")
            
            if self.last_signal is not None:
                return None # Bloqueado até o RSI voltar ao normal

            # 3) Lógica do RSI Extremes
            rsi_buy_signal = False
            rsi_sell_signal = False
            
            if self.entry_mode == "CROSSBACK":
                # RSI foi abaixo de 30 na vela anterior e já voltou a subir acima de 30 nesta vela
                rsi_buy_signal = (rsi2 <= self.rsi_oversold) and (rsi1 > self.rsi_oversold)
                # RSI foi acima de 70 na vela anterior e já desceu abaixo de 70 nesta vela
                rsi_sell_signal = (rsi2 >= self.rsi_overbought) and (rsi1 < self.rsi_overbought)
            else: # TOUCH
                rsi_buy_signal = rsi1 <= self.rsi_oversold
                rsi_sell_signal = rsi1 >= self.rsi_overbought

            # 4) Confirmação da Vela (Color e Tamanho)
            candle_ok_buy = (c1 > o1) if self.confirm_candle else True
            candle_ok_sell = (c1 < o1) if self.confirm_candle else True
            
            # A vela de reversão não pode ser minúscula (precisa de pelo menos 70% da média recente)
            volatility_ok = body1 >= (avg_body1 * 0.7)

            # 5) Filtro Institucional (Pullbacks de Tendência)
            # A EMA atua como rastreador rápido de tendência para confirmar a SMMA
            trend_up = ema1 > smma1
            trend_down = ema1 < smma1

            # 6) Confluência de Ouro
            # Comprar apenas em macrotendência de alta, no recuo do RSI, com uma vela forte de ignição
            is_buy = rsi_buy_signal and candle_ok_buy and volatility_ok and trend_up
            is_sell = rsi_sell_signal and candle_ok_sell and volatility_ok and trend_down
            
            if is_buy:
                self.last_signal = "BUY"
                self.last_signal_time = current_candle_time
                log_msg = f"⚡ [PRO RSI] COMPRA em {self.symbol}! Retração a favor da tendência (EMA > SMMA) com RSI ({rsi1:.1f}) e vela forte confirmada."
                logger.info(log_msg)
                await self.broker.place_order_and_monitor(symbol=self.symbol, direction="BUY", amount=self.trade_amount, duration=self.trade_duration, telegram_alert_cb=self.telegram_alert)
                
            elif is_sell:
                self.last_signal = "SELL"
                self.last_signal_time = current_candle_time
                log_msg = f"⚡ [PRO RSI] VENDA em {self.symbol}! Retração a favor da tendência (EMA < SMMA) com RSI ({rsi1:.1f}) e vela forte confirmada."
                logger.info(log_msg)
                await self.broker.place_order_and_monitor(symbol=self.symbol, direction="SELL", amount=self.trade_amount, duration=self.trade_duration, telegram_alert_cb=self.telegram_alert)
                
        except Exception as e:
            logger.error(f"[{self.name}] Erro na estratégia RSI Pro: {e}")
            
        return None

    async def execute(self):
        self.is_running = True
        logger.info(f"[{self.name}] Estratégia RSI PRO iniciada com sincronização.")
        
        import time
        while self.is_running:
            try:
                agora = time.time()
                segundos_atuais = agora % 60
                espera = 60 - segundos_atuais
                await asyncio.sleep(espera + 1.0) 
                await self.analyze_market()
            except Exception as e:
                logger.error(f"[{self.name}] Erro no loop principal: {e}")