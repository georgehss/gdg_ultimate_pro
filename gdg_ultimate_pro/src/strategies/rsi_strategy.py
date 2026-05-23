import asyncio, logging, time
import pandas as pd
import pandas_ta as ta
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class RSIStrategy(BaseStrategy):
    def __init__(self, broker, telegram_alert_cb, symbol="ETHUSDT", timeframe=60, profile="Balanceado", custom_params=None, active_filters=None):
        super().__init__(name=f"RSILateral_PRO_{symbol}", broker=broker, telegram_alert_cb=telegram_alert_cb)
        self.active_filters = active_filters or {}
        self.symbol = symbol
        self.timeframe_str = timeframe 
        
        if timeframe == "5m": self.timeframe_seconds = 300          # 5 minutos
        elif timeframe == "15m": self.timeframe_seconds = 900       # 15 minutos
        else: self.timeframe_seconds = 60 
        
        self.profile = profile
        self.custom_params = custom_params
        
        self._apply_profile_settings()
        
        self.trade_amount = 1.0             # Valor fixo para opções binárias (pode ser ajustado conforme necessário)
        self.trade_duration = "01:00"       # Duração fixa de 1 minuto para opções binárias (pode ser ajustada conforme necessário)
        self.last_signal = None             # Para evitar múltiplos sinais na mesma vela
        self.last_signal_time = None        # Para evitar múltiplos sinais na mesma vela


    def _apply_profile_settings(self):
        """Parâmetros avançados para operar Lateralidade Extrema e Absorção."""
        if self.profile == "Conservador":
            self.rsi_period = 14            # Período padrão do RSI para detectar sobrecompra/sobrevenda
            self.max_adx = 20               # ADX baixo para confirmar lateralidade (pode ser desativado via filtros)
            self.confirm_candle = True      # Exige um padrão de vela de confirmação para aumentar a precisão
            self.volatility_mult = 1.0      # Multiplicador de volatilidade
            self.bb_std = 2.5               # Desvio padrão da banda de Bollinger
            self.min_box_pct = 0.004        # O caixote deve ter pelo menos 0.4% de amplitude
            self.stop_vol_mult = 1.3        # Exige volume 30% superior à média (Alta absorção)

        elif self.profile == "Agressivo":
            self.rsi_period = 7             # Período mais curto para o RSI, tornando-o mais sensível a mudanças rápidas
            self.max_adx = 30               # Permite um ADX um pouco mais alto, aceitando lateralidades com leve tendência
            self.confirm_candle = False     # Não exige padrão de vela de confirmação
            self.volatility_mult = 0.5      # Aceita velas de sinal com volatilidade mais baixa, tentando pegar movimentos mais sutis
            self.bb_std = 2.0               # Desvio padrão da banda de Bollinger
            self.min_box_pct = 0.002        # Aceita caixotes mais estreitos (0.2%)
            self.stop_vol_mult = 1.0        # Não exige volume superior à média, aceitando sinais mesmo em momentos de menor liquidez

        elif self.profile == "Customizado" and self.custom_params:
            self.rsi_period = 14            # Período do RSI (ex: 14 para o RSI tradicional, 7 para um RSI mais sensível)
            self.max_adx = 25               # ADX máximo para considerar o mercado como lateral (ex: 20 para lateralidade estrita, 30 para aceitar um pouco de tendência)
            self.volatility_mult = 0.8      # Multiplicador de volatilidade para validar a força da vela de sinal (ex: 1.0 para exigir uma vela tão volátil quanto a média, 0.5 para aceitar velas com metade da volatilidade média)
            self.bb_std = 2.0               # Desvio padrão para as Bandas de Bollinger usadas como filtro de lateralidade (ex: 2.0 para o padrão, 2.5 para exigir uma lateralidade mais clara)
            self.min_box_pct = 0.003        # Amplitude mínima do caixote em porcentagem (ex: 0.003 para exigir que o caixote tenha pelo menos 0.3% de amplitude, 0.001 para aceitar caixotes mais estreitos)
            self.stop_vol_mult = 1.0        # Multiplicador para o filtro de volume de paragem (ex: 1.0 para exigir que o volume da vela de sinal seja igual ou maior que a média, 0.8 para aceitar sinais mesmo com volume 20% abaixo da média)        
            self.confirm_candle = True      # Exige um padrão de vela de confirmação para aumentar a precisão (pode ser desativado via filtros)

            logger.info("⚙️ A carregar Perfil Customizado...")
            try:
                valores = [v.strip() for v in self.custom_params.split(',')]
                if len(valores) >= 1 and valores[0]: self.rsi_period = int(valores[0])
                if len(valores) >= 2 and valores[1]: self.max_adx = int(valores[1])
                if len(valores) >= 3 and valores[2]: self.volatility_mult = float(valores[2])
                if len(valores) >= 4 and valores[3]: self.bb_std = float(valores[3])
                if len(valores) >= 5 and valores[4]: self.min_box_pct = float(valores[4])
                if len(valores) >= 6 and valores[5]: self.stop_vol_mult = float(valores[5])
                logger.info("✅ Perfil Customizado verificado.")
            except (ValueError, IndexError) as e:
                logger.error(f"⚠️ Falha na conversão dos parâmetros ({e}). A aplicar fallbacks seguros.")
        else: # Balanceado (Padrão)
            self.rsi_period = 14            # Período tradicional do RSI, equilibrando sensibilidade e estabilidade  
            self.max_adx = 25               # ADX máximo para considerar o mercado como lateral (25 é um valor intermediário que aceita lateralidades com leve tendência, mas ainda filtra mercados claramente direcionais)
            self.confirm_candle = True      # Exige um padrão de vela de confirmação para aumentar a precisão dos sinais, especialmente útil em mercados laterais onde os falsos sinais são mais comuns
            self.volatility_mult = 0.8      # Exige que a vela de sinal tenha pelo menos 80% da volatilidade média recente, ajudando a filtrar sinais fracos e focar em movimentos mais significativos dentro da lateralidade
            self.bb_std = 2.0               # Desvio padrão para as Bandas de Bollinger usadas como filtro de lateralidade (2.0 é o valor tradicional, oferecendo um bom equilíbrio entre filtrar lateralidades muito estreitas e aceitar lateralidades com uma amplitude razoável)
            self.min_box_pct = 0.003        # Amplitude mínima do caixote: 0.3%
            self.stop_vol_mult = 1.2        # Volume deve ser 20% maior que a média recente

    async def analyze_market(self):
        logger.info(f"[{self.name}] A analisar RSI Dinâmico + Volume de Paragem + Divergências...")
        
        limit_klines = 150
        klines = await self.broker.get_klines(symbol=self.symbol, interval=self.timeframe_str, limit=limit_klines)
        
        if not klines:
            return None
            
        try:
            df = pd.DataFrame(klines)
            for col in ['openPrice', 'closePrice', 'highPrice', 'lowPrice', 'time', 'volume']:
                df[col] = pd.to_numeric(df[col])
            
            # 1) Indicadores Base
            df['rsi'] = ta.rsi(df['closePrice'], length=self.rsi_period)
            
            # NOVO: RSI Dinâmico (Bandas de Bollinger aplicadas ao próprio RSI)
            rsi_bb = ta.bbands(df['rsi'], length=20, std=2.0)
            df['rsi_bb_lower'] = rsi_bb.iloc[:, 0]
            df['rsi_bb_upper'] = rsi_bb.iloc[:, 2]

            # Bandas de Bollinger do Preço (Limites da lateralidade)
            bbands = ta.bbands(df['closePrice'], length=20, std=self.bb_std)
            df['bb_lower'] = bbands.iloc[:, 0]
            df['bb_middle'] = bbands.iloc[:, 1] 
            df['bb_upper'] = bbands.iloc[:, 2]
            df['bb_width'] = df['bb_upper'] - df['bb_lower']
            
            # ADX (Filtro de Tendência)
            adx_df = ta.adx(df['highPrice'], df['lowPrice'], df['closePrice'], length=14)
            df['adx'] = adx_df.iloc[:, 0]
            
            # Volatilidade e Volume Médio (Stopping Volume)
            df['body_size'] = abs(df['closePrice'] - df['openPrice'])
            df['avg_body'] = df['body_size'].rolling(window=10).mean()
            df['avg_vol_5'] = df['volume'].rolling(window=5).mean()

            # ATR para filtro de vela esticada
            df['atr'] = ta.atr(df['highPrice'], df['lowPrice'], df['closePrice'], length=14)
            
            df.dropna(inplace=True)
            if len(df) < 3:
                return None

            idx1 = -2 # Vela recém-fechada
            idx2 = -3 # Vela anterior
            
            current_candle_time = df['time'].iloc[idx1]
            if self.last_signal_time == current_candle_time:
                return None
                
            # Dados da Vela Atual
            c1, o1 = df['closePrice'].iloc[idx1], df['openPrice'].iloc[idx1]
            h1, l1 = df['highPrice'].iloc[idx1], df['lowPrice'].iloc[idx1]
            rsi1 = df['rsi'].iloc[idx1]
            body1 = df['body_size'].iloc[idx1]
            adx1 = df['adx'].iloc[idx1]
            bbl1, bbu1 = df['bb_lower'].iloc[idx1], df['bb_upper'].iloc[idx1]
            bb_width1 = df['bb_width'].iloc[idx1]
            v1 = df['volume'].iloc[idx1]
            
            rsi_bbl1 = df['rsi_bb_lower'].iloc[idx1]
            rsi_bbu1 = df['rsi_bb_upper'].iloc[idx1]

            # Dados da Vela Anterior
            c2, o2 = df['closePrice'].iloc[idx2], df['openPrice'].iloc[idx2]
            l2, h2 = df['lowPrice'].iloc[idx2], df['highPrice'].iloc[idx2]
            rsi2 = df['rsi'].iloc[idx2]
            bbl2, bbu2 = df['bb_lower'].iloc[idx2], df['bb_upper'].iloc[idx2]
            
            rsi_bbl2 = df['rsi_bb_lower'].iloc[idx2]
            rsi_bbu2 = df['rsi_bb_upper'].iloc[idx2]
            avg_body1 = df['avg_body'].iloc[idx2]
            avg_vol_5 = df['avg_vol_5'].iloc[idx2]

            # 2) Reset da Trava Inteligente
            if self.last_signal is not None:
                if 45 < rsi1 < 55:
                    self.last_signal = None
                    logger.debug(f"[{self.name}] RSI retornou ao centro ({rsi1:.1f}). Trava libertada.")
            
            if self.last_signal is not None:
                return None

            # 3) O Filtro de Lateralidade (ADX Baixo)
            use_adx = self.active_filters.get("adx", True)
            market_is_ranging = (adx1 < self.max_adx) if use_adx else True

            # 4) NOVO: Filtro de Caixote Mínimo
            box_size_pct = bb_width1 / c1
            box_is_large_enough = box_size_pct >= self.min_box_pct

            # 5) NOVO: Volume de Paragem (Stopping Volume)
            use_volume = self.active_filters.get("volume", True)
            stopping_volume_ok = (v1 >= (avg_vol_5 * self.stop_vol_mult)) if use_volume else True

            # 6) NOVO: Lógica RSI Dinâmico e Divergência nas Bordas
            # RSI Dinâmico: Cruzou a própria banda do RSI de volta para dentro
            rsi_dyn_buy = (rsi2 <= rsi_bbl2) and (rsi1 > rsi_bbl1)
            rsi_dyn_sell = (rsi2 >= rsi_bbu2) and (rsi1 < rsi_bbu1)

            # Divergência (Spring / Falso Rompimento)
            bullish_div = (l1 < l2) and (rsi1 > rsi2) and (rsi1 < 45)
            bearish_div = (h1 > h2) and (rsi1 < rsi2) and (rsi1 > 55)

            rsi_buy_signal = rsi_dyn_buy or bullish_div
            rsi_sell_signal = rsi_dyn_sell or bearish_div

            # 7) Padrões Gráficos de Exaustão (Price Action)
            upper_wick = h1 - max(o1, c1)
            lower_wick = min(o1, c1) - l1
            
            is_hammer = (lower_wick > (body1 * 1.5)) and (upper_wick < body1)
            is_shooting_star = (upper_wick > (body1 * 1.5)) and (lower_wick < body1)
            
            engulf_bullish = (c1 > o1) and (o1 <= c2) and (c1 >= o2) and (c2 < o2)
            engulf_bearish = (c1 < o1) and (o1 >= c2) and (c1 <= o2) and (c2 > o2)

            candle_ok_buy = (is_hammer or engulf_bullish) if self.confirm_candle else True
            candle_ok_sell = (is_shooting_star or engulf_bearish) if self.confirm_candle else True

            # 8) Toque Físico nas Bandas do Preço
            use_bb = self.active_filters.get("bb", True)
            bb_touch_buy = ((l1 <= bbl1) or (l2 <= bbl2)) if use_bb else True
            bb_touch_sell = ((h1 >= bbu1) or (h2 >= bbu2)) if use_bb else True
            
            volatility_ok = body1 >= (avg_body1 * self.volatility_mult)

            # --- FILTROS ESPECÍFICOS DE OPÇÕES BINÁRIAS ---
            atr1 = df['atr'].iloc[idx1] if 'atr' in df else body1
            tamanho_total1 = h1 - l1
            vela_esticada = tamanho_total1 > (atr1 * 2.0)
            
            is_doji = body1 <= (tamanho_total1 * 0.15) if tamanho_total1 > 0 else True
            
            pavio_superior1 = h1 - max(c1, o1)
            pavio_inferior1 = min(c1, o1) - l1
            rejeicao_alta = pavio_superior1 > body1  
            rejeicao_baixa = pavio_inferior1 > body1 
            
            c3, o3 = df['closePrice'].iloc[-4], df['openPrice'].iloc[-4]
            c4, o4 = df['closePrice'].iloc[-5], df['openPrice'].iloc[-5]
            exaustao_compra = (c1 > o1) and (c2 > o2) and (c3 > o3) and (c4 > o4)
            exaustao_venda = (c1 < o1) and (c2 < o2) and (c3 < o3) and (c4 < o4)
            
            if is_doji or vela_esticada or exaustao_compra or rejeicao_alta:
                candle_ok_buy = False
            if is_doji or vela_esticada or exaustao_venda or rejeicao_baixa:
                candle_ok_sell = False
            # ----------------------------------------------

            # 9) Validação Final Institucional
            is_buy = rsi_buy_signal and candle_ok_buy and volatility_ok and market_is_ranging and bb_touch_buy and box_is_large_enough and stopping_volume_ok
            is_sell = rsi_sell_signal and candle_ok_sell and volatility_ok and market_is_ranging and bb_touch_sell and box_is_large_enough and stopping_volume_ok
            
            if is_buy:
                self.last_signal = "BUY"
                self.last_signal_time = current_candle_time
                motivo = "Divergência" if bullish_div else "RSI Dinâmico"
                log_msg = f"⚡ [RSI LATERAL PRO] COMPRA ({motivo}) em {self.symbol}! | Vol Paragem: OK | Caixote: {box_size_pct*100:.2f}%"
                logger.info(log_msg)
                return "BUY", log_msg
                
            elif is_sell:
                self.last_signal = "SELL"
                self.last_signal_time = current_candle_time
                motivo = "Divergência" if bearish_div else "RSI Dinâmico"
                log_msg = f"⚡ [RSI LATERAL PRO] VENDA ({motivo}) em {self.symbol}! | Vol Paragem: OK | Caixote: {box_size_pct*100:.2f}%"
                logger.info(log_msg)
                return "SELL", log_msg
                
        except Exception as e:
            logger.error(f"[{self.name}] Erro na estratégia RSI Lateral: {e}")
            
        return None

    async def execute(self):
        self.is_running = True
        logger.info(f"[{self.name}] Estratégia RSI Lateral iniciada com sincronização.")
        
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