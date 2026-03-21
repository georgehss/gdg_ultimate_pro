import asyncio, logging, time
import pandas as pd
import pandas_ta as ta
import numpy as np
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class EngulfMAStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60, 
                 ma_period=8, long_ma_period=59, rsi_period=14, ma_entry_mode="BREAK", epsilon_price=0.0):
        
        super().__init__(name=f"PriceActionPro_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.symbol = symbol
        self.timeframe = timeframe
        
        # Parâmetros da Estratégia
        self.ma_period = ma_period
        self.long_ma_period = long_ma_period 
        self.rsi_period = rsi_period # Novo: Período do RSI
        self.ma_entry_mode = ma_entry_mode.upper() 
        self.epsilon = epsilon_price 
        
        self.trade_amount = 1.0
        self.trade_duration = "01:00"
        self.last_signal_time = None 

    async def analyze_market(self):
        logger.info(f"[{self.name}] Analisando Price Action Pro + EMA {self.ma_period} + SMMA {self.long_ma_period} + RSI...")
        
        limit_klines = max(150, self.long_ma_period + 50)
        klines = await self.broker.get_klines(symbol=self.symbol, interval="1m", limit=limit_klines)
        
        if not klines:
            return None
            
        try:
            df = pd.DataFrame(klines)
            df['openPrice'] = pd.to_numeric(df['openPrice'])
            df['closePrice'] = pd.to_numeric(df['closePrice'])
            df['highPrice'] = pd.to_numeric(df['highPrice'])
            df['lowPrice'] = pd.to_numeric(df['lowPrice'])
            df['time'] = pd.to_numeric(df['time'])
            
            # 1) Calcula os Indicadores
            df['ema'] = ta.ema(df['closePrice'], length=self.ma_period)
            df['smma_long'] = ta.rma(df['closePrice'], length=self.long_ma_period) 
            df['rsi'] = ta.rsi(df['closePrice'], length=self.rsi_period)
            
            # Tamanho da vela (High - Low) para medir a volatilidade média das últimas 10 velas
            df['candle_size'] = df['highPrice'] - df['lowPrice']
            df['avg_size'] = df['candle_size'].rolling(window=10).mean()
            
            df.dropna(inplace=True)
            if len(df) < 3:
                return None

            idx1 = -2 # Vela recém-fechada
            idx2 = -3 # Vela anterior
            
            current_candle_time = df['time'].iloc[idx1]
            if self.last_signal_time == current_candle_time:
                return None 
            
            # Dados Vela 1 (Sinal)
            o1, c1, h1, l1 = df['openPrice'].iloc[idx1], df['closePrice'].iloc[idx1], df['highPrice'].iloc[idx1], df['lowPrice'].iloc[idx1]
            ma1 = df['ema'].iloc[idx1]
            smma_long1 = df['smma_long'].iloc[idx1]
            rsi1 = df['rsi'].iloc[idx1]
            size1 = df['candle_size'].iloc[idx1]
            avg_size1 = df['avg_size'].iloc[idx2] # Média calculada até a vela anterior
            
            # Dados Vela 2 (Anterior)
            o2, c2, h2, l2 = df['openPrice'].iloc[idx2], df['closePrice'].iloc[idx2], df['highPrice'].iloc[idx2], df['lowPrice'].iloc[idx2]
            
            # 2) Leitura de Corpo e Pavios
            body1 = abs(c1 - o1)
            upper_wick1 = h1 - max(o1, c1)
            lower_wick1 = min(o1, c1) - l1
            
            d1 = c1 - o1
            d2 = c2 - o2
            
            bull1 = d1 > self.epsilon
            bear1 = d1 < -self.epsilon
            bull2 = d2 > self.epsilon
            bear2 = d2 < -self.epsilon
            
            # Filtro de Volatilidade: A vela de sinal não pode ser "morta" (deve ter pelo menos 80% do tamanho médio recente)
            volatility_ok = size1 >= (avg_size1 * 0.8)
            
            # 3) Lógica dos Padrões Gráficos
            bullish_engulf = bull1 and bear2 and (o1 <= c2 + self.epsilon) and (c1 >= o2 - self.epsilon)
            bearish_engulf = bear1 and bull2 and (o1 >= c2 - self.epsilon) and (c1 <= o2 + self.epsilon)
            
            is_hammer = bull1 and (lower_wick1 >= 2 * body1) and (upper_wick1 <= body1) and (body1 > self.epsilon)
            is_shooting_star = bear1 and (upper_wick1 >= 2 * body1) and (lower_wick1 <= body1) and (body1 > self.epsilon)

            bullish_pattern = "Engolfo de Alta" if bullish_engulf else ("Martelo" if is_hammer else None)
            bearish_pattern = "Engolfo de Baixa" if bearish_engulf else ("Estrela Cadente" if is_shooting_star else None)

            # 4) Alinhamento Duplo de Tendência (O segredo institucional)
            # A EMA Curta precisa concordar com a SMMA Longa
            trend_up = (c1 > smma_long1) and (ma1 > smma_long1)
            trend_down = (c1 < smma_long1) and (ma1 < smma_long1)

            # 5) Lógica da Média Curta (MA Entry Mode)
            ma_ok_buy = False
            ma_ok_sell = False
            
            if self.ma_entry_mode == "ABOVE_BELOW":
                ma_ok_buy = c1 > ma1 + self.epsilon
                ma_ok_sell = c1 < ma1 - self.epsilon
            else: 
                ma_ok_buy = (l1 <= ma1 + self.epsilon) and (c1 > ma1 + self.epsilon)
                ma_ok_sell = (h1 >= ma1 - self.epsilon) and (c1 < ma1 - self.epsilon)
                
            # 6) Filtro de Exaustão (RSI)
            # Compra: RSI não pode estar acima de 65 (espaço para subir)
            rsi_ok_buy = rsi1 < 65
            # Venda: RSI não pode estar abaixo de 35 (espaço para cair)
            rsi_ok_sell = rsi1 > 35
            
            # 7) Confluência de Sinais MÁXIMA
            is_buy = bullish_pattern and ma_ok_buy and trend_up and rsi_ok_buy and volatility_ok
            is_sell = bearish_pattern and ma_ok_sell and trend_down and rsi_ok_sell and volatility_ok
            
            if is_buy:
                self.last_signal_time = current_candle_time
                log_msg = f"🔥 [PRO] COMPRA: {bullish_pattern} validado em {self.symbol}! Rompeu EMA {self.ma_period}, EMA acima da SMMA {self.long_ma_period}, RSI = {rsi1:.1f} e boa Volatilidade."
                logger.info(log_msg)
                await self.telegram_alert(f"🟢 {log_msg}")
                await self.broker.place_order_and_monitor(symbol=self.symbol, direction="BUY", amount=self.trade_amount, duration=self.trade_duration, telegram_alert_cb=self.telegram_alert)
                
            elif is_sell:
                self.last_signal_time = current_candle_time
                log_msg = f"🔥 [PRO] VENDA: {bearish_pattern} validado em {self.symbol}! Rompeu EMA {self.ma_period}, EMA abaixo da SMMA {self.long_ma_period}, RSI = {rsi1:.1f} e boa Volatilidade."
                logger.info(log_msg)
                await self.telegram_alert(f"🔴 {log_msg}")
                await self.broker.place_order_and_monitor(symbol=self.symbol, direction="SELL", amount=self.trade_amount, duration=self.trade_duration, telegram_alert_cb=self.telegram_alert)
                
        except Exception as e:
            logger.error(f"[{self.name}] Erro na estratégia Engolfo MA Pro: {e}")
            
        return None

    async def execute(self):
        self.is_running = True
        logger.info(f"[{self.name}] Estratégia PRO iniciada.")
        
        while self.is_running:
            try:
                agora = time.time()
                segundos_atuais = agora % 60
                espera = 60 - segundos_atuais
                await asyncio.sleep(espera + 1.0) 
                await self.analyze_market()
                
            except Exception as e:
                logger.error(f"[{self.name}] Erro inesperado no loop principal: {e}")