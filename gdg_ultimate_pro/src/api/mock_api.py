from fastapi import FastAPI, Header, Request
import time
import random

app = FastAPI(title="Hiove Mock API")

# 1. Endpoint: Fetch All Wallets
@app.get("/token/wallets")
def get_wallets(api_token: str = Header(None, alias="api-token")):
    return [
        {
            "id": "mock_wallet_123",
            "userId": "user_001",
            "type": "DEMO",
            "balance": 10000.00,
            "createdAt": "2024-03-04T10:15:30Z"
        }
    ]

# 2. Endpoint: Get Aggregated Prices (Gera velas simuladas)
@app.get("/aggregated-prices/prices")
def get_aggregated_prices(
    pair: str,
    interval: str,
    limit: int = 50,
    startTime: int = None,
    endTime: int = None,
    api_key: str = Header(None, alias="api-key")
):
    klines = []
    # Define um preço base dependendo do ativo
    base_price = 1.0850 if "EUR" in pair else 65000.0
    current_time = endTime if endTime else int(time.time() * 1000)
    start_time = current_time - (limit * 60 * 1000) 
    
    # Gera o histórico de velas com flutuação randômica
    for i in range(limit):
        variation = random.uniform(-0.0010, 0.0010) if "EUR" in pair else random.uniform(-50, 50)
        close_price = base_price + variation
        klines.append({
            "openPrice": base_price,
            "closePrice": close_price,
            "highPrice": max(base_price, close_price) + abs(variation/2),
            "lowPrice": min(base_price, close_price) - abs(variation/2),
            "time": start_time + (i * 60 * 1000),
            "volume": random.randint(10, 100)
        })
        base_price = close_price # o fechamento atual é a abertura do próximo
        
    return klines

# 3. Endpoint: Open Trade Order
@app.post("/token/trades/open")
async def open_trade(request: Request):
    data = await request.json()
    return {
        "id": f"mock_trade_{random.randint(1000, 9999)}",
        "result": "PENDING",
        "symbol": data.get("symbol", "UNKNOWN")
    }