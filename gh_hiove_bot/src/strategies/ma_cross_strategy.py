import asyncio, logging, time
import pandas as pd
import pandas_ta as ta
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class MACrossStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60, profile="Balanceado"):
        super().__init__(name=f"MACrossPro_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.symbol = symbol
        self.timeframe_str = timeframe # Guarda como string para a API (ex: '5m')
        
        # Converte para segundos para o relógio da estratégia
        if timeframe == "5m": self.timeframe_seconds = 300
        elif timeframe == "15m": self.timeframe_seconds = 900
        else: self.timeframe_seconds = 60 # Padrão 1 minuto
        
        self.profile = profile
        
        # Aplica as configurações baseadas no perfil escolhido
        self._apply_profile_settings()
        
        # Variáveis de Estado
        self.bars_since_signal = self.cooldown_bars 
        self.trade_amount = 1.0
        self.trade_duration = "01:00"

    def _apply_profile_settings(self):
        # Filtros de Período Básicos
        self.rsi_period = 14
        self.atr_period = 14
        self.slope_lookback = 3

        if self.profile == "Conservador":
            self.fast_period = 14
            self.slow_period = 50
            self.long_ma_period = 200
            self.cooldown_bars = 5
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.use_slope = True           # Exige que a EMA lenta esteja apontada a favor
            self.use_atr_sep = True         # Exige que as EMAs se afastem de verdade
            self.atr_sep_mult = 0.20        # Separação grande
            self.min_adx = 25               # Tendência consolidada
            self.volume_mult = 1.1          # Volume de cruzamento tem de ser 10% maior que a média
            self.rsi_max_buy = 55           # Muito rigor: recusa comprar num topo (RSI < 55)
            self.rsi_min_sell = 45          # Muito rigor: recusa vender num fundo (RSI > 45)

        elif self.profile == "Agressivo":
            self.fast_period = 5
            self.slow_period = 13
            self.long_ma_period = 50
            self.cooldown_bars = 1
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.use_slope = False          # Ignora a inclinação (tenta apanhar reversões abruptas)
            self.use_atr_sep = False        # Ignora a separação perfeita (basta tocar)
            self.atr_sep_mult = 0.05        # (Não será usado se use_atr_sep for False)
            self.min_adx = 15               # Aceita cruzamentos em mercados menos direcionais
            self.volume_mult = 0.8          # Aceita entrar mesmo com volume 20% abaixo da média
            self.rsi_max_buy = 75           # Aceita comprar até à boca da zona de sobrecompra
            self.rsi_min_sell = 25          # Aceita vender mesmo bem perto da sobrevenda

        else: # Balanceado (Padrão Original)
            self.fast_period = 9
            self.slow_period = 21
            self.long_ma_period = 100
            self.cooldown_bars = 3
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.use_slope = True
            self.use_atr_sep = True
            self.atr_sep_mult = 0.15
            self.min_adx = 20               # Padrão
            self.volume_mult = 1.0          # Padrão (Volume tem de passar a SMA20)
            self.rsi_max_buy = 65           # Padrão
            self.rsi_min_sell = 35          # Padrão

    async def analyze_market(self):
        # NOVO: Atualizado o log
        logger.info(f"[{self.name}] A processar MA Cross Pro (EMA {self.fast_period}/{self.slow_period}) + SMMA + RSI + ADX + Volume...")
        
        limit_klines = max(150, self.long_ma_period + 50)
        klines = await self.broker.get_klines(symbol=self.symbol, interval=self.timeframe_str, limit=limit_klines)
        
        if not klines:
            logger.warning(f"[{self.name}] Sem dados da API. A aguardar próximo ciclo.")
            return None
            
        try:
            df = pd.DataFrame(klines)
            df['openPrice'] = pd.to_numeric(df['openPrice'])
            df['closePrice'] = pd.to_numeric(df['closePrice'])
            df['highPrice'] = pd.to_numeric(df['highPrice'])
            df['lowPrice'] = pd.to_numeric(df['lowPrice'])
            
            # NOVO: Converte o Volume
            df['volume'] = pd.to_numeric(df['volume'])
            
            # 1. Calcula as EMAs, SMMA e RSI
            df['ema_fast'] = ta.ema(df['closePrice'], length=self.fast_period)
            df['ema_slow'] = ta.ema(df['closePrice'], length=self.slow_period)
            df['smma_long'] = ta.rma(df['closePrice'], length=self.long_ma_period) 
            df['rsi'] = ta.rsi(df['closePrice'], length=self.rsi_period) 
            
            # NOVO: Calcula o ADX e a Média de Volume (SMA 20)
            adx_df = ta.adx(df['highPrice'], df['lowPrice'], df['closePrice'], length=14)
            df['adx'] = adx_df[adx_df.columns[0]]
            df['vol_sma'] = ta.sma(df['volume'], length=20)
            
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
            
            # Dados dos Novos Filtros (SMMA, RSI, Volume, ADX)
            smma1 = df['smma_long'].iloc[last_closed]
            rsi1 = df['rsi'].iloc[last_closed]
            c1 = df['closePrice'].iloc[last_closed]
            o1 = df['openPrice'].iloc[last_closed]
            
            # NOVO: Pega o ADX, o Volume da vela atual e a Média de Volume
            adx1 = df['adx'].iloc[last_closed]
            v1 = df['volume'].iloc[last_closed]
            vol_sma1 = df['vol_sma'].iloc[last_closed]
            
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
            
            # NOVO: Filtro de Ignição (Volume e Ação de Preço) Dinâmico
            candle_ok_buy = (c1 > o1)
            candle_ok_sell = (c1 < o1)
            volume_ok = v1 > (vol_sma1 * self.volume_mult) # DINÂMICO
            
            if cross_up and not (candle_ok_buy and volume_ok):
                cross_up = False
                logger.debug(f"[{self.name}] BUY bloqueado: Falta de volume de ignição ou vela contrária.")
                
            if cross_down and not (candle_ok_sell and volume_ok):
                cross_down = False
                logger.debug(f"[{self.name}] SELL bloqueado: Falta de volume de ignição ou vela contrária.")

            # NOVO: Filtro de ADX (Força da Tendência) Dinâmico
            trend_strength_ok = adx1 > self.min_adx # DINÂMICO
            
            if cross_up and not trend_strength_ok:
                cross_up = False
                logger.debug(f"[{self.name}] BUY bloqueado: ADX fraco ({adx1:.1f}) indicando lateralização.")
                
            if cross_down and not trend_strength_ok:
                cross_down = False
                logger.debug(f"[{self.name}] SELL bloqueado: ADX fraco ({adx1:.1f}) indicando lateralização.")

            # Filtro 3: Alinhamento de Macrotendência (SMMA)
            trend_up = (c1 > smma1) and (f1 > smma1) and (s1 > smma1)
            trend_down = (c1 < smma1) and (f1 < smma1) and (s1 < smma1)
            
            if cross_up and not trend_up:
                cross_up = False
                
            if cross_down and not trend_down:
                cross_down = False

            # Filtro 4: Exaustão (RSI) Dinâmico
            rsi_ok_buy = rsi1 < self.rsi_max_buy   # DINÂMICO
            rsi_ok_sell = rsi1 > self.rsi_min_sell # DINÂMICO
            
            if cross_up and not rsi_ok_buy:
                cross_up = False
                
            if cross_down and not rsi_ok_sell:
                cross_down = False

            # Filtro 5: Cooldown (Anti-Chop)
            if self.bars_since_signal < self.cooldown_bars:
                cross_up = False
                cross_down = False

            self.bars_since_signal += 1

            # --- Execução de Ordens ---
            if cross_up:
                self.bars_since_signal = 0 
                # NOVO: Log melhorado para mostrar os dados de confirmação
                log_msg = f"🚀 [PRO MA CROSS] COMPRA (EMA {self.fast_period}/{self.slow_period}) em {self.symbol}! (Vol: OK, ADX: {adx1:.1f})"
                logger.info(log_msg)
                return "BUY", log_msg

            elif cross_down:
                self.bars_since_signal = 0 
                # NOVO: Log melhorado para mostrar os dados de confirmação
                log_msg = f"🚀 [PRO MA CROSS] VENDA (EMA {self.fast_period}/{self.slow_period}) em {self.symbol}! (Vol: OK, ADX: {adx1:.1f})"
                logger.info(log_msg)
                return "SELL", log_msg
                
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
                segundos_atuais = agora % self.timeframe_seconds
                espera = self.timeframe_seconds - segundos_atuais 
                await asyncio.sleep(espera + 1.0)
                
                resultado = await self.analyze_market()
                if resultado:
                    direction, log_msg = resultado
                    await self.broker.place_order_and_monitor(symbol=self.symbol, direction=direction, amount=self.trade_amount, duration=self.trade_duration, telegram_alert_cb=self.telegram_alert)
                
            except Exception as e:
                logger.error(f"[{self.name}] Erro inesperado no loop principal: {e}")