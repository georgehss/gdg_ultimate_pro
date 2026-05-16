import asyncio, logging, time
import pandas as pd
import pandas_ta as ta
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class RSIStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60, profile="Balanceado", custom_params=None):
        super().__init__(name=f"RSIPro_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
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
        self.last_signal = None 
        self.last_signal_time = None

    def _apply_profile_settings(self):
        """Define os parâmetros internos com base no perfil escolhido."""
        if self.profile == "Conservador":
            # Parâmetros de Período Básicos
            self.rsi_period = 14            # Período clássico do RSI para leitura sólida de exaustão
            self.rsi_overbought = 75        # Nível rigoroso de sobrecompra
            self.rsi_oversold = 25          # Nível rigoroso de sobrevenda
            self.long_ma_period = 200       # SMMA ultra-longa para garantir a proteção da macrotendência
            self.ema_period = 9             # EMA rápida para confirmar a direção do micro-movimento
            self.entry_mode = "CROSSBACK"   # Mais seguro: aguarda o RSI cruzar de volta para dentro da zona extrema
            self.confirm_candle = True      # Exige que a vela de entrada confirme a reversão (cor a favor da ordem)
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.min_adx = 22               # Tendência forte obrigatória para autorizar a operação
            self.volatility_mult = 1.0      # A vela tem de ter 100% ou mais da volatilidade média recente
            self.volume_mult = 1.1          # O volume de exaustão tem de ser 10% maior que a vela anterior
            self.bb_std = 2.5               # Exige que o preço perfure Bandas de Bollinger extremamente largas

        elif self.profile == "Agressivo":
            # Parâmetros de Período Básicos
            self.rsi_period = 9             # RSI mais rápido e sensível aos movimentos curtos do mercado
            self.rsi_overbought = 70        # Nível de sobrecompra mais acessível (toca mais vezes)
            self.rsi_oversold = 30          # Nível de sobrevenda mais acessível (toca mais vezes)
            self.long_ma_period = 50        # SMMA mais curta para apanhar microtendências
            self.ema_period = 5             # EMA ultra-rápida para gatilhos antecipados
            self.entry_mode = "TOUCH"       # Risco maior: entra imediatamente ao tocar na linha do RSI (não espera voltar)
            self.confirm_candle = False     # Não exige mudança de cor da vela (entrada o mais rápida possível)
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.min_adx = 12               # Aceita operar mesmo se o mercado estiver mais morno/lateral
            self.volatility_mult = 0.4      # Aceita velas de reversão pequenas (apenas 40% do tamanho da média)
            self.volume_mult = 0.6          # Aceita entrar mesmo se o volume for 40% menor que o anterior
            self.bb_std = 1.8               # Bandas de Bollinger mais estreitas (são perfuradas mais facilmente)

        elif self.profile == "Customizado" and self.custom_params:
            # Valores base seguros (Caso o utilizador não preencha tudo)
            self.rsi_period = 14            # Período de cálculo do RSI
            self.rsi_overbought = 70        # Linha superior (Sobrecompra)
            self.rsi_oversold = 30          # Linha inferior (Sobrevenda)
            self.long_ma_period = 100       # Período da SMMA para filtro de macrotendência
            self.ema_period = 9             # Período da EMA curta para direção do micro-movimento
            self.entry_mode = "CROSSBACK"   # 'TOUCH' (Tocou, entra) ou 'CROSSBACK' (Espera cruzar de volta)
            self.confirm_candle = True      # Exige que a cor da vela confirme a direção da operação
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.min_adx = 20               # Exige uma tendência direcional moderada/forte ativa
            self.volatility_mult = 0.7      # A vela deve ter no mínimo 70% da volatilidade média recente
            self.volume_mult = 1.0          # O volume da reversão deve ser pelo menos igual ao da vela anterior
            self.bb_std = 2.0               # Multiplicador de desvio padrão das Bandas de Bollinger

            try:
                valores = [v.strip() for v in self.custom_params.split(',')]
                if len(valores) >= 1: self.rsi_period = int(valores[0])
                if len(valores) >= 2: self.rsi_overbought = int(valores[1])
                if len(valores) >= 3: self.rsi_oversold = int(valores[2])
                if len(valores) >= 4: self.long_ma_period = int(valores[3])
                if len(valores) >= 5: self.ema_period = int(valores[4])
                if len(valores) >= 6: self.min_adx = int(valores[5])
                if len(valores) >= 7: self.volatility_mult = float(valores[6])
                if len(valores) >= 8: self.volume_mult = float(valores[7])
                if len(valores) >= 9: self.bb_std = float(valores[8])
            except ValueError:
                pass

        else: # Balanceado (Padrão Original)
            # Parâmetros de Período Básicos
            self.rsi_period = 14            # Período de cálculo do RSI padrão
            self.rsi_overbought = 70        # Linha superior clássica (Sobrecompra)
            self.rsi_oversold = 30          # Linha inferior clássica (Sobrevenda)
            self.long_ma_period = 100       # SMMA de 100 períodos para definir a tendência principal
            self.ema_period = 9             # EMA rápida para confirmar movimento a favor do trade
            self.entry_mode = "CROSSBACK"   # Entrada mais segura aguardando o retorno do cruzamento
            self.confirm_candle = True      # Exige cor da vela alinhada com a entrada (Verde p/ Compra, Vermelha p/ Venda)
            
            # FILTROS INSTITUCIONAIS DINÂMICOS
            self.min_adx = 18               # Exige mercado a sair de consolidação (leve tendência)
            self.volatility_mult = 0.5      # Aceita velas com metade do corpo da média recente
            self.volume_mult = 0.8          # Permite entrar se o volume for até 20% menor que o anterior
            self.bb_std = 2.0               # Desvio padrão clássico das Bandas de Bollinger

    async def analyze_market(self):
        # NOVO: Atualizado o log para refletir os novos indicadores
        logger.info(f"[{self.name}] Analisando RSI Pro + SMMA + Bandas de Bollinger + ADX + Volume...")
        
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
            
            # NOVO: Converte o Volume
            df['volume'] = pd.to_numeric(df['volume'])
            
            # 1) Indicadores Base
            df['rsi'] = ta.rsi(df['closePrice'], length=self.rsi_period)
            df['ema'] = ta.ema(df['closePrice'], length=self.ema_period)
            df['smma_long'] = ta.rma(df['closePrice'], length=self.long_ma_period)
            
            # NOVO: Bandas de Bollinger Dinâmicas
            bbands = ta.bbands(df['closePrice'], length=20, std=self.bb_std)
            df['bb_lower'] = bbands.iloc[:, 0]
            df['bb_upper'] = bbands.iloc[:, 2]
            
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
            volatility_ok = body1 >= (avg_body1 * self.volatility_mult) # DINÂMICO

            # Filtros Extras Dinâmicos
            volume_ok = v1 > (v2 * self.volume_mult) # DINÂMICO
            trend_strength_ok = adx1 > self.min_adx  # DINÂMICO

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
                segundos_atuais = agora % self.timeframe_seconds 
                espera = self.timeframe_seconds - segundos_atuais 
                await asyncio.sleep(espera + 1.0)
                
                resultado = await self.analyze_market()
                if resultado:
                    direction, log_msg = resultado
                    await self.broker.place_order_and_monitor(symbol=self.symbol, direction=direction, amount=self.trade_amount, duration=self.trade_duration, telegram_alert_cb=self.telegram_alert)
                    
            except Exception as e:
                logger.error(f"[{self.name}] Erro no loop principal: {e}")