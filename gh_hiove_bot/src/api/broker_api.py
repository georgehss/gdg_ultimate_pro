import logging, asyncio, aiohttp, os, signal
from datetime import datetime
from core.database import log_trade, update_trade_result, get_session_profit

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
        self.limit_reached_cb = None    # Função a ser chamada quando o limite for alcançado
        # ESTRUTURAS DO MARTINGALE HÍBRIDO
        self.martingale_queue = []      # Fila para o modo "Global"
        self.martingale_state = {}      # Gavetas individuais para o modo "Ativo"

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
    async def place_order_and_monitor(self, symbol: str, direction: str, amount: float, duration: str, telegram_alert_cb, current_step=0):
        if self.stop_triggered:
            return None
            
        # --- LÓGICA DO MARTINGALE SINAL ANTES DE ENTRAR ---
        mg_type = self.user_config.get("martingale_type", "Nenhum")
        mg_signal_mode = self.user_config.get("martingale_signal_mode", "Global") # "Global" ou "Ativo"

        # Se for modo Sinal e for a primeira entrada do bot (não é vela esticada)
        if mg_type == "Sinal" and current_step == 0:
            
            # OPÇÃO A: Modo Global (Puxa da Fila)
            if mg_signal_mode == "Global" and len(self.martingale_queue) > 0:
                mg_data = self.martingale_queue.pop(0) 
                amount = mg_data['next_amount']
                current_step = mg_data['step']
                
                msg = f"🔄 Aplicando MG Sinal (Global) - Passo {current_step} em {symbol}: Valor ${amount:.2f}. Restam {len(self.martingale_queue)} na fila."
                logger.info(msg)
                await telegram_alert_cb(msg)
                
            # OPÇÃO B: Modo Ativo (Puxa da Gaveta Específica)
            elif mg_signal_mode == "Ativo":
                state = self.martingale_state.get(symbol, {'step': 0, 'next_amount': 0})
                if state['step'] > 0:
                    amount = state['next_amount']
                    current_step = state['step']
                    
                    # Limpa a gaveta deste ativo imediatamente
                    self.martingale_state[symbol] = {'step': 0, 'next_amount': 0}
                    
                    msg = f"🔄 Aplicando MG Sinal (Ativo) - Passo {current_step} estritamente em {symbol}: Valor ${amount:.2f}."
                    logger.info(msg)
                    await telegram_alert_cb(msg)
        
        # Lê os limites configurados pelo utilizador
        tp = self.user_config.get("take_profit", 50.0)
        sl = self.user_config.get("stop_loss", -20.0)
        
        # LÊ O LUCRO APENAS DA SESSÃO ATUAL
        lucro_sessao = await get_session_profit(self.user_config["session_start"])
        
        # 2. SEÇÃO DE RISCO ATUALIZADA
        if lucro_sessao >= tp or lucro_sessao <= sl:
            if not self.stop_triggered:
                self.stop_triggered = True # Levanta a bandeira de parada
                if lucro_sessao >= tp:
                    msg = f"🏆 *META ATINGIDA (TAKE PROFIT)!*\nLucro nesta sessão: ${lucro_sessao:.2f}\nO bot foi desligado pois o seu objetivo foi concluído."
                else:
                    msg = f"🛑 *STOP LOSS ATINGIDO!*\nPrejuízo nesta sessão: ${lucro_sessao:.2f}\nO bot foi interrompido e desligado para proteger o seu capital."
                
                # Se não tem mais nenhuma ordem aberta, encerra suavemente agora. Se tem, apenas guarda a mensagem.
                if self.active_monitors == 0:
                    logger.warning(msg.replace('*', '').replace('\n', ' | '))
                    saldo_atual = await self.scraper.get_balance(symbol)
                    if self.limit_reached_cb:
                        await self.limit_reached_cb(msg, saldo_atual, lucro_sessao)
                    else:
                        await telegram_alert_cb(msg)
                else:
                    self.limit_reached_msg = msg
            return None

        logger.info(f"⚡ Disparando ordem de {direction} via Estratégia...")
        # Adicione a variável 'amount' na chamada para o scraper!
        resultado = await self.scraper.place_order(symbol, direction, amount)
        
        if resultado and "id" in resultado:
            order_id = resultado["id"]
            payout_str = resultado.get("payout", "N/A") 
            await log_trade(symbol, direction, amount, duration, "ABERTA", order_id)
            
            # ==========================================
            # NOVA MENSAGEM DE ENTRADA PADRONIZADA
            # ==========================================
            hora_atual = datetime.now().strftime("%H:%M:%S")
            dir_icon = "🈯️ COMPRA" if direction.upper() == "BUY" else "🈲 VENDA"
            
            # Ajusta o texto se for Sinal normal ou Martingale
            if current_step == 0:
                header_msg = "🚀 *Sinal Executado!*"
                linha_hora = f"▫️ Horário do Sinal: {hora_atual}"
            else:
                header_msg = f"🔄 *Martingale Executado! (Passo {current_step})*"
                linha_hora = f"▫️ Horário do MG: {hora_atual}"
            
            msg_entrada = (
                f"{header_msg}\n"
                f"▫️ Ativo: {symbol}\n"
                f"▫️ Direção: {dir_icon}\n"
                f"▫️ Tempo: {duration}\n"
                f"▫️ Payout: {payout_str}\n"
                f"▫️ Valor Ordem: ${amount:.2f}\n"
                f"{linha_hora}"
            )
            asyncio.create_task(telegram_alert_cb(msg_entrada))
            # ==========================================
            
            # CÁLCULO DINÂMICO DE ESPERA (REGRA DOS 30 SEGUNDOS)
            agora = datetime.now()
            minutos_ativo, _ = map(int, duration.split(':'))
            
            if agora.second <= 30:
                # Pertence ao minuto atual. Subtrai 1 minuto pois o minuto atual já está a correr
                segundos_espera = (60 - agora.second) + ((minutos_ativo - 1) * 60) + 1
            else:
                # Empurrado para o minuto seguinte. Tempo restante deste minuto + tempo total do ativo
                segundos_espera = (60 - agora.second) + (minutos_ativo * 60) + 1
                
            logger.info(f"⏳ [{symbol}] O robô dormirá por {segundos_espera}s para sincronizar com a corretora...")
            
            self.active_monitors += 1
            asyncio.create_task(self._monitor_task(
                symbol, order_id, segundos_espera, telegram_alert_cb, 
                amount, direction, duration, current_step, payout_str, hora_atual
            ))
        else:
            await log_trade(symbol, direction, amount, duration, "FALHOU", None)
            msg_erro = f"❌ *Erro ao executar a ordem em {symbol}!*"
            logger.error(msg_erro.replace('*', ''))
            await telegram_alert_cb(msg_erro)

    async def _monitor_task(self, symbol, order_id, tempo_espera, telegram_alert_cb, amount: float, direction: str, duration: str, current_step: int = 0, payout: str = "N/A", hora_sinal: str = ""):
        try:
            """Espera o tempo calculado matematicamente, lê o histórico e calcula o lucro líquido real"""
            
            # O tempo de espera exato (sincronizado com os 30s da corretora) já foi calculado!
            # Basta o bot dormir exatamente essa quantidade de segundos.
            await asyncio.sleep(tempo_espera)
            
            status, lucro_bruto = await self.scraper.check_trade_result(symbol, amount, hora_sinal)
            
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
            # 2. BUSCAR BALANÇO E LUCRO ACUMULADO DA SESSÃO
            # ==========================================
            saldo_atual = await self.scraper.get_balance(symbol)
            lucro_acumulado = await get_session_profit(self.user_config["session_start"])
            
            # ==========================================
            # 3. FORMATAR E ENVIAR MENSAGEM
            # ==========================================
            if status == "WIN": emoji = "🟢 WIN"
            elif status == "LOSS": emoji = "🔴 LOSS"
            elif status == "EMPATE": emoji = "⚪ EMPATE"
            else: emoji = "⚠️ AVISO"

            dir_icon = "🈯️ COMPRA" if direction.upper() == "BUY" else "🈲 VENDA"
            
            # Mostra qual foi o horário da entrada baseada no tipo (Normal ou MG)
            linha_hora = f"▫️ Horário do Sinal: {hora_sinal}" if current_step == 0 else f"▫️ Horário do MG: {hora_sinal}"
            hora_fechamento = datetime.now().strftime("%H:%M:%S")

            msg = (
                f"*Resultado da Operação!*\n"
                f"{emoji}\n"
                f"▫️ Ativo: {symbol}\n"
                f"▫️ Direção: {dir_icon}\n"
                f"▫️ Tempo: {duration}\n"
                f"▫️ Payout: {payout}\n"
                f"▫️ Valor Ordem: ${amount:.2f}\n"
                f"{linha_hora}\n"
                f"▫️ Horário do Fim: {hora_fechamento}\n"
                f"▫️ Resultado: ${lucro_liquido:.2f}\n\n"
                f"💰 *Balanço da Conta:* ${saldo_atual:.2f}\n"
                f"📊 *Lucro Acumulado:* ${lucro_acumulado:.2f}"
            )
            # ... Mensagem de Resultado enviada ...
            asyncio.create_task(telegram_alert_cb(msg)) # Envia em 2º plano
            logger.info(msg.replace('\n', ' | ').replace('*', ''))

            # ==========================================
            # AÇÃO DE MARTINGALE PÓS-RESULTADO
            # ==========================================
            mg_type = self.user_config.get("martingale_type", "Nenhum")
            mg_signal_mode = self.user_config.get("martingale_signal_mode", "Global")
            mg_steps = self.user_config.get("martingale_steps", 0)
            mg_mult = self.user_config.get("martingale_multiplier", 2.0)

            # LÓGICA DE EMPATE/WIN: 
            if status == "WIN" or (status == "EMPATE" and current_step == 0):
                # Limpa a gaveta do ativo por segurança caso estivesse no modo 'Ativo'
                self.martingale_state[symbol] = {'step': 0, 'next_amount': 0}
                
            # Se for LOSS, ou se for EMPATE DENTRO DO MARTINGALE (current_step > 0):
            elif (status == "LOSS" or (status == "EMPATE" and current_step > 0)) and mg_type != "Nenhum":
                if current_step < mg_steps:
                    next_step = current_step + 1
                    next_amount = amount * mg_mult

                    if mg_type == "Vela":
                        motivo = "Empate" if status == "EMPATE" else "Loss"
                        msg_mg = f"🔄 *Martingale Vela* acionado por {motivo}! (Passo {next_step}/{mg_steps})\nEntrando imediatamente com ${next_amount:.2f} em {symbol} ({direction})."
                        asyncio.create_task(telegram_alert_cb(msg_mg)) 
                        asyncio.create_task(self.place_order_and_monitor(
                            symbol, direction, next_amount, duration, telegram_alert_cb, current_step=next_step
                        ))
                        
                    elif mg_type == "Sinal":
                        motivo = "Empate" if status == "EMPATE" else "Loss"
                        
                        # VERIFICA O MODO PARA SALVAR O MG
                        if mg_signal_mode == "Global":
                            self.martingale_queue.append({'step': next_step, 'next_amount': next_amount})
                            msg_mg = f"🔄 *Martingale Sinal (Global)* na fila após {motivo} em {symbol} (Passo {next_step}/{mg_steps}).\nExistem {len(self.martingale_queue)} recuperações pendentes."
                        else:
                            self.martingale_state[symbol] = {'step': next_step, 'next_amount': next_amount}
                            msg_mg = f"🔄 *Martingale Sinal (Ativo)* preparado após {motivo} em {symbol} (Passo {next_step}/{mg_steps}).\nO próximo sinal apenas de {symbol} entrará com ${next_amount:.2f}."
                            
                        await telegram_alert_cb(msg_mg)
                else:
                    msg_mg = f"⚠️ *Martingale Finalizado* após loss em {symbol}. Limite de {mg_steps} passos batido."
                    await telegram_alert_cb(msg_mg)
                    self.martingale_state[symbol] = {'step': 0, 'next_amount': 0}

            # ==========================================
            # 4. VERIFICAÇÃO IMEDIATA (STOP LOSS / TAKE PROFIT)
            # ==========================================
            tp = self.user_config.get("take_profit", 50.0)
            sl = self.user_config.get("stop_loss", -20.0)
        
            # Se bateu o limite E a bandeira de desligar ainda não foi ativada
            if (lucro_acumulado >= tp or lucro_acumulado <= sl) and not self.stop_triggered:
                self.stop_triggered = True
                if lucro_acumulado >= tp:
                    self.limit_reached_msg = f"🏆 *META ATINGIDA (TAKE PROFIT)!*\nLucro na sessão: ${lucro_acumulado:.2f}\nO bot atingiu o alvo e será desligado agora."
                else:
                    self.limit_reached_msg = f"🛑 *STOP LOSS ATINGIDO!*\nPrejuízo na sessão: ${lucro_acumulado:.2f}\nO limite de perda foi atingido. O bot será desligado."
                    
        finally: 
            # Avisa que esta operação terminou
            self.active_monitors -= 1
            
            # Se esta era a última operação rodando E o bot bateu a meta/loss, envia a mensagem e menu de parada suave
            if self.active_monitors == 0 and self.stop_triggered and self.limit_reached_msg:
                logger.warning(self.limit_reached_msg.replace('*', '').replace('\n', ' | '))
                
                saldo_atual = await self.scraper.get_balance(symbol)
                lucro_acumulado = await get_session_profit(self.user_config["session_start"])
                
                if self.limit_reached_cb:
                    await self.limit_reached_cb(self.limit_reached_msg, saldo_atual, lucro_acumulado)
                else:
                    await telegram_alert_cb(self.limit_reached_msg)
