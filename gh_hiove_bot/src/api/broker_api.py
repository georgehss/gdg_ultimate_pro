import logging, asyncio, aiohttp, os, signal
from core.database import log_trade, update_trade_result, get_daily_profit

logger = logging.getLogger(__name__)

class HioveBrokerAPI:
    def __init__(self, scraper=None, user_config=None):
        self.scraper = scraper
        self.user_config = user_config or {} # Guarda as opções do Telegram
        self.session = None
        # NOVAS VARIÁVEIS DE CONTROLE
        self.active_monitors = 0        # Conta quantas ordens estão abertas
        self.stop_triggered = False     # Sinaliza se bateu a meta/loss
        self.limit_reached_msg = None   # Guarda a mensagem de Stop Loss/Take Profit

    async def init_session(self):
        self.session = aiohttp.ClientSession()
        logger.info("Sessão HTTP do Broker iniciada (Conexão com Binance).")

    async def close(self):
        if self.session:
            await self.session.close()
            logger.info("Sessão HTTP do Broker encerrada.")

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 50):
        # ... (Mantém-se igualzinho ao original)
        endpoint = "https://api.binance.com/api/v3/klines"
        binance_symbol = symbol.replace("/", "").replace(".", "").upper()
        params = {"symbol": binance_symbol, "interval": interval, "limit": limit}
        try:
            async with self.session.get(endpoint, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                klines = []
                for candle in data:
                    klines.append({
                        "time": candle[0], "openPrice": float(candle[1]),
                        "highPrice": float(candle[2]), "lowPrice": float(candle[3]),
                        "closePrice": float(candle[4]), "volume": float(candle[5])
                    })
                return klines
        except Exception as e:
            logger.error(f"Erro ao buscar histórico (klines) na Binance para {symbol}: {e}")
            return None

    # ========================================================
    # NOVA FUNÇÃO UNIVERSAL DE ORDEM E MONITORAMENTO
    # ========================================================
    async def place_order_and_monitor(self, symbol: str, direction: str, amount: float, duration: str, telegram_alert_cb):
        # 1. Impede novas ordens se o bot já decidiu desligar
        if self.stop_triggered:
            return None
        
        # Lê os limites configurados pelo utilizador
        tp = self.user_config.get("take_profit", 50.0)
        sl = self.user_config.get("stop_loss", -20.0)
        
        lucro_diario = await get_daily_profit()
        
        # 2. SEÇÃO DE RISCO ATUALIZADA
        if lucro_diario >= tp or lucro_diario <= sl:
            if not self.stop_triggered:
                self.stop_triggered = True # Levanta a bandeira de parada
                if lucro_diario >= tp:
                    msg = f"🏆 *META ATINGIDA (TAKE PROFIT)!*\nLucro hoje: ${lucro_diario:.2f}\nO bot foi desligado pois o seu objetivo diário foi concluído."
                else:
                    msg = f"🛑 *STOP LOSS ATINGIDO!*\nPrejuízo hoje: ${lucro_diario:.2f}\nO bot foi interrompido e desligado para proteger o seu capital."
                
                # Se não tem mais nenhuma ordem aberta, desliga agora. Se tem, apenas guarda a mensagem.
                if self.active_monitors == 0:
                    logger.warning(msg.replace('*', '').replace('\n', ' | '))
                    await telegram_alert_cb(msg)
                    os.kill(os.getpid(), signal.SIGINT)
                else:
                    self.limit_reached_msg = msg
            return None 

        logger.info(f"⚡ Disparando ordem de {direction} via Estratégia...")
        resultado = await self.scraper.place_order(symbol, direction)
        
        if resultado and "id" in resultado:
            order_id = resultado["id"]
            await log_trade(symbol, direction, amount, duration, "ABERTA", order_id)
            
            minutos, segundos = map(int, duration.split(':'))
            tempo_total_segundos = (minutos * 60) + segundos
            
            logger.info(f"⏳ Ordem colocada. A aguardar {tempo_total_segundos + 3}s pelo fecho da vela...")
            
            # 3. INCREMENTA O CONTADOR AQUI, antes de iniciar o monitor
            self.active_monitors += 1
            asyncio.create_task(self._monitor_task(symbol, order_id, tempo_total_segundos, telegram_alert_cb, amount, direction, duration))
        else:
            await log_trade(symbol, direction, amount, duration, "FALHOU", None)
            msg_erro = f"❌ *Erro ao executar a ordem em {symbol}!*"
            logger.error(msg_erro.replace('*', ''))
            await telegram_alert_cb(msg_erro)

    async def _monitor_task(self, symbol, order_id, tempo_espera, telegram_alert_cb, amount: float, direction: str, duration: str):
        try:
            """Espera o tempo da vela, lê o histórico e calcula o lucro líquido real"""
            await asyncio.sleep(tempo_espera + 3) # Espera a vela terminar + 3 segs de margem
            
            status, lucro_bruto = await self.scraper.check_trade_result(symbol)
            
            # ==========================================
            # 1. CONVERSÃO PARA LUCRO LÍQUIDO
            # ==========================================
            if status == "WIN":
                lucro_liquido = lucro_bruto - amount
            elif status == "EMPATE":
                lucro_liquido = 0.0
            else:
                lucro_liquido = lucro_bruto 
                
            await update_trade_result(order_id, status, lucro_liquido)
            
            # ==========================================
            # 2. BUSCAR BALANÇO E LUCRO ACUMULADO
            # ==========================================
            # Passamos o symbol para ler o saldo da aba correta e atualizada
            saldo_atual = await self.scraper.get_balance(symbol)
            lucro_acumulado = await get_daily_profit()
            
            # ==========================================
            # 3. FORMATAR E ENVIAR MENSAGEM
            # ==========================================
            if status == "WIN": emoji = "🟢 WIN"
            elif status == "LOSS": emoji = "🔴 LOSS"
            elif status == "EMPATE": emoji = "⚪ EMPATE"
            else: emoji = "⚠️ AVISO"

            # Formatação de ícones para a direção (opcional, mas fica bem visual)
            dir_icon = "📈 COMPRA (BUY)" if direction.upper() == "BUY" else "📉 VENDA (SELL)"

            msg = (
                f"*Resultado da Operação!*\n"
                f"{emoji}\n"
                f"▫️ Ativo: {symbol}\n"
                f"▫️ Direção: {dir_icon}\n"
                f"▫️ Tempo: {duration}\n"
                f"▫️ Valor Ordem: ${amount:.2f}\n"
                f"▫️ Resultado: ${lucro_liquido:.2f}\n\n"
                f"💰 *Balanço da Conta:* ${saldo_atual:.2f}\n"
                f"📊 *Lucro Acumulado:* ${lucro_acumulado:.2f}"
            )
            await telegram_alert_cb(msg)
            logger.info(msg.replace('\n', ' | ').replace('*', ''))

            # ==========================================
            # 4. VERIFICAÇÃO IMEDIATA (STOP LOSS / TAKE PROFIT)
            # ==========================================
            tp = self.user_config.get("take_profit", 50.0)
            sl = self.user_config.get("stop_loss", -20.0)
        
            # Se bateu o limite E a bandeira de desligar ainda não foi ativada
            if (lucro_acumulado >= tp or lucro_acumulado <= sl) and not self.stop_triggered:
                self.stop_triggered = True
                if lucro_acumulado >= tp:
                    self.limit_reached_msg = f"🏆 *META ATINGIDA (TAKE PROFIT)!*\nLucro hoje: ${lucro_acumulado:.2f}\nO bot atingiu o alvo diário e será desligado agora."
                else:
                    self.limit_reached_msg = f"🛑 *STOP LOSS ATINGIDO!*\nPrejuízo hoje: ${lucro_acumulado:.2f}\nO limite de perda foi atingido. O bot será desligado para proteger o seu capital."
                    
        finally: 
            # Avisa que esta operação terminou
            self.active_monitors -= 1
            
            # Se esta era a última operação rodando E o bot bateu a meta/loss, envia a mensagem UMA vez e desliga
            if self.active_monitors == 0 and self.stop_triggered and self.limit_reached_msg:
                logger.warning(self.limit_reached_msg.replace('*', '').replace('\n', ' | '))
                await telegram_alert_cb(self.limit_reached_msg)
                os.kill(os.getpid(), signal.SIGINT)