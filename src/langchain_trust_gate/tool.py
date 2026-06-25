"""LangChain `BaseTool` wrappers around the hosted Trust Gate MCP server.

Two tools that any LangChain agent can `bind_tools` with. The receipts are post-quantum
by default (Ed25519 + ML-DSA-65), verifiable offline from the certificate alone.

Transport: each tool invocation makes ONE HTTPS POST to the live MCP endpoint and ONE
fire-and-forget GET to /x for per-channel attribution telemetry. No PII is sent --
just the JSON-RPC payload + a `?via=langchain` query param.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Type

import httpx
from pydantic import BaseModel, Field

try:
    from langchain_core.tools import BaseTool
except ImportError as e:
    raise ImportError(
        "langchain-trust-gate requires langchain_core. Install with: "
        "pip install langchain-core (or `pip install langchain-trust-gate[langchain]`)"
    ) from e


# Public endpoint of the Trust Gate MCP server. Override with TRUST_GATE_URL env if
# self-hosting (e.g. via Render, Smithery, or your own container).
TRUST_GATE_URL = os.environ.get("TRUST_GATE_URL", "https://trust-gate-mcp.onrender.com")
_VIA = "langchain"


def _mcp_call(method: str, arguments: Dict[str, Any], *, timeout: float = 30.0) -> Dict[str, Any]:
    """Single JSON-RPC tools/call against the hosted Trust Gate MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": method, "arguments": arguments},
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{TRUST_GATE_URL}/mcp",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-03-26",
            },
        )
        r.raise_for_status()
        body = r.json()
    if "error" in body:
        raise RuntimeError(f"Trust Gate MCP error: {body['error']}")
    # FastMCP returns the tool result under result.structuredContent or result.content
    result = body.get("result", {})
    if isinstance(result, dict):
        if "structuredContent" in result:
            return result["structuredContent"]
        if "content" in result and result["content"]:
            # text content -- best-effort JSON parse
            try:
                import json
                return json.loads(result["content"][0]["text"])
            except (KeyError, ValueError, IndexError):
                return {"raw": result["content"]}
    return result if isinstance(result, dict) else {"raw": result}


def _ping_telemetry(kind: str = "api") -> None:
    """Fire-and-forget channel-attribution ping. Never blocks the tool's return value."""
    try:
        with httpx.Client(timeout=2.0) as client:
            client.get(f"{TRUST_GATE_URL}/x", params={"via": _VIA, "kind": kind})
    except Exception:  # noqa: BLE001 -- telemetry is best-effort; ANY failure must be swallowed
        pass


# --- mint_action_receipt --------------------------------------------------------------
class MintActionReceiptInput(BaseModel):
    agent_id: str = Field(description="Identifier of the agent performing the action.")
    operation: str = Field(description="Operation name (e.g., 'deploy', 'send_email', "
                                       "'charge_card', 'write_db').")
    target: str = Field(description="Target of the action (e.g., 'prod/api', "
                                    "'alice@example.com', 'opp-019efc34').")
    policy: Optional[str] = Field(default="agent action evidence",
                                  description="Policy label carried in the receipt.")
    inputs: Optional[str] = Field(default=None,
                                  description="Inputs to the action (hashed in the receipt).")
    decision: Optional[str] = Field(default="ACTION_GOVERNED",
                                    description="Decision label.")


class MintActionReceiptTool(BaseTool):
    """Mint a post-quantum, tamper-evident receipt for a consequential agent action.

    Returns a receipt dict that's verifiable offline from the certificate alone.
    Use BEFORE the action (as a pre-commit) or IMMEDIATELY AFTER (as evidence).
    """
    name: str = "trust_gate_mint_action_receipt"
    description: str = (
        "Mint a post-quantum, tamper-evident receipt for a consequential agent action. "
        "Returns a receipt that's verifiable offline from the certificate alone. "
        "Receipt is signed Ed25519 + ML-DSA-65; carries a kid for offline 'same notary?' check."
    )
    args_schema: Type[BaseModel] = MintActionReceiptInput

    def _run(self, agent_id: str, operation: str, target: str,
             policy: Optional[str] = None, inputs: Optional[str] = None,
             decision: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        _ping_telemetry()
        args = {
            "agent_id": agent_id,
            "operation": operation,
            "target": target,
            "policy": policy or "agent action evidence",
        }
        if inputs is not None:
            args["inputs"] = inputs
        if decision is not None:
            args["decision"] = decision
        return _mcp_call("mint_action_receipt", args)


# --- verify_receipt -------------------------------------------------------------------
class VerifyReceiptInput(BaseModel):
    receipt: Dict[str, Any] = Field(description="The Trust Gate receipt to verify.")
    require_pq: Optional[bool] = Field(
        default=None,
        description="None=obey TRUST_GATE_REQUIRE_PQ env (default true). True=fail if "
                    "ML-DSA-65 AND SLH-DSA legs both missing. False=Ed25519-only OK.")


class VerifyReceiptTool(BaseTool):
    """Verify a Trust Gate receipt from the certificate alone (no DB, no network).

    Default is PQ-required mode: rejects receipts that have no verified PQ leg
    (defends against Ed25519-only downgrade).
    """
    name: str = "trust_gate_verify_receipt"
    description: str = (
        "Verify a Trust Gate receipt from the certificate alone (offline). "
        "Returns {ok, hash_ok, sig_ok, signed, legs, signature_alg, reason}. "
        "Defaults to PQ-required mode -- defends against Ed25519-only downgrade by "
        "requiring at least one verified PQ leg."
    )
    args_schema: Type[BaseModel] = VerifyReceiptInput

    def _run(self, receipt: Dict[str, Any],
             require_pq: Optional[bool] = None, **kwargs) -> Dict[str, Any]:
        _ping_telemetry()
        args: Dict[str, Any] = {"receipt": receipt}
        if require_pq is not None:
            args["require_pq"] = require_pq
        return _mcp_call("verify_receipt", args)
