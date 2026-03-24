import asyncio, logging, re
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

class HioveScraper:
    def __init__(self, email, password, is_demo=True):
        self.email = email
        self.password = password
        self.is_demo = is_demo
        self.browser = None
        self.context = None
        self.main_page = None # Usado para login e será reaproveitado
        self.playwright = None
        self.pages = {} # Dicionário para guardar a aba exclusiva de cada ativo
        self.trade_lock = asyncio.Lock() # Trava para criar uma fila de espera de cliques simultâneos
        self.last_trade_times = {} # Memória para não ler o mesmo horário de operação duas vezes
        self.keep_alive_task = None # Guarda a tarefa anti-inatividade

    async def start(self):
        """Inicia o navegador e faz login na Hiove"""
        logger.info("Iniciando o navegador do robô...")
        self.playwright = await async_playwright().start()
        
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=[
                '--start-maximized',
                '--disable-background-timer-throttling', # Impede de pausar os temporizadores do relógio
                '--disable-backgrounding-occluded-windows', # Mantém as janelas cobertas activas
                '--disable-renderer-backgrounding' # Impede de congelar o processo de renderização (vela/gráfico)
            ]
        )
        self.context = await self.browser.new_context(no_viewport=True)
        self.main_page = await self.context.new_page()
        
        try:
            logger.info("Acessando a página da Hiove...")
            await self.main_page.goto("https://app.hiove.com/auth/login") 
            await self.main_page.wait_for_load_state('networkidle')
            
            logger.info("Preenchendo credenciais...")
            await self.main_page.locator('#email').wait_for(state="visible", timeout=15000)
            await self.main_page.locator('#email').fill(self.email)
            await self.main_page.locator('#password').fill(self.password)
            
            logger.info("Clicando no botão de Login...")
            await self.main_page.locator('button[type="submit"]:has-text("Entrar")').click()
            
            await self.main_page.wait_for_load_state('networkidle')
            await self.main_page.wait_for_timeout(3000) 
            
            logger.info("✅ Login efetuado com sucesso!")
            
            
            # NOVIDADE: TRATAMENTO DO BANNER AQUI
            await self.handle_welcome_banner()
            
            await self.select_account_type(self.is_demo)
            
            # LIGA O SISTEMA ANTI-INATIVIDADE AQUI:
            self.keep_alive_task = asyncio.create_task(self._keep_tabs_alive())
            
        except PlaywrightTimeoutError:
            logger.error("❌ Timeout: A página de login demorou muito para responder.")
        except Exception as e:
            logger.error(f"❌ Erro crítico ao tentar fazer login via Scraper: {e}")

    async def _keep_tabs_alive(self):
        """
        Rotina de Fundo (Anti-Idle).
        A cada 2 minutos, navega pelas abas, move o rato e acorda o navegador
        para evitar o congelamento do relógio ou deslogue por inatividade.
        """
        logger.info("🛡️ Sistema Anti-Inatividade (Keep-Alive) iniciado.")
        while True:
            try:
                # Roda a verificação a cada 2 minutos (120 segundos)
                await asyncio.sleep(120)

                if not self.pages:
                    continue

                # ========================================================
                # SEGURANÇA MÁXIMA: Proteção do momento do disparo (Sniper)
                # ========================================================
                # O bot precisa do navegador livre na virada do minuto.
                # Se estivermos entre o segundo 45 e o segundo 10, ele não mexe em nada!
                agora = datetime.now()
                if agora.second > 45 or agora.second < 10:
                    await asyncio.sleep(15) # Espera o período crítico passar
                    continue

                logger.debug("🔄 Executando rotina Anti-Inatividade nas abas (Evitando congelamento)...")

                # Pega a trava de operações para garantir que não atrapalha um trade
                async with self.trade_lock:
                    for symbol, page in self.pages.items():
                        try:
                            # 1. Traz a aba para a frente (Força o Chrome a renderizar/descongelar)
                            await page.bring_to_front()
                            await asyncio.sleep(0.5)

                            # 2. Move o rato nativamente
                            await page.mouse.move(150, 200)
                            await asyncio.sleep(0.2)
                            await page.mouse.move(500, 350)

                            # 3. Dá um clique "fantasma" inofensivo num canto morto do cabeçalho
                            xpath_area_morta = '//*[@id="header"]'
                            if await page.locator(f'xpath={xpath_area_morta}').is_visible():
                                # Clica bem no cantinho superior esquerdo (x:5, y:5) para não acertar nenhum botão
                                await page.locator(f'xpath={xpath_area_morta}').click(position={"x": 5, "y": 5})
                            
                            await asyncio.sleep(0.5)

                        except Exception as e:
                            logger.debug(f"Aviso no Keep-Alive da aba {symbol}: {e}")

                    # Depois de acordar todas as abas, volta a deixar a tela na aba do primeiro ativo da lista
                    try:
                        primeira_pagina = list(self.pages.values())[0]
                        await primeira_pagina.bring_to_front()
                    except:
                        pass

            except asyncio.CancelledError:
                logger.info("⏹️ Sistema Anti-Inatividade encerrado com sucesso.")
                break
            except Exception as e:
                logger.debug(f"Erro menor na rotina Anti-Inatividade (Retentando em breve): {e}")
                await asyncio.sleep(60) # Pausa em caso de erro para não floodar logs

    async def handle_welcome_banner(self):
        """Verifica se o banner inicial apareceu, marca 'Não mostrar novamente' e o fecha."""
        logger.info("Verificando presença de banner inicial...")
        try:
            # Localizador da label que contém o texto "Não mostrar novamente"
            checkbox_label = self.main_page.locator('label.ant-checkbox-wrapper:has-text("Não mostrar novamente")')
            
            # Aguarda até 5 segundos para ver se o banner aparece na tela
            await checkbox_label.wait_for(state="visible", timeout=5000)
            
            logger.info("Banner detectado! Marcando a caixa 'Não mostrar novamente'...")
            await checkbox_label.click()
            await asyncio.sleep(0.5) # Pausa rápida para a interface registrar o clique
            
            # Localizador do botão de fechar (procura pela div com classe _icon-close_ ou o ícone)
            btn_close = self.main_page.locator('div[class*="_icon-close_"]').first
            
            logger.info("Fechando o banner...")
            await btn_close.click()
            await asyncio.sleep(1) # Aguarda a animação do banner sumir
            logger.info("✅ Banner fechado com sucesso.")
            
        except PlaywrightTimeoutError:
            # Se der timeout, significa que o banner não apareceu, o que é ótimo.
            logger.info("Nenhum banner inicial apareceu. Continuando...")
        except Exception as e:
            logger.warning(f"Aviso ao tentar fechar o banner (o robô continuará): {e}")

    async def select_account_type(self, is_demo: bool):
        """Troca entre a conta Demo e a Real na aba principal"""
        tipo_conta = "Conta demo" if is_demo else "Conta real"
        logger.info(f"Configurando plataforma para operar na: {tipo_conta}")
        
        try:
            await self.main_page.locator('xpath=//*[@id="header"]/div/div[2]/button[2]').click()
            await asyncio.sleep(1)
            
            await self.main_page.locator(f'li:has-text("{tipo_conta}")').first.click()
            await asyncio.sleep(1)
            
            await self.main_page.locator(f'.ant-card-bordered:has-text("{tipo_conta}")').click()
            await asyncio.sleep(0.5)
            
            await self.main_page.locator('button:has-text("Alterar tipo de conta")').click()
            
            await self.main_page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            logger.info(f"✅ Conta {tipo_conta} selecionada e confirmada!")
            
        except Exception as e:
            logger.error(f"⚠️ Erro ao tentar trocar de conta. O bot tentará continuar. Detalhes: {e}")

    async def setup_asset_page(self, symbol: str, amount: float, close_time: str, asset_type: str = "Crypto"):
        """Acessa a plataforma e deixa o ativo e os DADOS já 100% preenchidos"""
        logger.info(f"Configurando aba para o ativo {symbol}...")
        
        try:
            if not self.pages: 
                logger.info(f"Reaproveitando a aba de login para o ativo {symbol}...")
                page = self.main_page
            else:
                logger.info(f"Criando nova aba para o ativo {symbol}...")
                page = await self.context.new_page()
                await page.goto(self.main_page.url, wait_until='domcontentloaded', timeout=60000)
                await asyncio.sleep(5) 
            
            logger.info(f"Pesquisando e selecionando {symbol}...")
            await page.locator('//*[@id="header"]/div/div[1]/div[1]/button/i').click(timeout=10000)
            await asyncio.sleep(1) 
            
            await page.locator(f'button:has-text("{asset_type}")').click(timeout=10000)
            await asyncio.sleep(0.5)
            
            await page.locator('input[placeholder="Pesquisar aqui"]').fill(symbol)
            await asyncio.sleep(1.5) 
            
            await page.locator(f'text="{symbol}"').first.click(timeout=10000)
            
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except PlaywrightTimeoutError:
                pass
                
            await asyncio.sleep(2)
            
            # ==========================================
            # NOVIDADE: PRÉ-CONFIGURAÇÃO DO TEMPO E VALOR (Modo Clique)
            # ==========================================
            logger.info(f"[{symbol}] Injetando configurações iniciais (Valor: ${amount} | Tempo: {close_time})...")
            
            # --- Configurar o TEMPO usando o menu Popover da corretora ---
            xpath_tempo = '//*[@id="sider-trade"]/div/div/form/div[2]/div[1]/div/div/div/div/div/div/input'
            locator_tempo = page.locator(f'xpath={xpath_tempo}')
            await locator_tempo.wait_for(state="visible", timeout=5000)
            
            # 1. Clica no input para abrir o menu lateral
            await locator_tempo.click()
            await asyncio.sleep(1) # Dá tempo para a animação do popover abrir
            
            # 2. Procura a opção no menu popover que tem o texto exato do nosso close_time (ex: "05:00")
            # Usamos o seletor da classe .ant-menu-title-content que identificámos no HTML
            opcao_tempo = page.locator(f'.ant-popover-inner-content .ant-menu-item .ant-menu-title-content:has-text("{close_time}")')
            
            try:
                # Clica na opção do tempo
                await opcao_tempo.wait_for(state="visible", timeout=3000)
                await opcao_tempo.click()
                logger.info(f"[{symbol}] Tempo definido para {close_time} através do menu!")
            except PlaywrightTimeoutError:
                logger.warning(f"⚠️ Não foi possível encontrar a opção de tempo '{close_time}' no menu. O formato está correto (ex: '05:00')?")
            
            await asyncio.sleep(0.5)
            
            # --- Configurar o VALOR ---
            xpath_valor = '//*[@id="sider-trade"]/div/div/form/div[2]/div[2]/div/div/div/div/div/div/input'
            locator_valor = page.locator(f'xpath={xpath_valor}')
            await locator_valor.wait_for(state="visible", timeout=5000)
            
            # Limpa o campo de forma nativa via teclado
            await locator_valor.click()
            await page.keyboard.press("Control+A") 
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.1)
            
            # Formata corretamente o valor inicial
            if amount.is_integer():
                str_amount = str(int(amount))
            else:
                str_amount = str(round(amount, 2))
                
            await locator_valor.type(str_amount, delay=100)
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.5)
            
            self.pages[symbol] = page
            logger.info(f"✅ Aba do ativo {symbol} configurada e PRONTA PARA ATIRAR!")
            
        except Exception as e:
            logger.error(f"❌ Erro ao configurar aba para {symbol}: {e}")

    async def place_order(self, symbol: str, direction: str, amount: float = None):
        page = self.pages.get(symbol)
        
        if not page:
            logger.error(f"Página para {symbol} não encontrada!")
            return {"id": f"real_scraper_order_{symbol}", "payout": payout_str}
            
        async with self.trade_lock:
            try:
                await page.bring_to_front()
                await asyncio.sleep(0.1)
                
                # ==========================================
                # INJEÇÃO MARTINGALE: Atualizar valor antes de atirar
                # ==========================================
                if amount is not None:
                    xpath_valor = '//*[@id="sider-trade"]/div/div/form/div[2]/div[2]/div/div/div/div/div/div/input'
                    locator_valor = page.locator(f'xpath={xpath_valor}')
                    await locator_valor.click()
                    
                    # 1. Limpeza nativa via teclado (resolve o bloqueio do Ant Design)
                    await page.keyboard.press("Control+A") 
                    await page.keyboard.press("Backspace")
                    await asyncio.sleep(0.1)
                    
                    # 2. Verifica se o número tem casas decimais necessárias (ex: $5.0 vira "5", mas $12.5 vira "12.5")
                    # Para evitar passar o "5.0" que causa conflito.
                    if amount.is_integer():
                        str_amount = str(int(amount))
                    else:
                        str_amount = str(round(amount, 2))
                        
                    await locator_valor.type(str_amount, delay=100) 
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(0.2)

                # ==========================================
                # LER O PAYOUT ATUAL DA CORRETORA
                # ==========================================
                payout_str = "N/A"
                try:
                    # Usa uma busca focada no símbolo de percentagem dentro do painel lateral
                    xpath_payout = '//*[@id="sider-trade"]//span[contains(text(), "%")]'
                    payout_locator = page.locator(f'xpath={xpath_payout}').first
                    
                    # Aumentamos o timeout para garantir que dá tempo do site carregar a %
                    if await payout_locator.is_visible(timeout=2500):
                        payout_str = await payout_locator.inner_text()
                except Exception as e:
                    logger.debug(f"Aviso: Não foi possível ler o payout: {e}")
                
                # ==========================================
                # SINCRONIZAÇÃO COM O RELÓGIO DA CORRETORA
                # ==========================================
                try:
                    xpath_relogio = '//*[@id="root"]/div[2]/div/footer/footer/div[2]/span[2]'
                    relogio_element = page.locator(f'xpath={xpath_relogio}')
                    
                    for _ in range(15): # Tenta ler rapidamente por até 3 segundos
                        texto_relogio = await relogio_element.inner_text(timeout=1000)
                        
                        # Extrai os segundos do texto da corretora (ex: 04:06:59 -> 59)
                        match = re.search(r'(\d{2}):(\d{2}):(\d{2})', texto_relogio)
                        if match:
                            segundos = int(match.group(3))
                            
                            if segundos == 59:
                                logger.info(f"⏳ [{symbol}] Relógio da corretora em 59s. Segurando o gatilho para a virada...")
                                await asyncio.sleep(0.9) # Espera a vela abrir
                                break
                            elif segundos <= 2:
                                # Já está na abertura perfeita (segundo 00, 01, ou 02)
                                break
                            elif segundos > 55:
                                # Faltam poucos segundos, aguarda o tempo exato
                                espera = 60 - segundos
                                logger.info(f"⏳ [{symbol}] Ajuste fino: Aguardando {espera}s para a abertura...")
                                await asyncio.sleep(espera)
                                break
                            else:
                                # O sinal chegou muito atrasado. Interrompe a trava para não congelar o robô.
                                break
                                
                        await asyncio.sleep(0.2)
                except Exception as e:
                    logger.debug(f"Aviso na sincronização do relógio: {e}")
                # ==========================================

                logger.info(f"⚡ [{symbol}] SINAL SINCRONIZADO! Disparando ordem de {direction.upper()}...")
                
                if direction.upper() == "BUY":
                    btn_comprar = page.locator('#sider-trade button:has-text("Comprar")')
                    await btn_comprar.wait_for(state="visible", timeout=3000)
                    await btn_comprar.click()
                    logger.info(f"✅ [{symbol}] Ordem de COMPRA executada na abertura da vela!")
                elif direction.upper() == "SELL":
                    btn_vender = page.locator('#sider-trade button:has-text("Vender")')
                    await btn_vender.wait_for(state="visible", timeout=3000)
                    await btn_vender.click()
                    logger.info(f"✅ [{symbol}] Ordem de VENDA executada na abertura da vela!")

                return {"id": f"real_scraper_order_{symbol}"}

            except PlaywrightTimeoutError:
                logger.error(f"❌ [{symbol}] Os botões de ordem sumiram da tela.")
                return None
            except Exception as e:
                logger.error(f"❌ [{symbol}] Erro inesperado ao clicar: {e}")
                return None

    async def close(self):
        """Fecha o navegador de forma segura"""
        try:
            # NOVO: Cancela a rotina Anti-Inatividade antes de fechar
            if self.keep_alive_task and not self.keep_alive_task.done():
                self.keep_alive_task.cancel()
                
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logger.debug(f"Aviso ao encerrar o Playwright: {e}")

    async def get_balance(self, symbol: str = None) -> float:
        """Lê o saldo atual na plataforma usando a aba do ativo atual"""
        try:
            # Usa a aba do ativo se existir, senão usa a principal
            page = self.pages.get(symbol) if symbol else self.main_page
            if not page:
                page = self.main_page
                
            # Pega o contêiner exato do XPath
            xpath_saldo = '//*[@id="header"]/div/div[2]/button[2]/div/div/div/div[1]'
            elemento_saldo = page.locator(f'xpath={xpath_saldo}')
            
            await elemento_saldo.wait_for(state="visible", timeout=5000)
            texto_saldo = await elemento_saldo.inner_text()
            
            # Divide o texto e localiza o valor numérico após o '$'
            if '$' in texto_saldo:
                texto_limpo = texto_saldo.split('$')[-1].replace(',', '').strip()
                return float(texto_limpo)
            return 0.0
        except Exception as e:
            logger.error(f"⚠️ Erro ao ler saldo: {e}")
            return 0.0

    async def check_trade_result(self, symbol: str, amount: float = None, hora_sinal: str = None) -> tuple[str, float]:
        """Abre o histórico, varre a lista cruzando Ativo, Tempo e Valor. Inclui logs super detalhados para debug."""
        page = self.pages.get(symbol)
        if not page:
            logger.error(f"❌ [{symbol}] ERRO: A página do ativo não foi encontrada na memória (self.pages).")
            return "FALHOU", 0.0
            
        try:
            logger.info(f"⏳ [{symbol}] Operação finalizada. Aguardando a corretora processar...")
            
            # AUMENTO DE TENTATIVAS: Tenta 6 vezes (Dá ~18 segundos para a corretora resolver o delay)
            for tentativa in range(6):
                logger.debug(f"🔄 [{symbol}] Iniciando tentativa de leitura {tentativa + 1}/6...")
                
                # ==========================================
                # O SEGREDO DO REFRESH: Alternar as abas!
                # ==========================================
                xpath_btn_operacoes = '//*[@id="sider-trade"]/div/div/div/div[1]/button[1]'
                xpath_btn_historico = '//*[@id="sider-trade"]/div/div/div/div[1]/button[2]'
                
                try:
                    logger.debug(f"🖱️ [{symbol}] Clicando em 'Operações' para resetar a aba...")
                    await page.locator(f'xpath={xpath_btn_operacoes}').click(timeout=3000)
                    await asyncio.sleep(0.5)
                    
                    logger.debug(f"🖱️ [{symbol}] Clicando em 'Histórico' para forçar o recarregamento...")
                    await page.locator(f'xpath={xpath_btn_historico}').click(timeout=3000)
                except Exception as e:
                    logger.warning(f"⚠️ [{symbol}] Falha ao alternar abas de refresh: {e}")
                
                await asyncio.sleep(2.0) # Espera a lista nova renderizar na tela
                
                # Coleta todos os itens da lista
                xpath_itens = '//*[@id="sider-trade"]/div/div/div/div[2]//ul/li'
                itens = await page.locator(f'xpath={xpath_itens}').all()
                
                logger.debug(f"📋 [{symbol}] Foram encontradas {len(itens)} operações na lista do histórico.")
                
                if not itens:
                    logger.debug(f"⚠️ [{symbol}] A lista de histórico está vazia nesta tentativa.")
                
                for index, item in enumerate(itens):
                    try:
                        logger.debug(f"🔍 [{symbol}] Analisando linha {index + 1}...")
                        
                        # --- A. VERIFICA O ATIVO ---
                        elemento_titulo = item.locator('h4.ant-list-item-meta-title span')
                        if not await elemento_titulo.is_visible(): 
                            logger.debug(f"⏭️ [{symbol}] Linha {index + 1} ignorada: Título do ativo não visível.")
                            continue
                            
                        texto_ativo = await elemento_titulo.inner_text()
                        if texto_ativo != symbol:
                            logger.debug(f"⏭️ [{symbol}] Linha {index + 1} ignorada: Ativo diferente. Encontrado '{texto_ativo}', procurado '{symbol}'.")
                            continue
                            
                        # --- B. VERIFICA A MEMÓRIA (ANTI-REPETIÇÃO) ---
                        elemento_tempo = item.locator('.ant-list-item-meta-description span').first
                        texto_tempo = await elemento_tempo.inner_text() 
                        
                        if self.last_trade_times.get(symbol) == texto_tempo:
                            logger.debug(f"⏭️ [{symbol}] Linha {index + 1} ignorada: Tempo '{texto_tempo}' já está na memória (Esta é a operação passada!).")
                            continue 

                        # --- C. VERIFICA O TEMPO DO SINAL (REGRA DOS 30 SEGUNDOS) ---
                        if hora_sinal:
                            try:
                                from datetime import datetime, timedelta
                                hora_obj = datetime.strptime(hora_sinal, "%H:%M:%S")
                                if hora_obj.second <= 30:
                                    minuto_esperado = hora_obj.strftime("%H:%M")
                                else:
                                    minuto_esperado = (hora_obj + timedelta(minutes=1)).strftime("%H:%M")
                                
                                if minuto_esperado not in texto_tempo:
                                    logger.debug(f"⏭️ [{symbol}] Linha {index + 1} ignorada: Regra de tempo falhou. O texto da corretora '{texto_tempo}' não contém o minuto esperado '{minuto_esperado}'.")
                                    continue
                            except Exception as e:
                                logger.error(f"❌ [{symbol}] Erro interno ao calcular a regra dos 30 segundos: {e}")
                            
                        # --- D. VERIFICA O VALOR (ANTI-MARTINGALE FALSO) ---
                        elemento_valor = item.locator('h5')
                        texto_valor = await elemento_valor.inner_text() 
                        classes_css = await elemento_valor.get_attribute('class')
                        
                        try:
                            lucro_bruto = float(texto_valor.replace('$', '').replace(',', '').strip())
                        except ValueError:
                            logger.error(f"❌ [{symbol}] Erro ao converter o texto de valor '{texto_valor}' para número na linha {index + 1}.")
                            continue
                        
                        if amount is not None and 'ant-typography-danger' in classes_css:
                            if abs(lucro_bruto) != float(amount):
                                logger.debug(f"⏭️ [{symbol}] Linha {index + 1} ignorada: Falso LOSS de Martingale. Valor da perda (${abs(lucro_bruto)}) é diferente do investido (${amount}).")
                                continue 

                        # --- DEFINIÇÃO DO RESULTADO FINAL ---
                        if 'ant-typography-success' in classes_css:
                            status = "WIN"
                        elif 'ant-typography-danger' in classes_css:
                            status = "LOSS"
                        elif 'ant-typography-secondary' in classes_css:
                            status = "EMPATE"
                        else:
                            logger.warning(f"⚠️ [{symbol}] Classes CSS desconhecidas na linha {index + 1}: {classes_css}")
                            status = "DESCONHECIDO"

                        logger.info(f"✅ [{symbol}] HISTÓRICO CONFIRMADO! -> Ativo: {texto_ativo} | Tempo: {texto_tempo} | Valor: {texto_valor} | Status: {status}")
                        
                        # Salva na memória
                        self.last_trade_times[symbol] = texto_tempo

                        # Tenta fechar o Histórico voltando para a aba Operações
                        try:
                            await page.locator(f'xpath={xpath_btn_operacoes}').click(timeout=5000)
                            logger.debug(f"🖱️ [{symbol}] Voltou para a aba Operações com sucesso após leitura.")
                        except Exception as e: 
                            logger.debug(f"⚠️ [{symbol}] Não foi possível voltar à aba Operações no fechamento: {e}")
                            pass
                            
                        await asyncio.sleep(0.5)
                        
                        return status, lucro_bruto

                    except Exception as e:
                        logger.error(f"❌ [{symbol}] Falha inesperada ao tentar ler e decodificar a linha {index + 1}: {e}")
                        continue
                        
                logger.info(f"🔄 [{symbol}] Nenhum resultado compatível na tentativa {tentativa + 1}. Aguardando...")
                
            logger.error(f"🛑 [{symbol}] ESGOTADO! O resultado não apareceu no histórico após 6 tentativas cruzando os dados.")
            try:
                xpath_btn_operacoes = '//*[@id="sider-trade"]/div/div/div/div[1]/button[1]'
                await page.locator(f'xpath={xpath_btn_operacoes}').click(timeout=3000)
            except: 
                pass
            
            return "ERRO_LEITURA", 0.0

        except Exception as e:
            logger.error(f"💥 [{symbol}] ERRO CRÍTICO GLOBAL na função check_trade_result: {e}", exc_info=True)
            return "ERRO_LEITURA", 0.0