"""
summary: Windmill Primary Strategy Execution Bridge (WF-22)
description: Receives a persistent strategy proposal ID and invokes the Core authorization bridge via in-process call or authenticated HTTP dispatch.
schema:
  type: object
  properties:
    proposal_id:
      type: string
      description: Persistent strategy proposal ID
    account_id:
      type: string
      description: Target desk account ID
    core_api_url:
      type: string
      default: ""
      description: Optional Core API Base URL for HTTP dispatch (e.g. http://127.0.0.1:8000)
    api_token:
      type: string
      default: ""
      description: Optional administrative API token for remote dispatch
    timeout_seconds:
      type: number
      default: 10.0
      description: Request timeout for HTTP dispatch
"""

import os
import sys
import httpx
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.abspath("./backend"))
from app.core.config import settings

# Enforce strict paper-only boundary
if getattr(settings, "ALLOW_LIVE_TRADING", False):
    raise RuntimeError("Live execution is strictly prohibited. ALLOW_LIVE_TRADING must be False.")


def execute_proposal_workflow(
    proposal_id: str,
    account_id: str,
    core_api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    timeout_seconds: float = 10.0,
    paper_service: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Executes a persistent proposal through Core authorization bridge.
    Supports:
    1. HTTP dispatch when core_api_url is provided, with timeout recovery and idempotent status reconciliation.
    2. In-process Core execution when running locally without core_api_url.
    Windmill orchestrates the call but never accesses SQLite tables or writes ledger entries directly.
    """
    # 1. HTTP Dispatch Mode
    if core_api_url and core_api_url.strip():
        base_url = core_api_url.rstrip("/")
        endpoint = f"{base_url}/api/v1/strategies/proposals/{proposal_id}/execute"
        token = api_token or os.environ.get("ADMIN_API_TOKEN") or getattr(settings, "ADMIN_API_TOKEN", None)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Admin-Token"] = token
            headers["X-API-Key"] = token
            headers["Authorization"] = f"Bearer {token}"

        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                resp = client.post(endpoint, json={"account_id": account_id}, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "status": "SUCCESS",
                        "order_id": data.get("order_id"),
                        "client_order_id": data.get("client_order_id"),
                        "execution_status": data.get("status"),
                        "side": data.get("side"),
                        "chain_id": data.get("chain_id"),
                        "output_amount_raw": float(data["output_amount_raw"]) if data.get("output_amount_raw") is not None else None,
                        "effective_price_native": float(data["effective_price_native"]) if data.get("effective_price_native") is not None else None,
                        "created_at": data.get("created_at")
                    }
                elif resp.status_code == 401:
                    detail = resp.json().get("detail", "Unauthorized") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
                    return {
                        "status": "UNAUTHORIZED",
                        "error_type": "AUTHENTICATION_FAILED",
                        "message": detail,
                        "proposal_id": proposal_id,
                        "account_id": account_id
                    }
                elif resp.status_code == 403:
                    detail = resp.json().get("detail", "Forbidden") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
                    return {
                        "status": "FORBIDDEN",
                        "error_type": "PERMISSION_DENIED",
                        "message": detail,
                        "proposal_id": proposal_id,
                        "account_id": account_id
                    }
                elif resp.status_code == 400:
                    detail = resp.json().get("detail", "Bad Request") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
                    status_cat = "EXPIRED" if "expired" in detail.lower() else ("RISK_REJECTED" if "risk" in detail.lower() else "VALIDATION_FAILED")
                    return {
                        "status": status_cat,
                        "error_type": "VALIDATION_FAILED",
                        "message": detail,
                        "proposal_id": proposal_id,
                        "account_id": account_id
                    }
                else:
                    detail = resp.text
                    return {
                        "status": "FAILED",
                        "error_type": "INTERNAL_ERROR",
                        "message": f"HTTP {resp.status_code}: {detail}",
                        "proposal_id": proposal_id,
                        "account_id": account_id
                    }
        except httpx.TimeoutException:
            # Ambiguous timeout recovery: query proposal status idempotently
            try:
                status_url = f"{base_url}/api/v1/strategies/proposals/{proposal_id}"
                with httpx.Client(timeout=5.0) as verify_client:
                    v_resp = verify_client.get(status_url, headers=headers)
                    if v_resp.status_code == 200:
                        prop = v_resp.json()
                        if prop.get("execution_status") == "CONSUMED" and prop.get("order_id"):
                            return {
                                "status": "SUCCESS",
                                "order_id": prop.get("order_id"),
                                "execution_status": "FILLED",
                                "reconciled_after_timeout": True,
                                "proposal_id": proposal_id,
                                "account_id": account_id
                            }
            except Exception:
                pass

            return {
                "status": "TIMEOUT",
                "error_type": "AMBIGUOUS_TIMEOUT",
                "message": f"Dispatch to {endpoint} timed out after {timeout_seconds}s; proposal status query inconclusive.",
                "proposal_id": proposal_id,
                "account_id": account_id
            }
        except httpx.ConnectError as ce:
            return {
                "status": "FAILED",
                "error_type": "CONNECTION_ERROR",
                "message": f"Could not connect to Core API at {base_url}: {ce}",
                "proposal_id": proposal_id,
                "account_id": account_id
            }
        except Exception as e:
            return {
                "status": "FAILED",
                "error_type": "NETWORK_ERROR",
                "message": str(e),
                "proposal_id": proposal_id,
                "account_id": account_id
            }

    # 2. In-Process Core Service Call Mode
    try:
        if paper_service is None:
            from app.services.persistent_paper_service import persistent_paper_service
            svc = persistent_paper_service
        else:
            svc = paper_service

        order_resp = svc.execute_strategy_proposal(
            proposal_id=proposal_id,
            account_id=account_id
        )
        return {
            "status": "SUCCESS",
            "order_id": order_resp.order_id,
            "client_order_id": order_resp.client_order_id,
            "execution_status": order_resp.status,
            "side": order_resp.side,
            "chain_id": order_resp.chain_id,
            "output_amount_raw": float(order_resp.output_amount_raw) if order_resp.output_amount_raw else None,
            "effective_price_native": float(order_resp.effective_price_native) if order_resp.effective_price_native else None,
            "created_at": order_resp.created_at.isoformat() if order_resp.created_at else None
        }
    except PermissionError as pe:
        return {
            "status": "FORBIDDEN",
            "error_type": "PERMISSION_DENIED",
            "message": str(pe),
            "proposal_id": proposal_id,
            "account_id": account_id
        }
    except ValueError as ve:
        err_msg = str(ve)
        status_category = "EXPIRED" if "expired" in err_msg.lower() else ("RISK_REJECTED" if "risk" in err_msg.lower() else "INVALID_REQUEST")
        return {
            "status": status_category,
            "error_type": "VALIDATION_FAILED",
            "message": err_msg,
            "proposal_id": proposal_id,
            "account_id": account_id
        }
    except Exception as e:
        return {
            "status": "FAILED",
            "error_type": "INTERNAL_ERROR",
            "message": str(e),
            "proposal_id": proposal_id,
            "account_id": account_id
        }


def main(
    proposal_id: str,
    account_id: str,
    core_api_url: Optional[str] = None
) -> Dict[str, Any]:
    return execute_proposal_workflow(proposal_id=proposal_id, account_id=account_id, core_api_url=core_api_url)
