import logging, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
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
        self.stop_session_event = asyncio.Event() 
        
        self.user_config = {
            "is_demo": True,
            "mode": "strategy",
            "active_strategies": ["rsi", "ma", "engulf"],
            "min_votes_required": 2,
            "profile": "Balanceado", 
            "assets": [],
            "timeframe": "1m",     
            "duration": "01:00",
            "amount": 1.0,
            "martingale_type": "Nenhum",  
            "martingale_signal_mode": "Global", 
            "martingale_steps": 0,         
            "martingale_multiplier": 2.0,  
            "take_profit": 50.0,
            "stop_loss": -20.0
        }
        self.setup_step = "account" 

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

        saved_config = await load_user_config()
        if saved_config:
            self.setup_step = "start_menu" 
            await self.send_setup_step(update.message)
        else:
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
        text = ""
        keyboard = []

        if self.setup_step == "start_menu":
            saved_config = await load_user_config()
            
            if saved_config:
                tipo_conta = "DEMO 🟢" if saved_config.get('is_demo') else "REAL 🔴"
                modo = saved_config.get('mode', 'Desconhecido').upper().replace('_', ' ')
                perfil = saved_config.get('profile', 'Balanceado')
                ativos = ", ".join(saved_config.get('assets', []))
                tempo = saved_config.get('duration', '01:00')
                valor = float(saved_config.get('amount', 1.0))
                tp = float(saved_config.get('take_profit', 0.0))
                sl = float(saved_config.get('stop_loss', 0.0))
                
                mg_str = "❌ Desativado"
                if saved_config.get('martingale_type') and saved_config['martingale_type'] != "Nenhum":
                    modo_str = f" ({saved_config.get('martingale_signal_mode', 'Global')})" if saved_config['martingale_type'] == "Sinal" else ""
                    mult = saved_config.get('martingale_multiplier', 0)
                    if mult == "Conservador":
                        mg_str = f"{saved_config['martingale_type']}{modo_str} | {saved_config.get('martingale_steps', 0)} passos | Conservador"
                    else:
                        mg_str = f"{saved_config['martingale_type']}{modo_str} | {saved_config.get('martingale_steps', 0)} passos | {mult}x"

                text = (
                    f"💾 *Configuração Salva Encontrada*\n\n"
                    f"📋 *Resumo das definições:*\n"
                    f"▫️ Conta: {tipo_conta}\n"
                    f"▫️ Modo: {modo}\n"
                    f"▫️ Perfil: {perfil}\n"
                    f"▫️ Análise Gráfica: {saved_config.get('timeframe', '1m')}\n"
                    f"▫️ Ativos: {ativos}\n"
                    f"▫️ Tempo Expiração: {tempo}\n"
                    f"▫️ Valor Ordem: ${valor:.2f}\n"
                    f"🔄 Martingale: {mg_str}\n"
                    f"🎯 Take Profit: ${tp:.2f}\n"
                    f"🛑 Stop Loss: ${sl:.2f}\n\n"
                    f"Deseja iniciar o robô com esta configuração ou criar uma nova do zero?"
                )
            else:
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
                [InlineKeyboardButton("📋 Lista de Sinais (Em breve)", callback_data='mod_list')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_mode')]
            ]
            
        elif self.setup_step == "strategy_type":
            text = "🧠 *Escolha a Estratégia*\nQual estratégia o robô deve usar?"
            keyboard = [
                [InlineKeyboardButton("📊 Estratégia RSI", callback_data='strat_strat_rsi')],
                [InlineKeyboardButton("📊 Estratégia MA Cross", callback_data='strat_strat_ma')],
                [InlineKeyboardButton("📊 Estratégia Engolfo MA", callback_data='strat_strat_engulf')],
                [InlineKeyboardButton("🤝 Consenso (As 3 Juntas)", callback_data='strat_strat_consensus')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_strategy_type')] 
            ]

        elif self.setup_step == "consensus_strategies":
            text = "🤝 *Estratégias do Consenso*\nQuais estratégias devem participar da votação? (Escolha pelo menos 2)"
            
            ativas = self.user_config.get("active_strategies", [])
            rsi_text = "✅ RSI" if "rsi" in ativas else "RSI"
            ma_text = "✅ MA Cross" if "ma" in ativas else "MA Cross"
            engulf_text = "✅ Price Action" if "engulf" in ativas else "Price Action"

            keyboard = [
                [InlineKeyboardButton(rsi_text, callback_data='cstrat_rsi'),
                 InlineKeyboardButton(ma_text, callback_data='cstrat_ma')],
                [InlineKeyboardButton(engulf_text, callback_data='cstrat_engulf')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_consensus_strategies'),
                 InlineKeyboardButton("➡️ Continuar", callback_data='cstrat_done')] 
            ]

        elif self.setup_step == "consensus_votes":
            ativas = len(self.user_config.get("active_strategies", []))
            text = f"🗳️ *Votos Necessários*\nVocê ativou {ativas} estratégias. Quantos votos iguais a favor (sem conflito) são necessários para fazer uma entrada?"
            
            keyboard = []
            row = []
            for i in range(1, ativas + 1):
                row.append(InlineKeyboardButton(f"{i} Voto{'s' if i>1 else ''}", callback_data=f'cvote_{i}'))
            keyboard.append(row)
            keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data='back_consensus_votes')])

        elif self.setup_step == "profile":
            text = "⚖️ *Perfil de Operação*\nComo o robô deve se comportar no mercado?"
            keyboard = [
                [InlineKeyboardButton("🛡️ Conservador (Alta precisão, menos entradas)", callback_data='prof_Conservador')],
                [InlineKeyboardButton("⚖️ Balanceado (Padrão)", callback_data='prof_Balanceado')],
                [InlineKeyboardButton("🔥 Agressivo (Muitas entradas, maior risco)", callback_data='prof_Agressivo')],
                [InlineKeyboardButton("⚙️ Customizado (Valores padrão originais)", callback_data='prof_Customizado')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_profile')]
            ]

        elif self.setup_step == "wait_custom_params":
            text = (
                "✍️ *Configuração Customizada*\n\n"
                "Digite os parâmetros que deseja usar separados por vírgula. "
                "Pode digitar apenas os primeiros se quiser manter o resto como padrão.\n\n"
                "📌 *Ordem de Inserção:*\n\n"
                "▫️ *RSI:* `RSI Per, O.Bought, O.Sold, SMMA Long, EMA Curta, ADX Min, Vol Mult, Volm Mult, BB Std`\n"
                "👉 *Ex:* `14, 70, 30, 100, 9, 20, 0.7, 1.0, 2.0`\n\n"
                "▫️ *MA Cross:* `EMA Rápida, EMA Lenta, SMMA Long, Cooldown, ATR Mult, ADX Min, Volm Mult, RSI Max Compra, RSI Min Venda`\n"
                "👉 *Ex:* `9, 21, 100, 3, 0.15, 20, 1.0, 65, 35`\n\n"
                "▫️ *Engolfo MA:* `SMMA Curta, SMMA Média, SMMA Long, ADX Min, Vol Mult, RSI Pullback C, RSI Pullback V, Volm Mult`\n"
                "👉 *Ex:* `8, 59, 200, 18, 0.8, 60, 40, 1.0`\n\n"
                "Escreva os seus valores agora:"
            )
            keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data='back_profile')]]

        elif self.setup_step == "timeframe":
            text = "📊 *Tempo de Análise (Timeframe)*\nQual o tempo gráfico das velas para o robô analisar?"
            keyboard = [
                [InlineKeyboardButton("1 Minuto", callback_data='tf_1m'),
                 InlineKeyboardButton("5 Minutos", callback_data='tf_5m')],
                [InlineKeyboardButton("15 Minutos", callback_data='tf_15m')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_timeframe')]
            ]
            
        elif self.setup_step == "assets":
            text = "🪙 *Ativos*\nClique nos ativos que deseja operar e depois em Continuar:"
            xrp_text = "✅ XRP" if "XRP/USDT" in self.user_config["assets"] else "XRP"
            eth_text = "✅ ETH" if "ETH/USDT" in self.user_config["assets"] else "ETH"
            sol_text = "✅ SOL" if "SOL/USDT" in self.user_config["assets"] else "SOL"
            btc_text = "✅ BTC" if "BTC/USDT" in self.user_config["assets"] else "BTC"
            
            keyboard = [
                [InlineKeyboardButton(xrp_text, callback_data='ast_XRP/USDT'),
                 InlineKeyboardButton(eth_text, callback_data='ast_ETH/USDT')],
                [InlineKeyboardButton(sol_text, callback_data='ast_SOL/USDT'),
                 InlineKeyboardButton(btc_text, callback_data='ast_BTC/USDT')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_assets'),
                 InlineKeyboardButton("➡️ Continuar", callback_data='ast_done')] 
            ]

        elif self.setup_step == "duration":
            text = "⏳ *Tempo de Operação*\nQual a duração de cada entrada?"
            keyboard = [
                [InlineKeyboardButton("1 Minuto", callback_data='dur_01:00'),
                 InlineKeyboardButton("5 Minutos", callback_data='dur_05:00')],
                [InlineKeyboardButton("15 Minutos", callback_data='dur_15:00')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_duration')]
            ]
            
        elif self.setup_step == "amount":
            text = "💵 *Valor de Entrada*\nQuanto investir por operação?"
            keyboard = [
                [InlineKeyboardButton("$ 1", callback_data='amt_1'),
                 InlineKeyboardButton("$ 2", callback_data='amt_2'),
                 InlineKeyboardButton("$ 3", callback_data='amt_3')],
                [InlineKeyboardButton("$ 5", callback_data='amt_5'),
                 InlineKeyboardButton("$ 10", callback_data='amt_10'),
                 InlineKeyboardButton("$ 20", callback_data='amt_20')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_amount')]
            ]

        elif self.setup_step == "martingale_type":
            text = "🔄 *Martingale*\nDeseja utilizar recuperação de perdas (Martingale)?"
            keyboard = [
                [InlineKeyboardButton("❌ Nenhum", callback_data='mgtype_Nenhum')],
                [InlineKeyboardButton("🕯️ Na Próxima Vela", callback_data='mgtype_Vela')],
                [InlineKeyboardButton("📡 No Próximo Sinal", callback_data='mgtype_Sinal')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_martingale_type')]
            ]

        elif self.setup_step == "martingale_signal_mode":
            text = "🌐 *Modo do Martingale Sinal*\nComo o bot deve usar os próximos sinais para recuperar o loss?"
            keyboard = [
                [InlineKeyboardButton("🌍 Global (Qualquer ativo que der sinal)", callback_data='mgmode_Global')],
                [InlineKeyboardButton("🎯 Ativo (Apenas no mesmo ativo do loss)", callback_data='mgmode_Ativo')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_martingale_signal_mode')]
            ]

        elif self.setup_step == "martingale_steps":
            text = "🔢 *Passos do Martingale*\nQuantas vezes o bot deve tentar recuperar?"
            keyboard = [
                [InlineKeyboardButton("1 Passo", callback_data='mgstep_1'),
                 InlineKeyboardButton("2 Passos", callback_data='mgstep_2'),
                 InlineKeyboardButton("3 Passos", callback_data='mgstep_3')],
                [InlineKeyboardButton("4 Passos", callback_data='mgstep_4'),
                 InlineKeyboardButton("5 Passos", callback_data='mgstep_5'),
                 InlineKeyboardButton("6 Passos", callback_data='mgstep_6')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_martingale_steps')]
            ]

        elif self.setup_step == "martingale_multiplier":
            text = "✖️ *Multiplicador*\nQual o fator de multiplicação de banca do MG?"
            keyboard = [
                [InlineKeyboardButton("🛡️ Conservador (Apenas Recupera)", callback_data='mgmult_Conservador')],
                [InlineKeyboardButton("1.0 x", callback_data='mgmult_1.0'),
                 InlineKeyboardButton("1.2 x", callback_data='mgmult_1.2'),
                 InlineKeyboardButton("1.5 x", callback_data='mgmult_1.5')],
                [InlineKeyboardButton("1.8 x", callback_data='mgmult_1.8'),
                 InlineKeyboardButton("2.0 x", callback_data='mgmult_2.0'),
                 InlineKeyboardButton("2.2 x", callback_data='mgmult_2.2')],
                [InlineKeyboardButton("2.5 x", callback_data='mgmult_2.5'),
                 InlineKeyboardButton("3.0 x", callback_data='mgmult_3.0')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_martingale_multiplier')]
            ]

        elif self.setup_step == "take_profit":
            text = "🎯 *Meta de Lucro (Take Profit)*\nAo atingir que lucro o bot deve parar hoje?"
            keyboard = [
                [InlineKeyboardButton("$ 2", callback_data='tp_2.0'),
                 InlineKeyboardButton("$ 3", callback_data='tp_3.0'),
                 InlineKeyboardButton("$ 5", callback_data='tp_5.0')],
                [InlineKeyboardButton("$ 10", callback_data='tp_10.0'),
                 InlineKeyboardButton("$ 20", callback_data='tp_20.0')],
                [InlineKeyboardButton("$ 50", callback_data='tp_50.0'),
                 InlineKeyboardButton("$ 100", callback_data='tp_100.0')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_take_profit')]
            ]

        elif self.setup_step == "stop_loss":
            text = "🛑 *Limite de Perda (Stop Loss)*\nAo atingir que prejuízo o bot deve parar hoje para proteger a banca?"
            keyboard = [
                [InlineKeyboardButton("-$ 2", callback_data='sl_-2.0'),
                 InlineKeyboardButton("-$ 3", callback_data='sl_-3.0'),
                 InlineKeyboardButton("-$ 5", callback_data='sl_-5.0')],
                [InlineKeyboardButton("-$ 10", callback_data='sl_-10.0'),
                 InlineKeyboardButton("-$ 20", callback_data='sl_-20.0')],
                [InlineKeyboardButton("-$ 50", callback_data='sl_-50.0'),
                 InlineKeyboardButton("-$ 100", callback_data='sl_-100.0')],
                [InlineKeyboardButton("⬅️ Voltar", callback_data='back_stop_loss')]
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

        if data == 'stop_yes':
            await query.edit_message_text("⏳ *Calculando resultados e encerrando sessão...*", parse_mode='Markdown')
            
            saldo_atual = 0.0
            if self.broker and self.broker.scraper:
                ativo_base = self.user_config["assets"][0] if self.user_config.get("assets") else None
                saldo_atual = await self.broker.scraper.get_balance(ativo_base)
            
            lucro_sessao = 0.0
            session_start = self.user_config.get("session_start")
            if session_start:
                lucro_sessao = await get_session_profit(session_start)

            self.stop_session_event.set() 

            msg_resumo = (
                f"✅ *Sessão Encerrada com Sucesso!*\n"
                f"Todas as abas e operações foram finalizadas.\n\n"
                f"📊 *Resultado Final da Sessão:*\n"
                f"💰 *Balanço da Conta:* ${saldo_atual:.2f}\n"
                f"📈 *Lucro Acumulado:* ${lucro_sessao:.2f}\n\n"
                f"O que deseja fazer agora?"
            )

            keyboard = [
                [InlineKeyboardButton("🔄 Nova Sessão", callback_data='session_new')],
                [InlineKeyboardButton("💤 Deixar em Espera", callback_data='session_standby')]
            ]
            
            await query.edit_message_text(
                text=msg_resumo, 
                parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        elif data == 'stop_no':
            await query.edit_message_text("▶️ Parada cancelada. A sessão atual continua operando normalmente.")
            return

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
                self.user_config.update(saved_config) 
                await self._show_final_summary(query) 
            return

        elif data == 'menu_new_config':
            self.setup_step = "account"
            self.user_config["assets"] = []
            await self.send_setup_step(query.message, is_edit=True)
            return

        elif data == 'session_standby':
            await query.edit_message_text("💤 *Robô em modo de espera.*\n\nO sistema continua online no terminal. Quando quiser operar novamente, basta digitar /start.", parse_mode='Markdown')
            return

        if self.setup_event.is_set():
            await query.edit_message_text(text="⚠️ O robô já foi inicializado e está operando.")
            return
        
        if data.startswith('back_'):
            step = data.replace('back_', '')
            
            if step == 'mode':
                self.setup_step = 'account'
            elif step == 'strategy_type':
                self.setup_step = 'mode'
            elif step == 'profile':
                if self.user_config.get("mode") == "strat_consensus":
                    self.setup_step = 'consensus_votes'
                else:
                    self.setup_step = 'strategy_type'
            elif step == 'consensus_votes':             
                self.setup_step = 'consensus_strategies'
            elif step == 'consensus_strategies':
                self.setup_step = 'strategy_type'
            elif step == 'assets':
                if self.user_config.get("mode") in ["live", "list"]:
                    self.setup_step = 'mode'
                else:
                    self.setup_step = 'timeframe' 
            elif step == 'duration':
                self.setup_step = 'assets' 
            elif step == 'timeframe':
                if self.user_config.get("profile") == "Customizado":
                    self.setup_step = 'wait_custom_params'
                else:
                    self.setup_step = 'profile'
            elif step == 'amount':
                self.setup_step = 'duration'
            elif step == 'martingale_type':
                self.setup_step = 'amount'
            elif step == 'martingale_signal_mode':
                self.setup_step = 'martingale_type'
            elif step == 'martingale_steps':
                if self.user_config.get("martingale_type") == "Sinal":
                    self.setup_step = 'martingale_signal_mode'
                else:
                    self.setup_step = 'martingale_type'
            elif step == 'martingale_multiplier':
                self.setup_step = 'martingale_steps'
            elif step == 'take_profit':
                if self.user_config.get("martingale_type") == "Nenhum":
                    self.setup_step = 'martingale_type'
                else:
                    self.setup_step = 'martingale_multiplier'
            elif step == 'stop_loss':
                self.setup_step = 'take_profit'

            await self.send_setup_step(query.message, is_edit=True)
            return

        if data.startswith('acc_'):
            self.user_config["is_demo"] = (data == 'acc_demo')
            self.setup_step = "mode"
            
        elif data.startswith('mod_'):
            modo_escolhido = data.replace('mod_', '')
            
            if modo_escolhido == "strategy_menu":
                self.setup_step = "strategy_type"
                await self.send_setup_step(query.message, is_edit=True)
                return
            else:
                self.user_config["mode"] = modo_escolhido 
                self.setup_step = "assets"
                
        elif data.startswith('strat_'):
            strat_escolhida = data.replace('strat_', '', 1)
            self.user_config["mode"] = strat_escolhida
            
            if strat_escolhida == "strat_consensus":
                self.setup_step = "consensus_strategies"
            else:
                self.setup_step = "profile"

        elif data.startswith('cstrat_'):
            strat = data.replace('cstrat_', '')
            if strat == "done":
                if len(self.user_config.get("active_strategies", [])) < 2:
                    await query.answer("Escolha pelo menos 2 estratégias para haver consenso!", show_alert=True)
                    return
                self.setup_step = "consensus_votes"
            else:
                if "active_strategies" not in self.user_config:
                    self.user_config["active_strategies"] = []
                
                if strat in self.user_config["active_strategies"]:
                    self.user_config["active_strategies"].remove(strat)
                else:
                    self.user_config["active_strategies"].append(strat)

        elif data.startswith('cvote_'):
            self.user_config["min_votes_required"] = int(data.split('_')[1])
            self.setup_step = "profile" 
                
        elif data.startswith('prof_'):
            self.user_config["profile"] = data.split('_')[1]
            
            if self.user_config["profile"] == "Customizado":
                self.setup_step = "wait_custom_params"
            else:
                self.setup_step = "timeframe"
            
        elif data.startswith('ast_'):
            asset = data.replace('ast_', '')
            if asset == "done":
                if not self.user_config["assets"]:
                    await query.answer("Escolha pelo menos 1 ativo!", show_alert=True)
                    return
                self.setup_step = "duration"
            else:
                if asset in self.user_config["assets"]:
                    self.user_config["assets"].remove(asset)
                else:
                    self.user_config["assets"].append(asset)

        elif data.startswith('tf_'):
            self.user_config["timeframe"] = data.split('_')[1]
            self.setup_step = "assets"
                    
        elif data.startswith('dur_'):
            self.user_config["duration"] = data.split('_')[1]
            self.setup_step = "amount"
            
        elif data.startswith('amt_'):
            self.user_config["amount"] = float(data.split('_')[1])
            self.setup_step = "martingale_type" 
            
        elif data.startswith('mgtype_'):
            self.user_config["martingale_type"] = data.split('_')[1]
            if self.user_config["martingale_type"] == "Nenhum":
                self.setup_step = "take_profit"
            elif self.user_config["martingale_type"] == "Sinal":
                self.setup_step = "martingale_signal_mode"
            else:
                self.setup_step = "martingale_steps"

        elif data.startswith('mgmode_'):
            self.user_config["martingale_signal_mode"] = data.split('_')[1]
            self.setup_step = "martingale_steps"

        elif data.startswith('mgstep_'):
            self.user_config["martingale_steps"] = int(data.split('_')[1])
            self.setup_step = "martingale_multiplier"

        elif data.startswith('mgmult_'):
            mult_value = data.split('_')[1]
            if mult_value == "Conservador":
                self.user_config["martingale_multiplier"] = "Conservador"
            else:
                self.user_config["martingale_multiplier"] = float(mult_value)
            self.setup_step = "take_profit"
            
        elif data.startswith('tp_'):
            self.user_config["take_profit"] = float(data.split('_')[1])
            self.setup_step = "stop_loss"

        elif data.startswith('sl_'):
            self.user_config["stop_loss"] = float(data.split('_')[1])
            await self._show_final_summary(query)
            return

        await self.send_setup_step(query.message, is_edit=True)

    async def text_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update): return
        
        if self.setup_step == "wait_custom_params":
            self.user_config["custom_params"] = update.message.text.strip()
            self.setup_step = "timeframe"
            await self.send_setup_step(update.message, is_edit=False)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update): return
        
        if not self.setup_event.is_set():
            await update.message.reply_text("⚠️ O robô ainda não foi inicializado. Use /start para iniciar uma sessão.")
            return

        status_msg = await update.message.reply_text("⏳ *Consultando dados ao vivo da corretora...*", parse_mode='Markdown')

        saldo_atual = 0.0
        if self.broker and self.broker.scraper:
            ativo_base = self.user_config["assets"][0] if self.user_config.get("assets") else None
            saldo_atual = await self.broker.scraper.get_balance(ativo_base)
        
        lucro_sessao = 0.0
        session_start = self.user_config.get("session_start")
        if session_start:
            lucro_sessao = await get_session_profit(session_start)

        mg_str = "❌ Desativado"
        if self.user_config.get('martingale_type') and self.user_config['martingale_type'] != "Nenhum":
            modo_str = f" ({self.user_config.get('martingale_signal_mode', 'Global')})" if self.user_config['martingale_type'] == "Sinal" else ""
            
            mult = self.user_config.get('martingale_multiplier', 0)
            if mult == "Conservador":
                mg_str = f"{self.user_config['martingale_type']}{modo_str} | {self.user_config.get('martingale_steps', 0)} passos | Conservador"
            else:
                mg_str = f"{self.user_config['martingale_type']}{modo_str} | {self.user_config.get('martingale_steps', 0)} passos | {mult}x"

        if self.manager and self.manager.strategies:
            active_strats = [s.name.replace('_', '\\_') for s in self.manager.strategies if getattr(s, 'is_running', False)]
            strat_msg = "\n".join([f"✅ {name}" for name in active_strats]) if active_strats else "❌ Nenhuma estratégia a rodar."
        else:
            strat_msg = "📡 Aguardando sinais do MT5 (Webhook)..." if self.user_config.get('mode') == 'live' else "❌ Nenhum gerenciador vinculado."

        tipo_conta = "DEMO 🟢" if self.user_config.get('is_demo') else "REAL 🔴"
        modo = self.user_config.get('mode', 'Desconhecido').upper().replace('_', ' ')
        perfil = self.user_config.get('profile', 'Balanceado')
        tempo = self.user_config.get('duration', '01:00')
        valor = float(self.user_config.get('amount', 1.0))
        tp = float(self.user_config.get('take_profit', 0.0))
        sl = float(self.user_config.get('stop_loss', 0.0))

        msg = (
            f"📊 *STATUS DO SISTEMA*\n\n"
            f"▫️ Conta: {tipo_conta}\n"
            f"▫️ Modo: {modo}\n"
            f"▫️ Perfil: {perfil}\n"
            f"▫️ Análise Gráfica: {self.user_config.get('timeframe', '1m')}\n"
            f"▫️ Tempo Expiração: {tempo}\n"
            f"▫️ Valor Ordem: ${valor:.2f}\n"
            f"🔄 Martingale: {mg_str}\n"
            f"🎯 Take Profit: ${tp:.2f}\n"
            f"🛑 Stop Loss: ${sl:.2f}\n\n"
            f"💰 *Balanço Atual:* ${saldo_atual:.2f}\n"
            f"📈 *Lucro da Sessão:* ${lucro_sessao:.2f}\n\n"
            f"*Estratégias Ativas:*\n{strat_msg}"
        )

        await status_msg.edit_text(text=msg, parse_mode='Markdown')

    async def estrategias_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update): return
        
        instrucoes = """
        *🤖 INSTRUÇÕES DE ESTRATÉGIAS DO BOT 🤖*
        =========================================

        *1. PRICE ACTION PRO (O Caçador de Padrões)*
        Esta estratégia observa o comportamento "nu e cru" das velas, mas usa uma bússola de longo prazo para não ser enganada por falsos movimentos.
        • *O Gatilho Visual:* Espera fechar duas velas e procura por padrões claros de reversão, como o "Engolfo" (uma vela engole o corpo da anterior), Martelo ou Estrela Cadente.
        • *A "Maré" do Mercado:* Consulta três Médias Móveis Suavizadas (SMMAs: 8, 59 e 200). A SMMA 200 dita a macro-tendência, e as curtas ditam a aceleração. Para comprar, o preço precisa estar acima da 200 e as médias alinhadas.
        • *Segurança:* Exige que a vela rompa a SMMA curta (8) com volume financeiro maior que a vela anterior.

        *2. MA CROSS PRO (O Surfista de Tendências)*
        Estratégia de cruzamento de médias (Rápida x Lenta) turbinada com filtros institucionais para evitar falsos sinais em mercados laterais.
        • *Filtro de Separação (ATR):* Não aceita um cruzamento "raspando". Exige que as médias se cruzem e se afastem com uma distância mínima baseada na volatilidade.
        • *Filtro de Rampa (Slope):* A média lenta precisa estar efetivamente "apontada" para o lado da operação. 
        • *Confirmações:* Valida se o ADX (força da tendência) é forte e se há aumento real no volume (vela de ignição).

        *3. RSI PRO (O Operador de Elástico)*
        Foca em capturar reversões após exaustão extrema do preço.
        • *Gatilho de Tensão (RSI):* Monitora o RSI para identificar quando o mercado está muito sobrevendido (abaixo de 30) ou sobrecomprado (acima de 70).
        • *Confirmação Extrema (Bandas de Bollinger):* Exige que o preço tenha perfurado as Bandas de Bollinger junto com o RSI extremo.
        • *Defesa Institucional:* Na vela de reversão, o volume precisa ser maior que o da vela anterior, sinalizando que os grandes players estão defendendo a região.

        *4. CONSENSUS STRATEGY (O Orquestrador Inteligente)*
        Atua como um coordenador que consulta os sinais das outras 3 abordagens.
        • *Votação Democrática:* Você define um número mínimo de votos (ex: 2). A ordem só é disparada se houver essa concordância na direção.
        • *Trava Anti-Conflito:* Se uma estratégia gritar "COMPRA" e outra gritar "VENDA" ao mesmo tempo, ocorre um Conflito Global e a operação é abortada imediatamente por segurança.
        """
        await update.message.reply_text(instrucoes, parse_mode='Markdown')

    async def send_alert(self, message: str):
        if not self.app or not self.admin_id: return
        try:
            await self.app.bot.send_message(chat_id=self.admin_id, text=message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Erro ao enviar alerta via Telegram: {e}")

    async def send_limit_reached_menu(self, limit_msg: str, saldo_atual: float, lucro_sessao: float):
        self.stop_session_event.set() 
        
        msg_resumo = (
            f"{limit_msg}\n\n"
            f"✅ *Todas as abas e operações foram finalizadas.*\n\n"
            f"📊 *Resultado Final da Sessão:*\n"
            f"💰 *Balanço da Conta:* ${saldo_atual:.2f}\n"
            f"📈 *Lucro Acumulado:* ${lucro_sessao:.2f}\n\n"
            f"O que deseja fazer agora?"
        )

        keyboard = [
            [InlineKeyboardButton("🔄 Nova Sessão", callback_data='session_new')],
            [InlineKeyboardButton("💤 Deixar em Espera", callback_data='session_standby')]
        ]
        
        if not self.app or not self.admin_id: return
        try:
            await self.app.bot.send_message(
                chat_id=self.admin_id, 
                text=msg_resumo, 
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Erro ao enviar menu de limite alcançado: {e}")                    

    async def start_polling(self):
        if not self.token or not self.admin_id: return
        self.app = ApplicationBuilder().token(self.token).build()
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("stop", self.stop_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CommandHandler("estrategias", self.estrategias_command))
        self.app.add_handler(CallbackQueryHandler(self.button_handler))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text_handler))
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
        mg_str = "❌ Desativado"
        if self.user_config.get('martingale_type') and self.user_config['martingale_type'] != "Nenhum":
            modo_str = f" ({self.user_config.get('martingale_signal_mode', 'Global')})" if self.user_config['martingale_type'] == "Sinal" else ""
            
            mult = self.user_config.get('martingale_multiplier', 0)
            if mult == "Conservador":
                mg_str = f"{self.user_config['martingale_type']}{modo_str} | {self.user_config.get('martingale_steps', 0)} passos | Conservador"
            else:
                mg_str = f"{self.user_config['martingale_type']}{modo_str} | {self.user_config.get('martingale_steps', 0)} passos | {mult}x"

        perfil_exibicao = self.user_config.get('profile', 'Balanceado')
        if perfil_exibicao == "Customizado" and self.user_config.get('custom_params'):
            perfil_exibicao += f" ({self.user_config['custom_params']})"

        resumo = (
            f"🚀 *SISTEMA INICIANDO!*\n\n"
            f"▫️ Conta: {'DEMO 🟢' if self.user_config['is_demo'] else 'REAL 🔴'}\n"
            f"▫️ Modo: {self.user_config['mode'].upper().replace('_', ' ')}\n"
            f"▫️ Perfil: {perfil_exibicao}\n"
            f"▫️ Análise Gráfica: {self.user_config.get('timeframe', '1m')}\n"
            f"▫️ Ativos: {', '.join(self.user_config['assets'])}\n"
            f"▫️ Tempo: {self.user_config['duration']}\n"
            f"▫️ Valor Ordem: ${self.user_config['amount']}\n"
            f"🔄 Martingale: {mg_str}\n"
            f"🎯 Take Profit: ${self.user_config['take_profit']}\n"
            f"🛑 Stop Loss: ${self.user_config['stop_loss']}\n\n"
            f"⏳ *Abrindo navegador...*"
        )
        await query.edit_message_text(text=resumo, parse_mode='Markdown')
        
        await save_user_config(self.user_config)
        
        self.setup_event.set()