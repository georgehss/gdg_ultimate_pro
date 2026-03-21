from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    def __init__(self, name: str, broker, telegram_alert_cb):
        """
        :param name: Nome de identificação da estratégia.
        :param broker: Instância da HioveBrokerAPI para enviar ordens.
        :param telegram_alert_cb: Função de callback para enviar mensagens pro Telegram.
        """
        self.name = name
        self.broker = broker
        self.telegram_alert = telegram_alert_cb
        self.is_running = False

    @abstractmethod
    async def analyze_market(self):
        """
        Lógica para analisar os dados do mercado e gerar sinais.
        DEVE ser obrigatoriamente implementada na estratégia filha.
        """
        pass

    @abstractmethod
    async def execute(self):
        """
        O loop principal de execução da estratégia.
        DEVE ser obrigatoriamente implementada na estratégia filha.
        """
        pass