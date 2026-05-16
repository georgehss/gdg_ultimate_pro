import asyncio, logging, time
import pandas as pd
import pandas_ta as ta
import numpy as np
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class EngulfMAStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60, profile="Balanceado", custom_params=None):
        super().__init__(name=f"PriceActionPro_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.symbol = symbol
        self.timeframe_str = timeframe # Guarda como string para a API (ex: '5m')
        
        # Converte para segundos para o relógio da estratégia
        if timeframe == "5m": self.timeframe_seconds = 300
        elif timeframe == "15m": self.timeframe_seconds = 900
        else: self.timeframe_seconds = 60 # Padrão 1 minuto
        
        self.profile = profile
        self.custom_params = custom_params
        
        # Aplica as configurações baseadas no perfil escolhido
        self._apply_profile_settings()
        
        self.trade_amount = 1.0
        self.trade_duration = "01:00"
        self.last_signal_time = None 

    def _apply_profile_settings(self):
        self.rsi_period = 14
        self.ma_entry_mode = "BREAK"
        
        if self.profile == "Conservador":
            self.short_ma_period = 8        # Período da SMMA curta (Aceleração do preço)
            self.medium_ma_period = 59      # Período da SMMA média (Tendência intermediária)
            self.long_ma_period = 200       # Período da SMMA longa (Macrotendência)
            self.epsilon = 0.0001           # Tolerância rigorosa (exige engolfo perfeito ou superior)
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.min_adx = 25               # Exige forte direcionalidade no movimento
            self.volatility_mult = 1.0      # Volatilidade da vela de sinal deve ser >= 100% da média
            self.rsi_pullback_buy = 60      # Trava a compra se o RSI já estiver acima de 60 (quase sobrecomprado)
            self.rsi_pullback_sell = 40     # Trava a venda se o RSI já estiver abaixo de 40 (quase sobrevendido)
            self.volume_mult = 1.1          # Exige que o volume da vela de sinal seja 10% MAIOR que a anterior
            
        elif self.profile == "Agressivo":
            self.short_ma_period = 8        # Período da SMMA curta
            self.medium_ma_period = 59      # Período da SMMA média
            self.long_ma_period = 200       # Período da SMMA longa
            self.epsilon = -0.0001          # Tolerância solta (aceita engolfos com pequenas sobras/imperfeitos)
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.min_adx = 10               # Opera mesmo em mercado quase lateral
            self.volatility_mult = 0.5      # Aceita padrões de reversão de corpo pequeno (50% da média)
            self.rsi_pullback_buy = 70      # Compra até encostar na zona de sobrecompra extrema
            self.rsi_pullback_sell = 30     # Vende até encostar na zona de sobrevenda extrema
            self.volume_mult = 0.5          # Aceita entrar com metade do volume da vela anterior

        elif self.profile == "Customizado" and self.custom_params:
            # Valores base seguros
            self.short_ma_period = 8        # Período da SMMA curta
            self.medium_ma_period = 59      # Período da SMMA média
            self.long_ma_period = 200       # Período da SMMA longa
            self.epsilon = 0.0              # Sem tolerância extra (Exige cobertura exata 1 para 1)
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.min_adx = 18               # Nível mínimo de força de tendência (ADX)
            self.volatility_mult = 0.8      # A vela deve ter no mínimo 80% do tamanho médio
            self.rsi_pullback_buy = 60      # Teto máximo do RSI para validar uma COMPRA
            self.rsi_pullback_sell = 40     # Piso mínimo do RSI para validar uma VENDA
            self.volume_mult = 1.0          # O volume de engolfo deve ser no mínimo igual ao volume engolfado

            try:
                # Divide a string e converte consoante o tipo de dado esperado
                valores = [v.strip() for v in self.custom_params.split(',')]
                if len(valores) >= 1: self.short_ma_period = int(valores[0])
                if len(valores) >= 2: self.medium_ma_period = int(valores[1])
                if len(valores) >= 3: self.long_ma_period = int(valores[2])
                if len(valores) >= 4: self.min_adx = int(valores[3])
                if len(valores) >= 5: self.volatility_mult = float(valores[4])
                if len(valores) >= 6: self.rsi_pullback_buy = int(valores[5])
                if len(valores) >= 7: self.rsi_pullback_sell = int(valores[6])
                if len(valores) >= 8: self.volume_mult = float(valores[7])
            except ValueError:
                pass # Em caso de erro de digitação, cai de pé nos valores base
            
        else: # Balanceado (Padrão)
            self.short_ma_period = 8        # SMMA curta padrão (Aceleração)
            self.medium_ma_period = 59      # SMMA média padrão (Direção intermediária)
            self.long_ma_period = 200       # SMMA longa padrão (Macro tendência institucional)
            self.epsilon = 0.0              # Encaixe matemático perfeito exigido para engolfo
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.min_adx = 20               # Filtra falsos rompimentos em consolidação estreita
            self.volatility_mult = 1.0      # Exige uma vela de sinal com volatilidade forte ou normal
            self.rsi_pullback_buy = 65      # Aceita compra com margem leve de respiro antes da sobrecompra
            self.rsi_pullback_sell = 35     # Aceita venda com margem leve de respiro antes da sobrevenda
            self.volume_mult = 1.0          # Valida que há capital suficiente empurrando a reversão

    async def analyze_market(self):
        
        logger.info(f"[{self.name}] Analisando Price Action Pro + SMMAs ({self.short_ma_period}/{self.medium_ma_period}/{self.long_ma_period}) + RSI + ADX + Volume...")
        
        limit_klines = max(150, self.long_ma_period + 50)
        klines = await self.broker.get_klines(symbol=self.symbol, interval=self.timeframe_str, limit=limit_klines)
        
        if not klines:
            return None
            
        try:
            df = pd.DataFrame(klines)
            df['openPrice'] = pd.to_numeric(df['openPrice'])
            df['closePrice'] = pd.to_numeric(df['closePrice'])
            df['highPrice'] = pd.to_numeric(df['highPrice'])
            df['lowPrice'] = pd.to_numeric(df['lowPrice'])
            df['time'] = pd.to_numeric(df['time'])
            
            # NOVO: 1. Converte a coluna de volume da corretora
            df['volume'] = pd.to_numeric(df['volume'])
            
            # 1) Cálculo das 3 SMMAs (ta.rma é o equivalente à SMMA no pandas_ta)
            df['smma_short'] = ta.rma(df['closePrice'], length=self.short_ma_period)
            df['smma_medium'] = ta.rma(df['closePrice'], length=self.medium_ma_period)
            df['smma_long'] = ta.rma(df['closePrice'], length=self.long_ma_period)
            df['rsi'] = ta.rsi(df['closePrice'], length=self.rsi_period)
            
            # NOVO: 2. Calcula o ADX para evitar lateralização
            adx_df = ta.adx(df['highPrice'], df['lowPrice'], df['closePrice'], length=14)
            df['adx'] = adx_df[adx_df.columns[0]]
            
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
            
            # Dados Vela 1 (Sinal / Recém Fechada)
            o1, c1, h1, l1 = df['openPrice'].iloc[idx1], df['closePrice'].iloc[idx1], df['highPrice'].iloc[idx1], df['lowPrice'].iloc[idx1]
            
            # NOVO: Puxa os dados das 3 SMMAs
            smma_short1 = df['smma_short'].iloc[idx1]
            smma_medium1 = df['smma_medium'].iloc[idx1]
            smma_long1 = df['smma_long'].iloc[idx1]
            
            rsi1 = df['rsi'].iloc[idx1]
            size1 = df['candle_size'].iloc[idx1]
            avg_size1 = df['avg_size'].iloc[idx2] 
            
            # 3. Pega o volume e o ADX da vela de sinal
            v1 = df['volume'].iloc[idx1]
            adx1 = df['adx'].iloc[idx1]
            
            # Dados Vela 2 (Anterior / Engolfada)
            o2, c2, h2, l2 = df['openPrice'].iloc[idx2], df['closePrice'].iloc[idx2], df['highPrice'].iloc[idx2], df['lowPrice'].iloc[idx2]
            v2 = df['volume'].iloc[idx2]
            
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
            
            # Filtro de Volatilidade e Institucionais
            volatility_ok = size1 >= (avg_size1 * self.volatility_mult)
            volume_ok = v1 > (v2 * self.volume_mult) 
            trend_strength_ok = adx1 > self.min_adx 
            
            # 3) Lógica dos Padrões Gráficos
            bullish_engulf = bull1 and bear2 and (o1 <= c2 + self.epsilon) and (c1 >= o2 - self.epsilon)
            bearish_engulf = bear1 and bull2 and (o1 >= c2 - self.epsilon) and (c1 <= o2 + self.epsilon)
            
            is_hammer = bull1 and (lower_wick1 >= 2 * body1) and (upper_wick1 <= body1) and (body1 > self.epsilon)
            is_shooting_star = bear1 and (upper_wick1 >= 2 * body1) and (lower_wick1 <= body1) and (body1 > self.epsilon)

            bullish_pattern = "Engolfo de Alta" if bullish_engulf else ("Martelo" if is_hammer else None)
            bearish_pattern = "Engolfo de Baixa" if bearish_engulf else ("Estrela Cadente" if is_shooting_star else None)

            # NOVO: 4) Alinhamento Triplo de Tendência (As 3 SMMAs alinhadas com o preço)
            trend_up = (c1 > smma_long1) and (smma_medium1 > smma_long1) and (smma_short1 > smma_medium1)
            trend_down = (c1 < smma_long1) and (smma_medium1 < smma_long1) and (smma_short1 < smma_medium1)

            # NOVO: 5) Lógica da Média Curta (Usa agora a SMMA Curta)
            ma_ok_buy = False
            ma_ok_sell = False
            
            if self.ma_entry_mode == "ABOVE_BELOW":
                ma_ok_buy = c1 > smma_short1 + self.epsilon
                ma_ok_sell = c1 < smma_short1 - self.epsilon
            else: 
                ma_ok_buy = (l1 <= smma_short1 + self.epsilon) and (c1 > smma_short1 + self.epsilon)
                ma_ok_sell = (h1 >= smma_short1 - self.epsilon) and (c1 < smma_short1 - self.epsilon)
                
            # 6) Filtro de Exaustão (RSI)
            rsi_ok_buy = rsi1 < self.rsi_pullback_buy
            rsi_ok_sell = rsi1 > self.rsi_pullback_sell
            
            # NOVO: 7) Confluência de Sinais MÁXIMA - Inclusão do volume_ok e trend_strength_ok
            is_buy = bullish_pattern and ma_ok_buy and trend_up and rsi_ok_buy and volatility_ok and volume_ok and trend_strength_ok
            is_sell = bearish_pattern and ma_ok_sell and trend_down and rsi_ok_sell and volatility_ok and volume_ok and trend_strength_ok
            
            if is_buy:
                self.last_signal_time = current_candle_time
                # NOVO: Adicionado um log detalhado para você acompanhar o ADX da entrada no Telegram/Console
                log_msg = f"🔥 [PRO] COMPRA: {bullish_pattern} validado em {self.symbol}! (Vol: OK, ADX: {adx1:.1f})"
                logger.info(log_msg)
                return "BUY", log_msg
                
            elif is_sell:
                self.last_signal_time = current_candle_time
                # NOVO: Adicionado um log detalhado para você acompanhar o ADX da entrada no Telegram/Console
                log_msg = f"🔥 [PRO] VENDA: {bearish_pattern} validado em {self.symbol}! (Vol: OK, ADX: {adx1:.1f})"
                logger.info(log_msg)
                return "SELL", log_msg
                
        except Exception as e:
            logger.error(f"[{self.name}] Erro na estratégia Engolfo MA Pro: {e}")
            
        return None

    async def execute(self):
        self.is_running = True
        logger.info(f"[{self.name}] Estratégia PRO iniciada.")
        
        while self.is_running:
            try:
                agora = time.time()
                segundos_atuais = agora % self.timeframe_seconds 
                espera = self.timeframe_seconds - segundos_atuais 
                await asyncio.sleep(espera + 1.0)
                
                # Executa a ordem se estiver a rodar sozinha
                resultado = await self.analyze_market()
                if resultado:
                    direction, log_msg = resultado
                    await self.broker.place_order_and_monitor(symbol=self.symbol, direction=direction, amount=self.trade_amount, duration=self.trade_duration, telegram_alert_cb=self.telegram_alert)
                
            except Exception as e:
                logger.error(f"[{self.name}] Erro inesperado no loop principal: {e}")