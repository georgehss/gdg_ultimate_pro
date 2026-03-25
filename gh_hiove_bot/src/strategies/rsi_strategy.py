import asyncio, logging, time
import pandas as pd
import pandas_ta as ta
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class RSIStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60, profile="Balanceado"):
        super().__init__(name=f"RSIPro_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.symbol = symbol
        self.timeframe = timeframe
        self.profile = profile
        
        # Aplica as configurações baseadas no perfil escolhido
        self._apply_profile_settings()
        
        self.trade_amount = 1.0
        self.trade_duration = "01:00"
        self.last_signal = None 
        self.last_signal_time = None

    def _apply_profile_settings(self):
        """Define os parâmetros internos com base no perfil escolhido."""
        if self.profile == "Conservador":
            self.rsi_period = 14
            self.rsi_overbought = 75
            self.rsi_oversold = 25
            self.long_ma_period = 200
            self.ema_period = 9
            self.entry_mode = "CROSSBACK"
            self.confirm_candle = True
        elif self.profile == "Agressivo":
            self.rsi_period = 9
            self.rsi_overbought = 65
            self.rsi_oversold = 35
            self.long_ma_period = 50
            self.ema_period = 5
            self.entry_mode = "TOUCH"
            self.confirm_candle = False
        else: # Balanceado ou Customizado (Valores Padrão)
            self.rsi_period = 14
            self.rsi_overbought = 70
            self.rsi_oversold = 30
            self.long_ma_period = 100
            self.ema_period = 9
            self.entry_mode = "CROSSBACK"
            self.confirm_candle = True

    async def analyze_market(self):
        # NOVO: Atualizado o log para refletir os novos indicadores
        logger.info(f"[{self.name}] Analisando RSI Pro + SMMA + Bandas de Bollinger + ADX + Volume...")
        
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
            
            # NOVO: Converte o Volume
            df['volume'] = pd.to_numeric(df['volume'])
            
            # 1) Indicadores Base
            df['rsi'] = ta.rsi(df['closePrice'], length=self.rsi_period)
            df['ema'] = ta.ema(df['closePrice'], length=self.ema_period)
            df['smma_long'] = ta.rma(df['closePrice'], length=self.long_ma_period)
            
            # NOVO: Bandas de Bollinger (Exaustão Extrema)
            bbands = ta.bbands(df['closePrice'], length=20, std=2.0)
            df['bb_lower'] = bbands.iloc[:, 0] # Banda Inferior
            df['bb_upper'] = bbands.iloc[:, 2] # Banda Superior
            
            # NOVO: ADX (Força da Tendência)
            adx_df = ta.adx(df['highPrice'], df['lowPrice'], df['closePrice'], length=14)
            df['adx'] = adx_df.iloc[:, 0]
            
            # Volatilidade (tamanho do corpo)
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
                
            # Dados Vela 1 (Atual / Confirmação)
            c1, o1 = df['closePrice'].iloc[idx1], df['openPrice'].iloc[idx1]
            h1, l1 = df['highPrice'].iloc[idx1], df['lowPrice'].iloc[idx1]
            rsi1 = df['rsi'].iloc[idx1]
            ema1 = df['ema'].iloc[idx1]
            smma1 = df['smma_long'].iloc[idx1]
            body1 = df['body_size'].iloc[idx1]
            avg_body1 = df['avg_body'].iloc[idx2]
            v1 = df['volume'].iloc[idx1]
            adx1 = df['adx'].iloc[idx1]
            
            # NOVO: Dados Bandas de Bollinger e Vol Vela Anterior
            bbl1, bbu1 = df['bb_lower'].iloc[idx1], df['bb_upper'].iloc[idx1]
            bbl2, bbu2 = df['bb_lower'].iloc[idx2], df['bb_upper'].iloc[idx2]
            l2, h2 = df['lowPrice'].iloc[idx2], df['highPrice'].iloc[idx2]
            rsi2 = df['rsi'].iloc[idx2]
            v2 = df['volume'].iloc[idx2]
            
            # 2) Reset Inteligente da Trava
            if self.last_signal is not None:
                if 40 < rsi1 < 60:
                    self.last_signal = None
                    logger.debug(f"[{self.name}] RSI neutro ({rsi1:.1f}). Trava liberada.")
            
            if self.last_signal is not None:
                return None

            # 3) Lógica do RSI Extremes
            rsi_buy_signal = False
            rsi_sell_signal = False
            
            if self.entry_mode == "CROSSBACK":
                rsi_buy_signal = (rsi2 <= self.rsi_oversold) and (rsi1 > self.rsi_oversold)
                rsi_sell_signal = (rsi2 >= self.rsi_overbought) and (rsi1 < self.rsi_overbought)
            else: # TOUCH
                rsi_buy_signal = rsi1 <= self.rsi_oversold
                rsi_sell_signal = rsi1 >= self.rsi_overbought

            # NOVO: 4) Validação Extrema (Bandas de Bollinger + Volume + ADX)
            # A vela atual ou a anterior tem de ter furado as Bandas para confirmar exaustão
            bb_ok_buy = (l1 <= bbl1) or (l2 <= bbl2)
            bb_ok_sell = (h1 >= bbu1) or (h2 >= bbu2)
            
            volume_ok = v1 > v2 # Confirma defesa institucional na vela de reversão
            trend_strength_ok = adx1 > 20 # Tem de haver tendência clara para o respiro voltar
            
            # 5) Confirmação da Vela e Volatilidade
            candle_ok_buy = (c1 > o1) if self.confirm_candle else True
            candle_ok_sell = (c1 < o1) if self.confirm_candle else True
            volatility_ok = body1 >= (avg_body1 * 0.7)

            # 6) Filtro Institucional (Macrotendência)
            trend_up = ema1 > smma1
            trend_down = ema1 < smma1

            # 7) Confluência de Ouro Suprema
            is_buy = rsi_buy_signal and candle_ok_buy and volatility_ok and trend_up and bb_ok_buy and volume_ok and trend_strength_ok
            is_sell = rsi_sell_signal and candle_ok_sell and volatility_ok and trend_down and bb_ok_sell and volume_ok and trend_strength_ok
            
            if is_buy:
                self.last_signal = "BUY"
                self.last_signal_time = current_candle_time
                log_msg = f"⚡ [PRO RSI] COMPRA EXTREMA em {self.symbol}! (RSI: {rsi1:.1f} | BB: OK | Vol: OK)"
                logger.info(log_msg)
                return "BUY", log_msg
                
            elif is_sell:
                self.last_signal = "SELL"
                self.last_signal_time = current_candle_time
                log_msg = f"⚡ [PRO RSI] VENDA EXTREMA em {self.symbol}! (RSI: {rsi1:.1f} | BB: OK | Vol: OK)"
                logger.info(log_msg)
                return "SELL", log_msg
                
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
                
                resultado = await self.analyze_market()
                if resultado:
                    direction, log_msg = resultado
                    await self.broker.place_order_and_monitor(symbol=self.symbol, direction=direction, amount=self.trade_amount, duration=self.trade_duration, telegram_alert_cb=self.telegram_alert)
                    
            except Exception as e:
                logger.error(f"[{self.name}] Erro no loop principal: {e}")