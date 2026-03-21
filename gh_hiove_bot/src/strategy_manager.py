import asyncio
import logging

logger = logging.getLogger(__name__)

class StrategyManager:
    def __init__(self):
        self.strategies = []
        self.tasks = []

    def add_strategy(self, strategy):
        """Adiciona uma estratégia instanciada à lista do gerenciador."""
        self.strategies.append(strategy)
        logger.info(f"Estratégia '{strategy.name}' adicionada ao gerenciador.")

    async def start_all(self):
        """Inicia o loop de execução (execute) de todas as estratégias em paralelo."""
        if not self.strategies:
            logger.warning("Nenhuma estratégia configurada para iniciar.")
            return

        logger.info("Iniciando todas as estratégias em background...")
        for strat in self.strategies:
            # Cria uma "thread" assíncrona para cada estratégia não bloquear a outra
            task = asyncio.create_task(strat.execute())
            self.tasks.append(task)
        
        # Aguarda todas as tarefas (loop infinito enquanto estiverem rodando)
        await asyncio.gather(*self.tasks)

    async def stop_all(self):
        """Para todas as estratégias graciosamente."""
        logger.info("Recebido comando para parar todas as estratégias.")
        for strat in self.strategies:
            strat.is_running = False
        
        # Cancela as tarefas no Event Loop
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()
        logger.info("Todas as estratégias foram paradas.")