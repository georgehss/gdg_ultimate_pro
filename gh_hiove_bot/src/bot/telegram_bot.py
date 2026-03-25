import logging, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from core.database import save_user_config, load_user_config, get_session_profit
from core.config import TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID

logger = logging.getLogger(__name__)

class TradingTelegramBot:
    def __init__(self, strategy_manager=None, broker=None):
        self.token = TELEGRAM_BOT_TOKEN
        self.admin_id = str(ADMIN_CHAT_ID)
        self.manager = strategy_manager
        self.broker = broker
        self.app = None
        
        self.setup_event = asyncio.Event()
        self.stop_session_event = asyncio.Event() # Evento para gerir a paragem da sessão
        
        # Guardará TODAS as escolhas do usuário
        self.user_config = {
            "is_demo": True,
            "mode": "strategy",
            "profile": "Balanceado", 
            "assets": [],       
            "duration": "01:00",
            "amount": 1.0,
            "martingale_type": "Nenhum",   
            "martingale_steps": 0,         
            "martingale_multiplier": 2.0,  
            "take_profit": 50.0,
            "stop_loss": -20.0
        }
        self.setup_step = "account" # Controla em qual passo estamos

    async def check_auth(self, update: Update) -> bool:
        user_id = str(update.effective_user.id)
        if user_id != self.admin_id:
            await update.message.reply_text("⛔ Acesso negado.")
            return False
        return True

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update): return
        
        if self.setup_event.is_set():
            await update.message.reply_text("⚠️ O robô já está rodando! Para reconfigurar, pare a sessão atual com /stop.")
            return

        # Verifica se tem configuração salva
        saved_config = await load_user_config()
        if saved_config:
            self.setup_step = "start_menu" # Vai para o novo menu
            await self.send_setup_step(update.message)
        else:
            # Se não tiver, começa do zero
            self.setup_step = "account"
            self.user_config["assets"] = []
            await self.send_setup_step(update.message)

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update): return
        
        if not self.setup_event.is_set():
            await update.message.reply_text("⚠️ Não há nenhuma sessão ativa para parar. Use /start para iniciar.")
            return

        keyboard = [
            [InlineKeyboardButton("✅ Sim, parar sessão", callback_data='stop_yes')],
            [InlineKeyboardButton("❌ Não, continuar", callback_data='stop_no')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🛑 *Atenção!*\nVocê quer realmente encerrar a sessão atual do robô e parar as operações?", 
            parse_mode='Markdown', reply_markup=reply_markup
        )

    async def send_setup_step(self, message_obj, is_edit=False):
        """Envia ou edita a mensagem dependendo do passo atual"""
        text = ""
        keyboard = []

        # NOVO MENU DE CONFIGURAÇÃO SALVA
        if self.setup_step == "start_menu":
            text = "💾 *Configuração Salva Encontrada*\nDeseja iniciar rapidamente com a última configuração ou criar uma nova do zero?"
            keyboard = [
                [InlineKeyboardButton("▶️ Iniciar com Configuração Salva", callback_data='menu_load_saved')],
                [InlineKeyboardButton("⚙️ Criar Nova Configuração", callback_data='menu_new_config')]
            ]

        elif self.setup_step == "account":
            text = "🤖 *Tipo de Conta*\nOnde deseja operar?"
            keyboard = [
                [InlineKeyboardButton("🟢 Conta DEMO", callback_data='acc_demo')],
                [InlineKeyboardButton("🔴 Conta REAL", callback_data='acc_real')]
            ]
            
        elif self.setup_step == "mode":
            text = "⚙️ *Modo de Operação*\nEscolha a lógica do robô:"
            keyboard = [
                [InlineKeyboardButton("📈 Estratégias Internas", callback_data='mod_strategy_menu')],
                [InlineKeyboardButton("📡 Sinais MT5 (Webhook)", callback_data='mod_live')],
                [InlineKeyboardButton("📋 Lista de Sinais (Em breve)", callback_data='mod_list')]
            ]
            
        elif self.setup_step == "strategy_type":
            text = "🧠 *Escolha a Estratégia*\nQual estratégia o robô deve usar?"
            keyboard = [
                [InlineKeyboardButton("📊 Estratégia RSI", callback_data='strat_strat_rsi')],
                [InlineKeyboardButton("📊 Estratégia MA Cross", callback_data='strat_strat_ma')],
                [InlineKeyboardButton("📊 Estratégia Engolfo MA", callback_data='strat_strat_engulf')],
                [InlineKeyboardButton("🤝 Consenso (As 3 Juntas)", callback_data='strat_strat_consensus')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='strat_back')]
            ]

        elif self.setup_step == "profile":
            text = "⚖️ *Perfil de Operação*\nComo o robô deve se comportar no mercado?"
            keyboard = [
                [InlineKeyboardButton("🛡️ Conservador (Alta precisão, menos entradas)", callback_data='prof_Conservador')],
                [InlineKeyboardButton("⚖️ Balanceado (Padrão)", callback_data='prof_Balanceado')],
                [InlineKeyboardButton("🔥 Agressivo (Muitas entradas, maior risco)", callback_data='prof_Agressivo')],
                [InlineKeyboardButton("⚙️ Customizado (Valores padrão originais)", callback_data='prof_Customizado')]
            ]
            
        elif self.setup_step == "assets":
            text = "🪙 *Ativos*\nClique nos ativos que deseja operar e depois em Continuar:"
            # Adiciona um "✅" visual se o ativo já estiver na lista
            xrp_text = "✅ XRP" if "XRP/USDT" in self.user_config["assets"] else "XRP"
            eth_text = "✅ ETH" if "ETH/USDT" in self.user_config["assets"] else "ETH"
            sol_text = "✅ SOL" if "SOL/USDT" in self.user_config["assets"] else "SOL"
            btc_text = "✅ BTC" if "BTC/USDT" in self.user_config["assets"] else "BTC"
            
            keyboard = [
                [InlineKeyboardButton(xrp_text, callback_data='ast_XRP/USDT'),
                 InlineKeyboardButton(eth_text, callback_data='ast_ETH/USDT')],
                [InlineKeyboardButton(sol_text, callback_data='ast_SOL/USDT'),
                 InlineKeyboardButton(btc_text, callback_data='ast_BTC/USDT')],
                [InlineKeyboardButton("➡️ Continuar", callback_data='ast_done')]
            ]

        elif self.setup_step == "duration":
            text = "⏳ *Tempo de Operação*\nQual a duração de cada entrada?"
            keyboard = [
                [InlineKeyboardButton("1 Minuto", callback_data='dur_01:00'),
                 InlineKeyboardButton("5 Minutos", callback_data='dur_05:00')],
                [InlineKeyboardButton("15 Minutos", callback_data='dur_15:00')]
            ]
            
        elif self.setup_step == "amount":
            text = "💵 *Valor de Entrada*\nQuanto investir por operação?"
            keyboard = [
                [InlineKeyboardButton("$ 1", callback_data='amt_1'),
                 InlineKeyboardButton("$ 5", callback_data='amt_5')],
                [InlineKeyboardButton("$ 10", callback_data='amt_10'),
                 InlineKeyboardButton("$ 20", callback_data='amt_20')]
            ]

        elif self.setup_step == "martingale_type":
            text = "🔄 *Martingale*\nDeseja utilizar recuperação de perdas (Martingale)?"
            keyboard = [
                [InlineKeyboardButton("❌ Nenhum", callback_data='mgtype_Nenhum')],
                [InlineKeyboardButton("🕯️ Na Próxima Vela", callback_data='mgtype_Vela')],
                [InlineKeyboardButton("📡 No Próximo Sinal", callback_data='mgtype_Sinal')]
            ]

        elif self.setup_step == "martingale_steps":
            text = "🔢 *Passos do Martingale*\nQuantas vezes o bot deve tentar recuperar?"
            keyboard = [
                [InlineKeyboardButton("1 Passo", callback_data='mgstep_1'),
                 InlineKeyboardButton("2 Passos", callback_data='mgstep_2')],
                [InlineKeyboardButton("3 Passos", callback_data='mgstep_3')]
            ]

        elif self.setup_step == "martingale_multiplier":
            text = "✖️ *Multiplicador*\nQual o fator de multiplicação de banca do MG?"
            keyboard = [
                [InlineKeyboardButton("2.0 x", callback_data='mgmult_2.0'),
                 InlineKeyboardButton("2.2 x", callback_data='mgmult_2.2')],
                [InlineKeyboardButton("2.5 x", callback_data='mgmult_2.5'),
                 InlineKeyboardButton("3.0 x", callback_data='mgmult_3.0')]
            ]

        elif self.setup_step == "take_profit":
            text = "🎯 *Meta de Lucro (Take Profit)*\nAo atingir que lucro o bot deve parar hoje?"
            keyboard = [
                [InlineKeyboardButton("$ 10", callback_data='tp_10.0'),
                 InlineKeyboardButton("$ 20", callback_data='tp_20.0')],
                [InlineKeyboardButton("$ 50", callback_data='tp_50.0'),
                 InlineKeyboardButton("$ 100", callback_data='tp_100.0')]
            ]

        elif self.setup_step == "stop_loss":
            text = "🛑 *Limite de Perda (Stop Loss)*\nAo atingir que prejuízo o bot deve parar hoje para proteger a banca?"
            keyboard = [
                [InlineKeyboardButton("-$ 10", callback_data='sl_-10.0'),
                 InlineKeyboardButton("-$ 20", callback_data='sl_-20.0')],
                [InlineKeyboardButton("-$ 50", callback_data='sl_-50.0'),
                 InlineKeyboardButton("-$ 100", callback_data='sl_-100.0')]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if is_edit:
            await message_obj.edit_text(text=text, parse_mode='Markdown', reply_markup=reply_markup)
        else:
            await message_obj.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        # ==========================================
        # NOVOS COMANDOS DE PARADA E PÓS-PARADA
        # ==========================================
        if data == 'stop_yes':
            self.stop_session_event.set() # Avisa o main.py para destruir a sessão
            keyboard = [
                [InlineKeyboardButton("🔄 Nova Sessão", callback_data='session_new')],
                [InlineKeyboardButton("💤 Deixar em Espera", callback_data='session_standby')]
            ]
            await query.edit_message_text(
                "✅ *Sessão Encerrada com Sucesso!*\nTodas as abas de ativos foram fechadas. O que deseja fazer agora?", 
                parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        elif data == 'stop_no':
            await query.edit_message_text("▶️ Parada cancelada. A sessão atual continua operando normalmente.")
            return

        
        # GERENCIAMENTO DA CONFIGURAÇÃO SALVA
        elif data == 'session_new':
            saved_config = await load_user_config()
            if saved_config:
                self.setup_step = "start_menu"
                await self.send_setup_step(query.message, is_edit=True)
            else:
                self.setup_step = "account"
                self.user_config["assets"] = []
                await self.send_setup_step(query.message, is_edit=True)
            return

        elif data == 'menu_load_saved':
            saved_config = await load_user_config()
            if saved_config:
                self.user_config.update(saved_config) # Puxa tudo da memória
                await self._show_final_summary(query) # Mostra o resumo e inicia
            return

        elif data == 'menu_new_config':
            self.setup_step = "account"
            self.user_config["assets"] = []
            await self.send_setup_step(query.message, is_edit=True)
            return

        elif data == 'session_standby':
            await query.edit_message_text("💤 *Robô em modo de espera.*\n\nO sistema continua online no terminal. Quando quiser operar novamente, basta digitar /start.", parse_mode='Markdown')
            return

        # ==========================================
        # BLOQUEIO DE SEGURANÇA ORIGINAL
        # Garante que os botões de setup não sejam clicados durante uma sessão ativa
        if self.setup_event.is_set():
            await query.edit_message_text(text="⚠️ O robô já foi inicializado e está operando.")
            return

        # Passo 1: Conta
        if data.startswith('acc_'):
            self.user_config["is_demo"] = (data == 'acc_demo')
            self.setup_step = "mode"
            
        # Passo 2: Modo de Operação
        elif data.startswith('mod_'):
            modo_escolhido = data.replace('mod_', '')
            
            if modo_escolhido == "strategy_menu":
                # Se ele escolheu o menu de estratégias, mudamos para o ecrã secundário
                self.setup_step = "strategy_type"
                await self.send_setup_step(query.message, is_edit=True)
                return
            else:
                # Se for live ou list, guarda o modo e salta direto para os ativos
                self.user_config["mode"] = modo_escolhido 
                self.setup_step = "assets"
                
        # Passo 2.1: Submenu de Estratégias Internas
        elif data.startswith('strat_'):
            strat_escolhida = data.replace('strat_', '', 1)
            if strat_escolhida == "back":
                self.setup_step = "mode"
            else:
                self.user_config["mode"] = strat_escolhida
                self.setup_step = "profile"
                
        # NOVO PASSO: Perfil de Operação
        elif data.startswith('prof_'):
            self.user_config["profile"] = data.split('_')[1]
            self.setup_step = "assets"
            
        # Passo 3: Ativos
        elif data.startswith('ast_'):
            asset = data.replace('ast_', '')
            if asset == "done":
                if not self.user_config["assets"]:
                    await query.answer("Escolha pelo menos 1 ativo!", show_alert=True)
                    return
                self.setup_step = "duration"
            else:
                # Adiciona ou remove o ativo da lista (Toggle)
                if asset in self.user_config["assets"]:
                    self.user_config["assets"].remove(asset)
                else:
                    self.user_config["assets"].append(asset)
                    
        # Passo 4: Duração
        elif data.startswith('dur_'):
            self.user_config["duration"] = data.split('_')[1]
            self.setup_step = "amount"
            
        # Passo 5: Valor
        elif data.startswith('amt_'):
            self.user_config["amount"] = float(data.split('_')[1])
            self.setup_step = "martingale_type" # Vai para o Martingale
            
        # Passo 6: Martingale Type
        elif data.startswith('mgtype_'):
            self.user_config["martingale_type"] = data.split('_')[1]
            if self.user_config["martingale_type"] == "Nenhum":
                self.setup_step = "take_profit" # Pula o resto do MG
            else:
                self.setup_step = "martingale_steps"

        # Passo 7: Martingale Steps
        elif data.startswith('mgstep_'):
            self.user_config["martingale_steps"] = int(data.split('_')[1])
            self.setup_step = "martingale_multiplier"

        # Passo 8: Martingale Multiplier
        elif data.startswith('mgmult_'):
            self.user_config["martingale_multiplier"] = float(data.split('_')[1])
            self.setup_step = "take_profit"
            
        # Passo 9: Take Profit
        elif data.startswith('tp_'):
            self.user_config["take_profit"] = float(data.split('_')[1])
            self.setup_step = "stop_loss"

        # Passo 10: Stop Loss (Finaliza!)
        elif data.startswith('sl_'):
            self.user_config["stop_loss"] = float(data.split('_')[1])
            await self._show_final_summary(query)
            return

        # Atualiza o painel para o próximo passo (se não for o fim)
        await self.send_setup_step(query.message, is_edit=True)

    # (Os métodos status_command, send_alert, start_polling e stop continuam iguais ao anterior)
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update): return
        
        if not self.setup_event.is_set():
            await update.message.reply_text("⚠️ O robô ainda não foi inicializado. Use /start para iniciar uma sessão.")
            return

        # Envia uma mensagem de carregamento, pois ir ler a tela da corretora pode demorar 1 ou 2 segundos
        status_msg = await update.message.reply_text("⏳ *Consultando dados ao vivo da corretora...*", parse_mode='Markdown')

        # ==========================================
        # 1. OBTER DADOS AO VIVO (Saldo e Lucro)
        # ==========================================
        saldo_atual = 0.0
        if self.broker and self.broker.scraper:
            # Tenta ler o saldo usando a aba do primeiro ativo aberto para ser mais rápido
            ativo_base = self.user_config["assets"][0] if self.user_config.get("assets") else None
            saldo_atual = await self.broker.scraper.get_balance(ativo_base)
        
        lucro_sessao = 0.0
        session_start = self.user_config.get("session_start")
        if session_start:
            lucro_sessao = await get_session_profit(session_start)

        # ==========================================
        # 2. FORMATAR TEXTOS DA CONFIGURAÇÃO
        # ==========================================
        # Formata a string do Martingale
        mg_str = "❌ Desativado"
        if self.user_config.get('martingale_type') and self.user_config['martingale_type'] != "Nenhum":
            mg_str = f"{self.user_config['martingale_type']} | {self.user_config['martingale_steps']} passos | {self.user_config['martingale_multiplier']}x"

        # Formata a lista de estratégias ativas
        if self.manager and self.manager.strategies:
            active_strats = [s.name.replace('_', '\\_') for s in self.manager.strategies if getattr(s, 'is_running', False)]
            strat_msg = "\n".join([f"✅ {name}" for name in active_strats]) if active_strats else "❌ Nenhuma estratégia a rodar."
        else:
            strat_msg = "📡 Aguardando sinais do MT5 (Webhook)..." if self.user_config.get('mode') == 'live' else "❌ Nenhum gerenciador vinculado."

        # Captura os dados de forma segura (com valores padrão caso algo falte)
        tipo_conta = "DEMO 🟢" if self.user_config.get('is_demo') else "REAL 🔴"
        modo = self.user_config.get('mode', 'Desconhecido').upper().replace('_', ' ')
        perfil = self.user_config.get('profile', 'Balanceado')
        tempo = self.user_config.get('duration', '01:00')
        valor = float(self.user_config.get('amount', 1.0))
        tp = float(self.user_config.get('take_profit', 0.0))
        sl = float(self.user_config.get('stop_loss', 0.0))

        # ==========================================
        # 3. MONTAR A MENSAGEM FINAL
        # ==========================================
        msg = (
            f"📊 *STATUS DO SISTEMA*\n\n"
            f"▫️ Conta: {tipo_conta}\n"
            f"▫️ Modo: {modo}\n"
            f"▫️ Perfil: {perfil}\n"
            f"▫️ Tempo: {tempo}\n"
            f"▫️ Valor Ordem: ${valor:.2f}\n"
            f"🔄 Martingale: {mg_str}\n"
            f"🎯 Take Profit: ${tp:.2f}\n"
            f"🛑 Stop Loss: ${sl:.2f}\n\n"
            f"💰 *Balanço Atual:* ${saldo_atual:.2f}\n"
            f"📈 *Lucro da Sessão:* ${lucro_sessao:.2f}\n\n"
            f"*Estratégias Ativas:*\n{strat_msg}"
        )

        # Edita a mensagem de carregamento com os dados reais
        await status_msg.edit_text(text=msg, parse_mode='Markdown')

    async def send_alert(self, message: str):
        if not self.app or not self.admin_id: return
        try:
            await self.app.bot.send_message(chat_id=self.admin_id, text=message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Erro ao enviar alerta via Telegram: {e}")

    async def start_polling(self):
        if not self.token or not self.admin_id: return
        self.app = ApplicationBuilder().token(self.token).build()
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("stop", self.stop_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CallbackQueryHandler(self.button_handler)) 
        logger.info("Bot do Telegram inicializado. Aguardando /start...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    async def stop(self):
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    async def _show_final_summary(self, query):
        """Mostra o resumo e dispara a inicialização do robô"""
        mg_str = f"❌ Desativado"
        if self.user_config['martingale_type'] != "Nenhum":
            mg_str = f"{self.user_config['martingale_type']} | {self.user_config['martingale_steps']} passos | {self.user_config['martingale_multiplier']}x"

        resumo = (
            f"🚀 *SISTEMA INICIANDO!*\n\n"
            f"▫️ Conta: {'DEMO 🟢' if self.user_config['is_demo'] else 'REAL 🔴'}\n"
            f"▫️ Modo: {self.user_config['mode'].upper().replace('_', ' ')}\n"
            f"▫️ Perfil: {self.user_config.get('profile', 'Balanceado')}\n"
            f"▫️ Ativos: {', '.join(self.user_config['assets'])}\n"
            f"▫️ Tempo: {self.user_config['duration']}\n"
            f"▫️ Valor Ordem: ${self.user_config['amount']}\n"
            f"🔄 Martingale: {mg_str}\n"
            f"🎯 Take Profit: ${self.user_config['take_profit']}\n"
            f"🛑 Stop Loss: ${self.user_config['stop_loss']}\n\n"
            f"⏳ *Abrindo navegador...*"
        )
        await query.edit_message_text(text=resumo, parse_mode='Markdown')
        
        # SALVA A CONFIGURAÇÃO NO BANCO DE DADOS AQUI!
        await save_user_config(self.user_config)
        
        self.setup_event.set()