import asyncio, logging, time
import pandas as pd
import pandas_ta as ta
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class MACrossStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60, 
                 fast_period=9, slow_period=21, long_ma_period=100, rsi_period=14,
                 use_slope=True, slope_lookback=3,
                 use_atr_sep=True, atr_period=14, atr_sep_mult=0.15,
                 cooldown_bars=3):
        
        super().__init__(name=f"MACrossPro_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.symbol = symbol
        self.timeframe = timeframe  
        
        # Parâmetros das Médias e RSI
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.long_ma_period = long_ma_period # Média longa para macrotendência
        self.rsi_period = rsi_period # Período do RSI para exaustão
        
        # Filtros de Ruído
        self.use_slope = use_slope
        self.slope_lookback = slope_lookback
        self.use_atr_sep = use_atr_sep
        self.atr_period = atr_period
        self.atr_sep_mult = atr_sep_mult
        self.cooldown_bars = cooldown_bars
        
        # Variáveis de Estado
        self.bars_since_signal = cooldown_bars 
        self.trade_amount = 1.0
        self.trade_duration = "01:00"

    async def analyze_market(self):
        logger.info(f"[{self.name}] A processar MA Cross Pro (EMA {self.fast_period}/{self.slow_period}) + SMMA {self.long_ma_period} + RSI...")
        
        # Garante histórico suficiente para a Média Longa
        limit_klines = max(150, self.long_ma_period + 50)
        klines = await self.broker.get_klines(symbol=self.symbol, interval="1m", limit=limit_klines)
        
        if not klines:
            logger.warning(f"[{self.name}] Sem dados da API. A aguardar próximo ciclo.")
            return None
            
        try:
            df = pd.DataFrame(klines)
            df['closePrice'] = pd.to_numeric(df['closePrice'])
            df['highPrice'] = pd.to_numeric(df['highPrice'])
            df['lowPrice'] = pd.to_numeric(df['lowPrice'])
            
            # 1. Calcula as EMAs, SMMA e RSI
            df['ema_fast'] = ta.ema(df['closePrice'], length=self.fast_period)
            df['ema_slow'] = ta.ema(df['closePrice'], length=self.slow_period)
            df['smma_long'] = ta.rma(df['closePrice'], length=self.long_ma_period) # SMMA Longa
            df['rsi'] = ta.rsi(df['closePrice'], length=self.rsi_period) # RSI
            
            # 2. Calcula o ATR
            if self.use_atr_sep:
                df['atr'] = ta.atr(df['highPrice'], df['lowPrice'], df['closePrice'], length=self.atr_period)
            
            df.dropna(inplace=True)
            
            if len(df) < self.slope_lookback + 2:
                return None

            last_closed = -2
            prev_closed = -3
            
            # Dados das Médias Curtas
            f1 = df['ema_fast'].iloc[last_closed]
            f2 = df['ema_fast'].iloc[prev_closed]
            s1 = df['ema_slow'].iloc[last_closed]
            s2 = df['ema_slow'].iloc[prev_closed]
            
            # Dados dos Novos Filtros (SMMA e RSI)
            smma1 = df['smma_long'].iloc[last_closed]
            rsi1 = df['rsi'].iloc[last_closed]
            c1 = df['closePrice'].iloc[last_closed]
            
            # Lógica Base de Cruzamento
            cross_up = (f2 <= s2) and (f1 > s1)
            cross_down = (f2 >= s2) and (f1 < s1)

            # Filtro 1: Separação por ATR
            if (cross_up or cross_down) and self.use_atr_sep:
                atr_val = df['atr'].iloc[last_closed]
                separation = abs(f1 - s1)
                min_sep = self.atr_sep_mult * atr_val
                if separation < min_sep:
                    cross_up = False
                    cross_down = False
                    logger.debug(f"[{self.name}] Bloqueado por falta de separação ATR.")

            # Filtro 2: Confirmação por Inclinação (Slope)
            if (cross_up or cross_down) and self.use_slope:
                s_past = df['ema_slow'].iloc[last_closed - self.slope_lookback]
                slope = s1 - s_past
                
                if cross_up and slope <= 0:
                    cross_up = False
                    logger.debug(f"[{self.name}] BUY bloqueado: MA Lenta sem inclinação positiva.")
                if cross_down and slope >= 0:
                    cross_down = False
                    logger.debug(f"[{self.name}] SELL bloqueado: MA Lenta sem inclinação negativa.")

            # --- NOVOS FILTROS INSTITUCIONAIS ---
            
            # Filtro 3: Alinhamento de Macrotendência (SMMA)
            # Para comprar, o preço e as médias de sinal têm de estar acima da SMMA Longa
            trend_up = (c1 > smma1) and (f1 > smma1) and (s1 > smma1)
            # Para vender, o preço e as médias de sinal têm de estar abaixo da SMMA Longa
            trend_down = (c1 < smma1) and (f1 < smma1) and (s1 < smma1)
            
            if cross_up and not trend_up:
                cross_up = False
                logger.debug(f"[{self.name}] BUY bloqueado: Contra a Macrotendência (SMMA).")
                
            if cross_down and not trend_down:
                cross_down = False
                logger.debug(f"[{self.name}] SELL bloqueado: Contra a Macrotendência (SMMA).")

            # Filtro 4: Exaustão (RSI)
            rsi_ok_buy = rsi1 < 65
            rsi_ok_sell = rsi1 > 35
            
            if cross_up and not rsi_ok_buy:
                cross_up = False
                logger.debug(f"[{self.name}] BUY bloqueado: Mercado sobrecomprado (RSI = {rsi1:.1f}).")
                
            if cross_down and not rsi_ok_sell:
                cross_down = False
                logger.debug(f"[{self.name}] SELL bloqueado: Mercado sobrevendido (RSI = {rsi1:.1f}).")

            # Filtro 5: Cooldown (Anti-Chop)
            if self.bars_since_signal < self.cooldown_bars:
                cross_up = False
                cross_down = False

            self.bars_since_signal += 1

            # --- Execução de Ordens ---
            if cross_up:
                self.bars_since_signal = 0 
                log_msg = f"🚀 [PRO MA CROSS] COMPRA (EMA {self.fast_period}/{self.slow_period}) em {self.symbol}! Tendência validada pela SMMA {self.long_ma_period} e RSI saudável ({rsi1:.1f})."
                logger.info(log_msg)
                await self.telegram_alert(f"🟢 {log_msg}")
                
                await self.broker.place_order_and_monitor(
                    symbol=self.symbol, direction="BUY", 
                    amount=self.trade_amount, duration=self.trade_duration, 
                    telegram_alert_cb=self.telegram_alert
                )

            elif cross_down:
                self.bars_since_signal = 0 
                log_msg = f"🚀 [PRO MA CROSS] VENDA (EMA {self.fast_period}/{self.slow_period}) em {self.symbol}! Tendência validada pela SMMA {self.long_ma_period} e RSI saudável ({rsi1:.1f})."
                logger.info(log_msg)
                await self.telegram_alert(f"🔴 {log_msg}")
                
                await self.broker.place_order_and_monitor(
                    symbol=self.symbol, direction="SELL", 
                    amount=self.trade_amount, duration=self.trade_duration,
                    telegram_alert_cb=self.telegram_alert
                )
                
        except Exception as e:
            logger.error(f"[{self.name}] Erro no processamento do MA Cross: {e}")
            
        return None

    async def execute(self):
        self.is_running = True
        logger.info(f"[{self.name}] Estratégia PRO MA Cross iniciada com sincronização.")
        
        import time 
        
        while self.is_running:
            try:
                agora = time.time()
                segundos_atuais = agora % 60
                espera = 60 - segundos_atuais
                await asyncio.sleep(espera + 1.0) 
                await self.analyze_market()
                
            except Exception as e:
                logger.error(f"[{self.name}] Erro inesperado no loop principal: {e}")