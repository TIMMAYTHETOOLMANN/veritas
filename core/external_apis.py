#!/usr/bin/env python3
"""
core/external_apis.py — External API integrations for VERITAS.

Integrates:
- Alchemy: Primary RPC endpoint (reliable, high throughput)
- crypto-arbitrage: Arbitrage opportunity signals
- defillama: Protocol/TVL data for pool metadata

Excluded: crypto-recovery-expert (suspicious endpoint name)
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict, List, Optional


# ---- Configuration ----

ALCHEMY_KEY = "alch_VNgR_d3fLq-3WDpDb7_Ol"
ALCHEMY_ARBITRUM_URL = f"https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"

RAPIDAPI_KEY = "6af8fc9ed1msh37b65cbd5a9ba15p1c05a6jsn52c4beedb930"


# ---- Alchemy RPC ----

class AlchemyRPC:
    """
    Alchemy RPC client for reliable Arbitrum access.
    
    Usage:
        rpc = AlchemyRPC()
        block = rpc.eth_blockNumber()
        result = rpc.eth_call(address, data)
    """
    
    def __init__(self, api_key: str = ALCHEMY_KEY, chain: str = "arbitrum"):
        self.api_key = api_key
        self.chain = chain
        if chain == "arbitrum":
            self.url = f"https://arb-mainnet.g.alchemy.com/v2/{api_key}"
        else:
            self.url = f"https://eth-mainnet.g.alchemy.com/v2/{api_key}"
        self._request_count = 0
        self._error_count = 0
    
    def _call(self, method: str, params: list) -> Any:
        """Make a JSON-RPC call."""
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": self._request_count + 1,
            "method": method,
            "params": params,
        }).encode()
        
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        
        self._request_count += 1
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                if "error" in data:
                    self._error_count += 1
                    raise RuntimeError(f"RPC error: {data['error']}")
                return data.get("result")
        except Exception as e:
            self._error_count += 1
            raise
    
    def eth_blockNumber(self) -> int:
        """Get current block number."""
        result = self._call("eth_blockNumber", [])
        return int(result, 16) if result else 0
    
    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        """Make an eth_call."""
        result = self._call("eth_call", [{"to": to, "data": data}, block])
        return result or "0x"
    
    def stats(self) -> dict:
        """Return usage statistics."""
        return {
            "requests": self._request_count,
            "errors": self._error_count,
            "error_rate": round(self._error_count / max(1, self._request_count), 4),
        }


# ---- Crypto Arbitrage API ----

class CryptoArbitrageAPI:
    """
    Crypto arbitrage opportunity data from RapidAPI.
    """
    
    def __init__(self, api_key: str = RAPIDAPI_KEY):
        self.api_key = api_key
        self.host = "crypto-arbitrage3.p.rapidapi.com"
        self.base_url = f"https://{self.host}"
        self._request_count = 0
        self._error_count = 0
    
    def _call(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """Make a RapidAPI GET request."""
        import urllib.parse
        
        url = f"{self.base_url}/{endpoint}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
        
        req = urllib.request.Request(
            url,
            headers={
                "Content-Type": "application/json",
                "x-rapidapi-host": self.host,
                "x-rapidapi-key": self.api_key,
            },
        )
        
        self._request_count += 1
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                return data
        except Exception as e:
            self._error_count += 1
            return None
    
    def get_opportunities(self, exchange: str = "coinmetro", coin: str = "btc") -> List[dict]:
        """Get arbitrage opportunities."""
        result = self._call("crypto-arbitrage", {"exchange": exchange, "coin": coin})
        if isinstance(result, list):
            return result
        elif isinstance(result, dict) and "data" in result:
            return result["data"]
        return []
    
    def stats(self) -> dict:
        """Return usage statistics."""
        return {
            "requests": self._request_count,
            "errors": self._error_count,
        }


# ---- DeFiLlama API ----

class DeFiLlamaAPI:
    """
    DeFi protocol data from RapidAPI.
    """
    
    def __init__(self, api_key: str = RAPIDAPI_KEY):
        self.api_key = api_key
        self.host = "defillama-crypto-data-api.p.rapidapi.com"
        self.base_url = f"https://{self.host}"
        self._request_count = 0
        self._error_count = 0
    
    def _call(self, endpoint: str, params: dict = None) -> Optional[Any]:
        """Make a RapidAPI GET request."""
        import urllib.parse
        
        url = f"{self.base_url}/{endpoint}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
        
        req = urllib.request.Request(
            url,
            headers={
                "Content-Type": "application/json",
                "x-rapidapi-host": self.host,
                "x-rapidapi-key": self.api_key,
            },
        )
        
        self._request_count += 1
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                return data
        except Exception as e:
            self._error_count += 1
            return None
    
    def get_protocols(self) -> List[dict]:
        """Get list of DeFi protocols."""
        result = self._call("protocols")
        if isinstance(result, list):
            return result
        return []
    
    def stats(self) -> dict:
        """Return usage statistics."""
        return {
            "requests": self._request_count,
            "errors": self._error_count,
        }
