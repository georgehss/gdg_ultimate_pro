import asyncio, logging, time
import pandas as pd
import pandas_ta as ta
import numpy as np
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class EngulfMAStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60, profile="Balanceado", custom_params=None, active_filters=None):
        super().__init__(name=f"PriceActionPro_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.active_filters = active_filters or {}
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
            
            # Converte a coluna de volume da corretora
            df['volume'] = pd.to_numeric(df['volume'])
            
            # Cálculo das 3 SMMAs (ta.rma é o equivalente à SMMA no pandas_ta)
            df['smma_short'] = ta.rma(df['closePrice'], length=self.short_ma_period)
            df['smma_medium'] = ta.rma(df['closePrice'], length=self.medium_ma_period)
            df['smma_long'] = ta.rma(df['closePrice'], length=self.long_ma_period)
            df['rsi'] = ta.rsi(df['closePrice'], length=self.rsi_period)
            
            # Calcula o ADX para evitar lateralização
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
            
            # Puxa os dados das 3 SMMAs
            smma_short1 = df['smma_short'].iloc[idx1]
            smma_medium1 = df['smma_medium'].iloc[idx1]
            smma_long1 = df['smma_long'].iloc[idx1]
            
            rsi1 = df['rsi'].iloc[idx1]
            size1 = df['candle_size'].iloc[idx1]
            avg_size1 = df['avg_size'].iloc[idx2] 
            
            # Pega o volume e o ADX da vela de sinal
            v1 = df['volume'].iloc[idx1]
            adx1 = df['adx'].iloc[idx1]
            
            # Dados Vela 2 (Anterior / Engolfada)
            o2, c2, h2, l2 = df['openPrice'].iloc[idx2], df['closePrice'].iloc[idx2], df['highPrice'].iloc[idx2], df['lowPrice'].iloc[idx2]
            v2 = df['volume'].iloc[idx2]
            
            # Leitura de Corpo e Pavios
            body1 = abs(c1 - o1)
            upper_wick1 = h1 - max(o1, c1)
            lower_wick1 = min(o1, c1) - l1
            
            d1 = c1 - o1
            d2 = c2 - o2
            
            bull1 = d1 > self.epsilon
            bear1 = d1 < -self.epsilon
            bull2 = d2 > self.epsilon
            bear2 = d2 < -self.epsilon
            
            use_volume = self.active_filters.get("volume", True)
            use_adx = self.active_filters.get("adx", True)
            use_trend = self.active_filters.get("trend", True)

            # Filtro de Volatilidade e Institucionais
            volatility_ok = size1 >= (avg_size1 * self.volatility_mult)
            volume_ok = (v1 > (v2 * self.volume_mult)) if use_volume else True
            trend_strength_ok = (adx1 > self.min_adx) if use_adx else True
            
            # ----------------------------------------------------
            # Lógica dos Padrões Gráficos (Price Action Pro Expandido)
            # ----------------------------------------------------
            
            # Necessário para os novos cálculos:
            body2 = abs(c2 - o2)
            midpoint_2 = (o2 + c2) / 2
            
            # Padrões Originais
            bullish_engulf = bull1 and bear2 and (o1 <= c2 + self.epsilon) and (c1 >= o2 - self.epsilon)
            bearish_engulf = bear1 and bull2 and (o1 >= c2 - self.epsilon) and (c1 <= o2 + self.epsilon)
            
            # Martelo: O pavio inferior representa pelo menos 60% do tamanho total da vela, 
            # e o pavio superior não passa de 20% da vela. (O corpo pode ser pequeno).
            is_hammer = (lower_wick1 >= size1 * 0.6) and (upper_wick1 <= size1 * 0.2) and (body1 > self.epsilon)
            
            # Estrela Cadente: O pavio superior representa pelo menos 60% do tamanho da vela,
            # e o pavio inferior não passa de 20%.
            is_shooting_star = (upper_wick1 >= size1 * 0.6) and (lower_wick1 <= size1 * 0.2) and (body1 > self.epsilon)

            # 1: Marubozu (Vela de Força - Corpo compõe mais de 90% da vela)
            is_marubozu_bull = bull1 and (body1 >= size1 * 0.9) and (body1 > self.epsilon)
            is_marubozu_bear = bear1 and (body1 >= size1 * 0.9) and (body1 > self.epsilon)

            # 2: Harami (Inside Bar - Corpo da vela 1 totalmente dentro da vela 2)
            is_harami_bull = bear2 and bull1 and (o1 >= c2) and (c1 <= o2) and (body1 < body2)
            is_harami_bear = bull2 and bear1 and (o1 <= c2) and (c1 >= o2) and (body1 < body2)

            # 3: Piercing Line (Alta) e Dark Cloud Cover (Baixa)
            # A vela 1 rompe a mínima/máxima anterior, mas fecha cobrindo mais de 50% da vela 2
            is_piercing_line = bear2 and bull1 and (o1 <= c2) and (c1 >= midpoint_2) and (c1 <= o2)
            is_dark_cloud = bull2 and bear1 and (o1 >= c2) and (c1 <= midpoint_2) and (c1 >= o2)

            # 4: Fundo e Topo em Pinça (Tweezer)
            # As mínimas (para compra) ou máximas (para venda) das duas velas são quase idênticas
            # Usa uma margem de tolerância de 10% do tamanho da vela para absorver o ruído do mercado
            is_tweezer_bottom = bear2 and bull1 and (abs(l1 - l2) <= (avg_size1 * 0.1)) and (body1 > self.epsilon)
            is_tweezer_top = bull2 and bear1 and (abs(h1 - h2) <= (avg_size1 * 0.1)) and (body1 > self.epsilon)

            # 5: Linha de Cinturão (Belt Hold)
            # Vela de força que não deixa pavio contra o movimento (Abertura = Mínima/Máxima)
            is_belt_hold_bull = bear2 and bull1 and (lower_wick1 <= self.epsilon) and (body1 >= avg_size1 * 0.8)
            is_belt_hold_bear = bull2 and bear1 and (upper_wick1 <= self.epsilon) and (body1 >= avg_size1 * 0.8)

            # Definindo o Padrão de Alta Identificado
            if bullish_engulf:
                bullish_pattern = "Engolfo de Alta"
            elif is_hammer:
                bullish_pattern = "Martelo (Pin Bar)"
            elif is_marubozu_bull:
                bullish_pattern = "Marubozu de Alta"
            elif is_belt_hold_bull:
                bullish_pattern = "Cinturão de Alta"
            elif is_harami_bull:
                bullish_pattern = "Harami de Alta"
            elif is_piercing_line:
                bullish_pattern = "Linha de Perfuração"
            elif is_tweezer_bottom:
                bullish_pattern = "Fundo em Pinça"
            else:
                bullish_pattern = None

            # Definindo o Padrão de Baixa Identificado
            if bearish_engulf:
                bearish_pattern = "Engolfo de Baixa"
            elif is_shooting_star:
                bearish_pattern = "Estrela Cadente (Pin Bar)"
            elif is_marubozu_bear:
                bearish_pattern = "Marubozu de Baixa"
            elif is_belt_hold_bear:
                bearish_pattern = "Cinturão de Baixa"
            elif is_harami_bear:
                bearish_pattern = "Harami de Baixa"
            elif is_dark_cloud:
                bearish_pattern = "Nuvem Negra"
            elif is_tweezer_top:
                bearish_pattern = "Topo em Pinça"
            else:
                bearish_pattern = None
        

            # Alinhamento Triplo de Tendência (As 3 SMMAs alinhadas com o preço)
            trend_up = ((c1 > smma_long1) and (smma_medium1 > smma_long1) and (smma_short1 > smma_medium1)) if use_trend else True
            trend_down = ((c1 < smma_long1) and (smma_medium1 < smma_long1) and (smma_short1 < smma_medium1)) if use_trend else True

            # Lógica da Média Curta (Usa agora a SMMA Curta)
            ma_ok_buy = False
            ma_ok_sell = False
            
            if self.ma_entry_mode == "ABOVE_BELOW":
                ma_ok_buy = c1 > smma_short1 + self.epsilon
                ma_ok_sell = c1 < smma_short1 - self.epsilon
            else: 
                ma_ok_buy = (l1 <= smma_short1 + self.epsilon) and (c1 > smma_short1 + self.epsilon)
                ma_ok_sell = (h1 >= smma_short1 - self.epsilon) and (c1 < smma_short1 - self.epsilon)
                
            # Filtro de Exaustão (RSI)
            rsi_ok_buy = rsi1 < self.rsi_pullback_buy
            rsi_ok_sell = rsi1 > self.rsi_pullback_sell
            
            # ----------------------------------------------------
            # Confluência de Sinais MÁXIMA (Inteligente)
            # ----------------------------------------------------
            
            # Padrões de "Explosão" (precisam de muito volume e volatilidade confirmando o rompimento)
            padroes_explosao_alta = ["Engolfo de Alta", "Marubozu de Alta", "Cinturão de Alta", "Linha de Perfuração"]
            padroes_explosao_baixa = ["Engolfo de Baixa", "Marubozu de Baixa", "Cinturão de Baixa", "Nuvem Negra"]
            
            # Adapta os filtros dependendo da assinatura do padrão de Price Action
            if bullish_pattern in padroes_explosao_alta:
                vol_buy_ok = volatility_ok
                volm_buy_ok = volume_ok
            else:
                # Se for Harami, Martelo ou Pinça (padrões de absorção/descanso), relaxamos a exigência de tamanho e volume
                vol_buy_ok = True
                volm_buy_ok = True
                
            if bearish_pattern in padroes_explosao_baixa:
                vol_sell_ok = volatility_ok
                volm_sell_ok = volume_ok
            else:
                vol_sell_ok = True
                volm_sell_ok = True

            # Validação Final da Entrada
            is_buy = bullish_pattern and ma_ok_buy and trend_up and rsi_ok_buy and vol_buy_ok and volm_buy_ok and trend_strength_ok
            is_sell = bearish_pattern and ma_ok_sell and trend_down and rsi_ok_sell and vol_sell_ok and volm_sell_ok and trend_strength_ok
            #----------------------------------------------------------------------------------------------
            
            if is_buy:
                self.last_signal_time = current_candle_time
                # Adicionado um log detalhado para você acompanhar o ADX da entrada no Telegram/Console
                log_msg = f"🔥 [PRO] COMPRA: {bullish_pattern} validado em {self.symbol}! (Vol: OK, ADX: {adx1:.1f})"
                logger.info(log_msg)
                return "BUY", log_msg
                
            elif is_sell:
                self.last_signal_time = current_candle_time
                # Adicionado um log detalhado para você acompanhar o ADX da entrada no Telegram/Console
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