import logging
import asyncio
from aiohttp import web
from datetime import datetime
from core.config import WEBHOOK_TOKEN 
from core.database import log_trade, update_trade_result, get_session_profit


logger = logging.getLogger(__name__)

class WebhookServer:
    def __init__(self, scraper, telegram_bot, user_config):
        self.scraper = scraper
        self.telegram_bot = telegram_bot
        self.user_config = user_config
        self.app = web.Application()
        self.app.router.add_post('/sinal', self.handle_signal)
        self.runner = None
        self.martingale_state = {} # Guarda em que passo do MG cada ativo está


    async def _execute_and_monitor(self, symbol, direction, amount, duration, current_step=0, accumulated_loss=0.0):
        
        # Lê os limites configurados pelo utilizador no Telegram
        tp = self.user_config.get("take_profit", 50.0)
        sl = self.user_config.get("stop_loss", -20.0)

        # ==========================================
        # LÓGICA DO MARTINGALE ANTES DE ENTRAR
        # ==========================================
        mg_type = self.user_config.get("martingale_type", "Nenhum")
        state = self.martingale_state.get(symbol, {'step': 0, 'next_amount': amount, 'accumulated_loss': 0.0})
        
        # Se for modo Sinal, for o 1º sinal vindo do MT5 e existir um passo guardado, substituímos o valor
        if mg_type == "Sinal" and current_step == 0 and state['step'] > 0:
            amount = state['next_amount']
            current_step = state['step']
            accumulated_loss = state.get('accumulated_loss', 0.0)
            msg = f"🔄 Aplicando Martingale Sinal (Passo {current_step}) para {symbol}: Novo valor ${amount:.2f}"
            logger.info(msg)
            await self.telegram_bot.send_alert(msg)
        
        # ==========================================
        # 🛡️ BARREIRA DE GESTÃO DE RISCO DA SESSÃO
        # ==========================================
        lucro_sessao = await get_session_profit(self.user_config["session_start"])
        
        if lucro_sessao >= tp:
            msg = f"🏆 *META ATINGIDA (TAKE PROFIT)!*\nLucro na sessão: ${lucro_sessao:.2f}\nSinal do MT5 em {symbol} ignorado."
            logger.warning(msg.replace('*', '').replace('\n', ' | '))
            await self.telegram_bot.send_alert(msg)
            return None 
            
        if lucro_sessao <= sl:
            msg = f"🛑 *STOP LOSS ATINGIDO!*\nPrejuízo na sessão: ${lucro_sessao:.2f}\nSinal do MT5 em {symbol} ignorado."
            logger.warning(msg.replace('*', '').replace('\n', ' | '))
            await self.telegram_bot.send_alert(msg)
            return None
        # ==========================================

        """Executa a ordem e agenda a verificação do resultado pelo Histórico"""
        
        hora_sinal_mt5 = datetime.now().strftime("%H:%M:%S")
        
        # 1. Executa a ordem
        resultado = await self.scraper.place_order(symbol, direction, amount)
        
        # DECLARAÇÃO DO PAYOUT AQUI PARA NÃO DAR ERRO MAIS ABAIXO
        payout_str = "85%"
        
        if resultado and "id" in resultado:
            order_id = resultado["id"]
            payout_str = str(resultado.get("payout", "85%")) # Atualiza com o valor real se existir
            
            await log_trade(symbol, direction, amount, duration, "ABERTA", order_id)
            
            agora = datetime.now()
            minutos_ativo, _ = map(int, duration.split(':'))
            
            if agora.second <= 30:
                segundos_espera = (60 - agora.second) + ((minutos_ativo - 1) * 60) + 5
            else:
                segundos_espera = (60 - agora.second) + (minutos_ativo * 60) + 5
                
            logger.info(f"⏳ Ordem colocada. A aguardar {segundos_espera}s sincronizados com o relógio da corretora...")
            await asyncio.sleep(segundos_espera)
            
            # 3. Verifica o resultado
            status, lucro = await self.scraper.check_trade_result(symbol, amount, hora_sinal_mt5)
            
            # 4. Atualiza DB
            await update_trade_result(order_id, status, lucro)
            
            if status == "WIN": emoji = "🟢 WIN"
            elif status == "LOSS": emoji = "🔴 LOSS"
            elif status == "EMPATE": emoji = "⚪ EMPATE"
            else: emoji = "⚠️ AVISO"

            hora_fecho = datetime.now().strftime("%H:%M:%S")
            tipo_entrada = "Sinal" if current_step == 0 else f"Martingale (Passo {current_step})"

            msg_resultado = (
                f"*Resultado da Operação!*\n"
                f"{emoji}\n"
                f"▫️ Ativo: {symbol}\n"
                f"▫️ Tipo: {tipo_entrada}\n"
                f"▫️ Fechado às: {hora_fecho}\n"
                f"▫️ Lucro/Perda: ${lucro:.2f}"
            )
            await self.telegram_bot.send_alert(msg_resultado)
            logger.info(msg_resultado.replace('\n', ' | '))
            
            # ==========================================
            # AÇÃO DE MARTINGALE PÓS-RESULTADO
            # ==========================================
            mg_steps = self.user_config.get("martingale_steps", 0)

            if status in ["WIN", "EMPATE"]:
                self.martingale_state[symbol] = {'step': 0, 'next_amount': 0, 'accumulated_loss': 0.0}
                
            elif status == "LOSS" and mg_type != "Nenhum":
                if current_step < mg_steps:
                    next_step = current_step + 1
                    
                    # === LÓGICA HÍBRIDA (CONSERVADOR VS TRADICIONAL) ===
                    novo_prejuizo = accumulated_loss + amount
                    mg_mult = self.user_config.get("martingale_multiplier", 2.0)
                    
                    if mg_mult == "Conservador":
                        payout_decimal = 0.85
                        try:
                            if payout_str != "N/A":
                                payout_decimal = float(payout_str.replace('%', '').strip()) / 100.0
                        except: pass
                        if payout_decimal < 0.1: payout_decimal = 0.85
                        
                        next_amount = round(novo_prejuizo / payout_decimal, 2)
                        texto_modo = "Conservador"
                    else:
                        next_amount = round(amount * float(mg_mult), 2)
                        texto_modo = f"{mg_mult}x"
                    # ====================================================

                    if mg_type == "Vela":
                        msg_mg = f"🔄 *Martingale Vela* acionado! (Passo {next_step}/{mg_steps})\nModo: {texto_modo} | Entrando com ${next_amount:.2f} em {symbol}."
                        await self.telegram_bot.send_alert(msg_mg)
                        
                        asyncio.create_task(self._execute_and_monitor(
                            symbol, direction, next_amount, duration, current_step=next_step, accumulated_loss=novo_prejuizo
                        ))
                        
                    elif mg_type == "Sinal":
                        self.martingale_state[symbol] = {'step': next_step, 'next_amount': next_amount, 'accumulated_loss': novo_prejuizo}
                        msg_mg = f"🔄 *Martingale Sinal* preparado. Prejuízo de ${novo_prejuizo:.2f}. Próximo sinal será de ${next_amount:.2f} ({texto_modo})."
                        await self.telegram_bot.send_alert(msg_mg)
                else:
                    msg_mg = f"⚠️ *Martingale Finalizado* em {symbol}. Limite de {mg_steps} passos batido. Retornando ao normal."
                    await self.telegram_bot.send_alert(msg_mg)
                    self.martingale_state[symbol] = {'step': 0, 'next_amount': 0, 'accumulated_loss': 0.0}
            
        else:
            await log_trade(symbol, direction, amount, duration, "FALHOU", None)


    async def handle_signal(self, request):
        try:
            token_recebido = request.headers.get('X-API-Token')
            if not token_recebido or token_recebido != WEBHOOK_TOKEN:
                return web.Response(text="Acesso não autorizado.", status=401)

            data = await request.json()
            ativo = data.get('ativo')
            direcao = data.get('direcao')
            
            if not ativo or not direcao:
                return web.Response(text="Faltam dados", status=400)

            if ativo not in self.user_config.get("assets", []):
                return web.Response(text="Ativo não configurado", status=400)

            amount = self.user_config.get("amount", 1.0)
            duration = self.user_config.get("duration", "01:00")

            msg = f"🔔 SINAL MT5 RECEBIDO!\nAtivo: {ativo}\nDireção: {direcao.upper()}"
            await self.telegram_bot.send_alert(f"🚀 {msg}")

            # Chama a nossa nova função que executa e depois espera para monitorizar
            asyncio.create_task(
                self._execute_and_monitor(ativo, direcao, amount, duration)
            )

            return web.Response(text="Sinal processado com sucesso!", status=200)

        except Exception as e:
            logger.error(f"Erro ao processar sinal do webhook: {e}")
            return web.Response(text="Erro interno no servidor", status=500)

    # ... as funções start() e stop() mantêm-se iguais
    async def start(self, port=8080):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', port)
        await site.start()
        logger.info(f"🌐 Webhook do MT5 a escutar em http://127.0.0.1:{port}/sinal")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            logger.info("Servidor Webhook encerrado.")