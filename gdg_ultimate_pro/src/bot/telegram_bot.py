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
        
        # === VARIÁVEIS DO NOVO QUESTIONÁRIO ===
        self.custom_param_index = 0
        self.temp_custom_params = []
        self.custom_questions = []
        # ======================================
        
        self.user_config = {
            "is_demo": True,
            "mode": "strategy",
            "active_strategies": ["rsi", "ma", "engulf"],
            "min_votes_required": 2,
            "profile": "Balanceado",
            "active_filters": {"adx": True, "volume": True, "trend": True, "bb": True, "atr": True, "slope": True}, 
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

        elif self.setup_step == "custom_params_q":
            idx = self.custom_param_index
            pergunta, padrao, tipo, explicacao, strat_key = self.custom_questions[idx]
            
            # Adiciona um aviso visual caso o usuário esteja no modo Consenso
            modo_atual = self.user_config.get("mode", "strategy")
            tag_contexto = f" 🏷️ Estratégia: *{strat_key.upper()}*\n" if modo_atual == "strat_consensus" else ""
            
            text = (
                f"✍️ *Configuração Customizada [{idx+1}/{len(self.custom_questions)}]*\n\n"
                f"{tag_contexto}"
                f"🔹 *{pergunta}*\n"
                f"💡 _O que faz:_ {explicacao}\n\n"
                f"👉 Valor Padrão Recomendado: `{padrao}`\n\n"
                f"Digite o valor desejado no chat ou clique no botão abaixo para manter o padrão:"
            )
            keyboard = [
                [InlineKeyboardButton(f"✅ Manter Padrão ({padrao})", callback_data='custom_keep_default')],
                [InlineKeyboardButton("⬅️ Cancelar e Recomeçar", callback_data='back_profile')]
            ]

        elif self.setup_step == "filters_menu":
            text = "🎛️ *Filtros Institucionais*\nAtive ou desative os filtros de segurança específicos da sua estratégia.\nClique para alterar (✅ = Ligado / ❌ = Desligado) e depois em Continuar:"
            
            # Garante que o dicionário de filtros exista com os valores padrão
            f = self.user_config.setdefault("active_filters", {"adx": True, "volume": True, "trend": True, "bb": True, "atr": True, "slope": True})
            
            # 1) Mapeamento de quais filtros pertencem a cada estratégia
            modo_atual = self.user_config.get("mode", "strategy")
            filtros_visiveis = []

            if modo_atual == "strat_engulf":
                filtros_visiveis = ["adx", "volume", "trend"]
            elif modo_atual == "strat_ma":
                filtros_visiveis = ["adx", "volume", "atr", "slope"]
            elif modo_atual == "strat_rsi":
                filtros_visiveis = ["bb", "volume"]
            elif modo_atual == "strat_consensus":
                # No consenso, mostramos os filtros combinados de todas as lógicas ativas na votação
                ativas = self.user_config.get("active_strategies", [])
                if "rsi" in ativas:
                    filtros_visiveis.extend(["bb", "volume"])
                if "ma" in ativas:
                    filtros_visiveis.extend(["adx", "volume", "atr", "slope"])
                if "engulf" in ativas:
                    filtros_visiveis.extend(["adx", "volume", "trend"])
                # Remove chaves duplicadas mantendo a organização visual
                filtros_visiveis = list(dict.fromkeys(filtros_visiveis))
            else:
                # Fallback de segurança para exibir tudo caso seja um modo genérico/Live
                filtros_visiveis = ["adx", "volume", "trend", "bb", "atr", "slope"]

            # Nomes de exibição amigáveis para cada chave técnica
            nomes_filtros = {
                "adx": "ADX (Força)",
                "volume": "Volume",
                "trend": "Macro Tendência",
                "bb": "Bollinger (RSI)",
                "atr": "Separação ATR",
                "slope": "Inclinação MA"
            }

            # 2) Constrói a lista de botões ativos sequencialmente
            botoes_dinamicos = []
            for filtro_key in filtros_visiveis:
                status_icon = "✅" if f.get(filtro_key, True) else "❌"
                label_botao = f"{status_icon} {nomes_filtros[filtro_key]}"
                botoes_dinamicos.append(InlineKeyboardButton(label_botao, callback_data=f'flt_{filtro_key}'))

            # 3) Organiza os botões em linhas de 2 em 2 automaticamente
            keyboard = []
            for i in range(0, len(botoes_dinamicos), 2):
                keyboard.append(botoes_dinamicos[i:i+2])

            # Adiciona os botões de controle de navegação no final do menu
            keyboard.append([
                InlineKeyboardButton("⬅️ Voltar", callback_data='back_filters_menu'), 
                InlineKeyboardButton("➡️ Continuar", callback_data='flt_done')
            ])

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
                self.setup_step = 'filters_menu' 
            elif step == 'filters_menu':
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
                modo_atual = self.user_config.get("mode", "strategy")
                self.custom_questions = []
                
                # Definição das listas de perguntas (Adicionado o 5º parâmetro para identificar a estratégia correspondente)
                questions_rsi = [
                    ("Período do RSI", "14", "int", "Define a quantidade de velas anteriores que o indicador RSI vai analisar para medir a velocidade e a mudança dos movimentos de preço.", "rsi"),
                    ("Nível de Sobrecompra (Teto)", "70", "int", "O limite máximo do RSI. Acima deste valor, o preço é considerado caro demais (exaustão de compradores) e o robô buscará Gatilhos de Venda.", "rsi"),
                    ("Nível de Sobrevenda (Piso)", "30", "int", "O limite mínimo do RSI. Abaixo deste valor, o preço é considerado barato demais (exaustão de vendedores) e o robô buscará Gatilhos de Compra.", "rsi"),
                    ("Período da SMMA Longa (Macro)", "100", "int", "Média móvel de longo prazo institucional. Atua como bússola: o robô só compra se o preço estiver acima dela e só vende se estiver abaixo.", "rsi"),
                    ("Período da EMA Curta", "9", "int", "Média móvel rápida usada para rastrear micro-tendências e desvios imediatos do preço atual em confluência com o RSI.", "rsi"),
                    ("Nível Mínimo do ADX (Força)", "20", "int", "Garante que o mercado tenha força direcional. Valores baixos evitam que o robô envie ordens quando o mercado estiver totalmente parado de lado.", "rsi"),
                    ("Multiplicador de Volatilidade", "0.7", "float", "Exige que o tamanho total da vela de sinal seja pelo menos 'X' vezes maior que a volatilidade média das últimas 10 velas.", "rsi"),
                    ("Multiplicador de Volume (Ignição)", "1.0", "float", "Exige que o volume financeiro da vela de sinal seja forte, confirmando a entrada de capital institucional a mercado.", "rsi"),
                    ("Desvio Padrão Bollinger", "2.0", "float", "Controla a largura das Bandas. Valores maiores exigem que o preço estique mais agressivamente para fora das bandas para validar a exaustão.", "rsi")
                ]
                
                questions_ma = [
                    ("Período EMA Rápida", "9", "int", "Média móvel de curto prazo que acompanha o preço de perto para detecção imediata de viradas de fluxo.", "ma"),
                    ("Período EMA Lenta", "21", "int", "Média móvel de médio prazo. O cruzamento da EMA Rápida sobre esta EMA Lenta determina a mudança oficial da tendência.", "ma"),
                    ("Período da SMMA Longa (Macro)", "100", "int", "Média protetora institucional. O robô irá ignorar cruzamentos de médias se eles forem contra a direção desta macro-tendência.", "ma"),
                    ("Cooldown (Velas de espera)", "3", "int", "Número de velas que o robô deve esperar obrigatoriamente após abrir uma ordem antes de poder analisar um novo sinal neste mesmo ativo.", "ma"),
                    ("Multiplicador ATR (Afastamento)", "0.15", "float", "Usa o indicador ATR (volatilidade) para exigir que as médias se cruzem e se seprem por uma distância segura, filtrando cruzamentos 'falsos' em mercados travados.", "ma"),
                    ("Nível Mínimo do ADX (Força)", "20", "int", "Filtro de tendência. Evita que o robô compre ou venda cruzamentos de médias que ocorram durante consolidações/mercados laterais.", "ma"),
                    ("Multiplicador de Volume (Ignição)", "1.0", "float", "Exige que a vela que gerou o cruzamento venha acompanhada de forte volume de injeção financeira institucional.", "ma"),
                    ("RSI Máximo para Comprar", "65", "int", "Filtro de segurança. Impede que o robô compre um cruzamento de alta se o mercado já estiver esticado demais no topo (sobrecomprado).", "ma"),
                    ("RSI Mínimo para Vender", "35", "int", "Filtro de segurança. Impede que o robô venda um cruzamento de baixa se o preço já estiver esticado demais no fundo (sobrevendido).", "ma")
                ]
                
                questions_engulf = [
                    ("Período da SMMA Curta (Aceleração)", "8", "int", "Média móvel de curtíssimo prazo usada para validar a proximidade, o toque de retorno ou o rompimento imediato do preço.", "engulf"),
                    ("Período da SMMA Média (Tendência)", "59", "int", "Média intermediária usada para garantir que a tendência de médio prazo apoia a reversão gráfica identificada.", "engulf"),
                    ("Período da SMMA Longa (Macro)", "200", "int", "A grande média institucional. Define a maré principal do ativo para garantir que você nunca opere contra os grandes players mundiais.", "engulf"),
                    ("Nível Mínimo do ADX (Força)", "18", "int", "Evita que o robô opere padrões de Price Action (como engolfos e martelos) quando o mercado estiver sem direção, dentro de caixotes estreitos.", "engulf"),
                    ("Multiplicador de Volatilidade (Tamanho)", "0.8", "float", "Exige que o tamanho total da vela do padrão gráfico tenha pelo menos 'X' % do tamanho médio das últimas 10 velas.", "engulf"),
                    ("Teto Máximo do RSI para COMPRA", "60", "int", "Bloqueia ordens de compra se o RSI estiver acima deste valor, evitando que você compre topo logo antes de um pullback.", "engulf"),
                    ("Piso Mínimo do RSI para VENDA", "40", "int", "Bloqueia ordens de venda se o RSI estiver abaixo deste valor, evitando que você venda fundo bem em cima de suportes históricos.", "engulf"),
                    ("Multiplicador de Volume Mínimo", "1.0", "float", "Para padrões de força (Engolfo, Marubozu, Cinturão), exige que o volume financeiro supere a vela anterior para confirmar o interesse real de reversão.", "engulf")
                ]
                
                if modo_atual == "strat_rsi":
                    self.custom_questions = questions_rsi
                elif modo_atual == "strat_ma":
                    self.custom_questions = questions_ma
                elif modo_atual == "strat_engulf":
                    self.custom_questions = questions_engulf
                elif modo_atual == "strat_consensus":
                    # Monta um super-questionário sequencial com base nas estratégias ativas na votação
                    ativas = self.user_config.get("active_strategies", [])
                    if "rsi" in ativas: self.custom_questions.extend(questions_rsi)
                    if "ma" in ativas: self.custom_questions.extend(questions_ma)
                    if "engulf" in ativas: self.custom_questions.extend(questions_engulf)
                else:
                    self.custom_questions = questions_engulf
                    
                self.setup_step = "custom_params_q"
                self.custom_param_index = 0
                self.temp_custom_params = []
            else:
                self.setup_step = "filters_menu"

        elif data == 'custom_keep_default':
            padrao = self.custom_questions[self.custom_param_index][1]
            self.temp_custom_params.append(padrao)
            self.custom_param_index += 1
            
            if self.custom_param_index >= len(self.custom_questions):
                modo_atual = self.user_config.get("mode", "strategy")
                
                if modo_atual == "strat_consensus":
                    dict_params = {}
                    for idx_q, val_ans in enumerate(self.temp_custom_params):
                        s_key = self.custom_questions[idx_q][4]
                        dict_params.setdefault(s_key, []).append(val_ans)
                    # Grava como um dicionário de strings estruturadas
                    self.user_config["custom_params"] = {k: ", ".join(v) for k, v in dict_params.items()}
                else:
                    self.user_config["custom_params"] = ", ".join(self.temp_custom_params)
                    
                self.setup_step = "filters_menu"

        elif data.startswith('flt_'):
            comando = data.replace('flt_', '')
            if comando == 'done':
                self.setup_step = "timeframe"
            else:
                # Inverte o valor do botão clicado (True vira False, False vira True)
                self.user_config["active_filters"][comando] = not self.user_config["active_filters"][comando]
                # Não muda o setup_step, a tela será recarregada no mesmo lugar
            
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
        
        if self.setup_step == "custom_params_q":
            texto = update.message.text.strip().replace(',', '.')
            idx = self.custom_param_index
            _, _, tipo, _, strat_key = self.custom_questions[idx]
            
            try:
                if tipo == "int":
                    val = str(int(texto))
                else:
                    val = str(float(texto))
                
                self.temp_custom_params.append(val)
                self.custom_param_index += 1
                
                if self.custom_param_index >= len(self.custom_questions):
                    modo_atual = self.user_config.get("mode", "strategy")
                    if modo_atual == "strat_consensus":
                        dict_params = {}
                        for idx_q, val_ans in enumerate(self.temp_custom_params):
                            s_key = self.custom_questions[idx_q][4]
                            dict_params.setdefault(s_key, []).append(val_ans)
                        self.user_config["custom_params"] = {k: ", ".join(v) for k, v in dict_params.items()}
                    else:
                        self.user_config["custom_params"] = ", ".join(self.temp_custom_params)
                        
                    self.setup_step = "filters_menu"
                    await self.send_setup_step(update.message, is_edit=False)
                else:
                    await self.send_setup_step(update.message, is_edit=False)
                    
            except ValueError:
                tipo_str = "número inteiro (ex: 8)" if tipo == "int" else "número decimal (ex: 1.5)"
                await update.message.reply_text(f"⚠️ Valor inválido! Por favor, digite um {tipo_str}.")
                
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
            cp = self.user_config['custom_params']
            if isinstance(cp, dict):
                perfil_exibicao += " [Consenso Customizado ⚙️]"
            else:
                perfil_exibicao += f" ({cp})"

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