import logging
import asyncio
from aiohttp import web
from core.config import WEBHOOK_TOKEN 
from core.database import log_trade, update_trade_result, get_daily_profit


logger = logging.getLogger(__name__)

class WebhookServer:
    def __init__(self, scraper, telegram_bot, user_config):
        self.scraper = scraper
        self.telegram_bot = telegram_bot
        self.user_config = user_config
        self.app = web.Application()
        self.app.router.add_post('/sinal', self.handle_signal)
        self.runner = None

    async def _execute_and_monitor(self, symbol, direction, amount, duration):
        
        # Lê os limites configurados pelo utilizador no Telegram
        tp = self.user_config.get("take_profit", 50.0)
        sl = self.user_config.get("stop_loss", -20.0)
        
        # ==========================================
        # 🛡️ BARREIRA DE GESTÃO DE RISCO
        # ==========================================
        lucro_diario = await get_daily_profit()
        
        if lucro_diario >= tp:
            msg = f"🏆 *META ATINGIDA (TAKE PROFIT)!*\nLucro hoje: ${lucro_diario:.2f}\nSinal do MT5 em {symbol} ignorado."
            logger.warning(msg.replace('*', '').replace('\n', ' | '))
            await self.telegram_bot.send_alert(msg)
            return None 
            
        if lucro_diario <= sl:
            msg = f"🛑 *STOP LOSS ATINGIDO!*\nPrejuízo hoje: ${lucro_diario:.2f}\nSinal do MT5 em {symbol} ignorado."
            logger.warning(msg.replace('*', '').replace('\n', ' | '))
            await self.telegram_bot.send_alert(msg)
            return None 
        # ==========================================

        """Executa a ordem e agenda a verificação do resultado pelo Histórico"""
        
        # 1. Executa a ordem (Apenas com Ativo e Direção, muito mais rápido!)
        resultado = await self.scraper.place_order(symbol, direction)
        
        if resultado and "id" in resultado:
            order_id = resultado["id"]
            await log_trade(symbol, direction, amount, duration, "ABERTA", order_id)
            
            # 2. Calcula o tempo de espera (ex: "01:00" -> 60s)
            minutos, segundos = map(int, duration.split(':'))
            tempo_total_segundos = (minutos * 60) + segundos
            
            logger.info(f"⏳ Ordem colocada. A aguardar {tempo_total_segundos + 3}s pelo fecho da vela...")
            
            # Espera o tempo da vela + 3 segundos de margem para a corretora atualizar a lista
            await asyncio.sleep(tempo_total_segundos + 3)
            
            # 3. O robô vai na aba de Histórico ver qual foi o resultado
            status, lucro = await self.scraper.check_trade_result(symbol)
            
            # 4. Atualiza o banco de dados e avisa no Telegram
            await update_trade_result(order_id, status, lucro)
            
            if status == "WIN":
                emoji = "🟢 WIN"
            elif status == "LOSS":
                emoji = "🔴 LOSS"
            elif status == "EMPATE":
                emoji = "⚪ EMPATE"
            else:
                emoji = "⚠️ AVISO"

            msg_resultado = f"{emoji} Resultado da Operação!\nAtivo: {symbol}\nLucro/Perda: ${lucro:.2f}"
            await self.telegram_bot.send_alert(msg_resultado)
            logger.info(msg_resultado.replace('\n', ' | '))
            
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