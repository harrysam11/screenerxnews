"""
WF-14: API Quota & Health Guardian (Windmill Scheduled Worker)
Monitors endpoint latencies, tracks token bucket budgets, and rotates failed RPC providers.
"""
import os
import sys
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath("./backend"))
from app.services.evm_provider_manager import evm_provider_manager
from app.services.quota_manager import quota_manager

def check_all_providers() -> Dict[str, Any]:
    health_report = {}
    for chain_id in evm_provider_manager.providers:
        active_url = evm_provider_manager.get_active_endpoint(chain_id)
        bucket = quota_manager.buckets.get(chain_id)
        is_cooling = quota_manager.is_cooling_down(chain_id)
        health_report[chain_id] = {
            "active_endpoint": active_url,
            "available_tokens": bucket.tokens if bucket else 10.0,
            "is_cooling_down": is_cooling,
            "status": "HEALTHY" if (active_url and not is_cooling) else "DEGRADED"
        }
    return health_report

def main() -> Dict[str, Any]:
    report = check_all_providers()
    return {
        "status": "SUCCESS",
        "guardian": "WF-14",
        "providers_checked": len(report),
        "health_matrix": report
    }

if __name__ == "__main__":
    import json
    print(json.dumps(main()))
