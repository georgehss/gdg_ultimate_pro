import aiosqlite
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# --- CONFIGURAÇÃO DO CAMINHO DA BASE DE DADOS ---
# __file__ é o caminho deste ficheiro (src/core/database.py)
# .parent.parent.parent volta 3 níveis: core -> src -> raiz_do_projeto
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"

# Garante que a pasta 'data' existe. Se não existir, o Python cria-a automaticamente.
DATA_DIR.mkdir(parents=True, exist_ok=True)

# O caminho final será absoluto e apontará sempre para 'data/trading_bot.db'
DB_NAME = str(DATA_DIR / "trading_bot.db")
# ------------------------------------------------

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Adicionei a coluna 'profit' no final
        await db.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                direction TEXT,
                amount REAL,
                close_time TEXT,
                status TEXT,
                order_id TEXT,
                profit REAL
            )
        ''')
        await db.commit()
        logger.info("🗄️ Base de dados inicializada com sucesso.")

async def log_trade(symbol: str, direction: str, amount: float, close_time: str, status: str, order_id: str = None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''
                INSERT INTO trades (timestamp, symbol, direction, amount, close_time, status, order_id, profit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (timestamp, symbol, direction, amount, close_time, status, order_id, 0.0))
            await db.commit()
    except Exception as e:
        logger.error(f"❌ Erro ao guardar trade: {e}")

async def update_trade_result(order_id: str, new_status: str, profit: float):
    """Atualiza a ordem de ABERTA para WIN ou LOSS e regista o valor, protegendo o histórico."""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            # A subquery localiza apenas o ID (chave primária) da ordem mais recente que está ABERTA
            await db.execute('''
                UPDATE trades 
                SET status = ?, profit = ? 
                WHERE id = (
                    SELECT id FROM trades 
                    WHERE order_id = ? AND status = 'ABERTA' 
                    ORDER BY id DESC LIMIT 1
                )
            ''', (new_status, profit, order_id))
            await db.commit()
            logger.info(f"💾 Resultado guardado: {order_id} | {new_status} | Lucro: ${profit}")
    except Exception as e:
        logger.error(f"❌ Erro ao atualizar resultado na base de dados: {e}")

async def get_session_profit(session_start_time: str) -> float:
    """Calcula o lucro/prejuízo total apenas da sessão atual"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            # Soma a coluna 'profit' onde o timestamp for maior ou igual à hora de início da sessão
            cursor = await db.execute('''
                SELECT SUM(profit) FROM trades 
                WHERE timestamp >= ? AND status IN ('WIN', 'LOSS')
            ''', (session_start_time,))
            row = await cursor.fetchone()
            
            # Se não houver operações nesta sessão, o lucro é 0.0
            return row[0] if row and row[0] is not None else 0.0
    except Exception as e:
        logger.error(f"❌ Erro ao calcular lucro da sessão: {e}")
        return 0.0