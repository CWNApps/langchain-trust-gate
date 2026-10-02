"""LangChain `BaseTool` wrappers around the hosted Trust Gate MCP server.

Tools that any LangChain agent can `bind_tools` with. Receipts are signed with Ed25519, plus ML-DSA-65 when the server has a post-quantum backend; their integrity
can be checked offline, and the signer is pinned with `expected_kid`.

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


def _require_pin_reported(out: Dict[str, Any], expected_kid: Optional[str]) -> Dict[str, Any]:
    """A server older than 0.3.0 drops arguments it does not know, so a pin it never checked would look
    like a pass. When a pin was requested and the server accepted the receipt without reporting
    signer_pinned, raise instead. A refusal (ok false, or an error) is passed through with its reason."""
    if expected_kid is None:
        return out
    body = out.get("result") if isinstance(out.get("result"), dict) else out
    if isinstance(body, dict) and (body.get("ok") is False or "error" in body or "signer_pinned" in body):
        return out
    raise RuntimeError("Trust Gate server did not report signer_pinned, so it ignored expected_kid "
                       "(it needs Trust Gate MCP 0.3.0 or later). Do not treat this receipt as pinned.")


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
    """Mint a signed receipt (Ed25519, plus ML-DSA-65 when the server has a post-quantum backend) for a consequential agent action.

    Returns a receipt dict whose integrity can be checked offline.
    Use BEFORE the action (as a pre-commit) or IMMEDIATELY AFTER (as evidence).
    """
    name: str = "trust_gate_mint_action_receipt"
    description: str = (
        "Mint a signed receipt (Ed25519, plus ML-DSA-65 when the server has a post-quantum backend) for a consequential agent action. Its "
        "integrity can be checked offline; to know which key signed it, verify it with "
        "expected_kid. A receipt is evidence of what was signed, not proof that the action "
        "was safe or met any requirement."
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
        description="None=obey the server's TRUST_GATE_REQUIRE_PQ (default true). True=fail unless a post-quantum signature verifies. False=Ed25519-only is accepted.")
    expected_kid: Optional[str] = Field(
        default=None,
        description="kid of the signer you trust (32 hex characters). When set, verification also requires that the receipt was signed by that key; the result reports signer_pinned. Needs Trust Gate MCP server 0.3.0 or later: an older server "
        "ignores it, so this tool raises an error if the server does not report signer_pinned.")


class VerifyReceiptTool(BaseTool):
    """Verify a Trust Gate receipt from the receipt itself (no DB, no network).

    Pass expected_kid to pin the signer. With require_pq on (the server default) a receipt
    with no verified post-quantum signature is rejected.
    """
    name: str = "trust_gate_verify_receipt"
    description: str = (
        "Verify a Trust Gate receipt from the receipt itself (offline). Returns ok plus the "
        "values it checked and signer_pinned. Pass expected_kid, the kid of the server you "
        "trust, to pin the signer: without it anyone's receipt can verify. With require_pq on "
        "(the server default) it fails unless a post-quantum signature verifies. "
        "expected_kid needs server 0.3.0 or later: with an older server this tool raises an error instead of reporting a pin."
    )
    args_schema: Type[BaseModel] = VerifyReceiptInput

    def _run(self, receipt: Dict[str, Any],
             require_pq: Optional[bool] = None,
             expected_kid: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        _ping_telemetry()
        args: Dict[str, Any] = {"receipt": receipt}
        if require_pq is not None:
            args["require_pq"] = require_pq
        if expected_kid is not None:
            args["expected_kid"] = expected_kid
        return _require_pin_reported(_mcp_call("verify_receipt", args), expected_kid)


# --- gate_decision (sovereignty v0.2.0) -------------------------------------------
class GateDecisionInput(BaseModel):
    action: str = Field(description="The action to evaluate (e.g., 'deploy', 'send_email').")
    resource: str = Field(description="Target resource (e.g., 'prod/api', 'user-db').")
    context: Dict[str, Any] = Field(description="Context dict for the decision (hashed in the receipt).")
    phase: str = Field(default="PREVIEW",
                       description="'PREVIEW' for risk assessment, 'COMMIT' to proceed with receipt.")
    preview_id: Optional[str] = Field(default=None,
                                      description="Required for COMMIT. Returned by the PREVIEW phase.")


class GateDecisionTool(BaseTool):
    """Two-phase decision gate: PREVIEW returns a verdict, COMMIT signs a receipt and returns a permit.

    The verdict is ALLOW, DENY or ESCALATE and comes from the server's read-only allowlist
    over the action name and resource. A GRANTED permit exists only for ALLOW. The gate does
    not observe or block anything; the caller decides what to do with the permit. Needs
    Trust Gate MCP server 0.3.0 or later: a GRANTED permit from an older server is not evidence.
    """
    name: str = "trust_gate_gate_decision"
    description: str = (
        "Two-phase decision gate (needs Trust Gate MCP server 0.3.0 or later). PREVIEW "
        "returns a verdict (ALLOW, DENY or ESCALATE) and a preview_id without acting. COMMIT "
        "evaluates the same inputs again, signs a receipt and returns a permit: GRANTED only "
        "for ALLOW, DENIED for DENY, WITHHELD_PENDING_HUMAN for ESCALATE. It judges the "
        "action name and resource against a read-only allowlist and does not observe or block "
        "anything. Treat a GRANTED permit from a server older than 0.3.0 as not evidence."
    )
    args_schema: Type[BaseModel] = GateDecisionInput

    def _run(self, action: str, resource: str, context: Dict[str, Any],
             phase: str = "PREVIEW", preview_id: Optional[str] = None,
             **kwargs) -> Dict[str, Any]:
        _ping_telemetry()
        args: Dict[str, Any] = {
            "action": action, "resource": resource,
            "context": context, "phase": phase,
        }
        if preview_id is not None:
            args["preview_id"] = preview_id
        return _mcp_call("gate_decision", args)


# --- check_egress (sovereignty v0.2.0) --------------------------------------------
class CheckEgressInput(BaseModel):
    destination: str = Field(description="Where data is being sent (e.g., 'openai.com').")
    data_sample: str = Field(description="Sample of the data being sent (scanned for sensitivity).")
    provider: str = Field(description="The provider/service receiving the data.")


class CheckEgressTool(BaseTool):
    """Check outbound data for sensitivity markers and return a signed receipt.

    Scans the data_sample for a finite list of markers (heuristic) and classifies it
    NO_MARKERS_FOUND / INTERNAL / CONFIDENTIAL / RESTRICTED. It flags and cannot block:
    the caller must act on a RESTRICTED result.
    """
    name: str = "trust_gate_check_egress"
    description: str = (
        "Egress marker check. Scans data for sensitivity markers and classifies it "
        "NO_MARKERS_FOUND, INTERNAL, CONFIDENTIAL or RESTRICTED. It flags and cannot block: "
        "act on a RESTRICTED result yourself. NO_MARKERS_FOUND is not clearance to send."
    )
    args_schema: Type[BaseModel] = CheckEgressInput

    def _run(self, destination: str, data_sample: str, provider: str,
             **kwargs) -> Dict[str, Any]:
        _ping_telemetry()
        return _mcp_call("check_egress", {
            "destination": destination, "data_sample": data_sample, "provider": provider,
        })


# --- run_exit_drill (sovereignty v0.2.0) ------------------------------------------
class RunExitDrillTool(BaseTool):
    """Check vendor exit readiness: local signing key and local model endpoint.

    Informational. Returns step-by-step results and a signed receipt; signing creates the
    signing key on first use.
    """
    name: str = "trust_gate_run_exit_drill"
    description: str = (
        "Vendor exit readiness drill. Checks that the local signing key works (and names the "
        "post-quantum backend) and whether a local model endpoint is configured (it is not "
        "contacted), and signs a receipt (which creates the signing key on first use)."
    )

    def _run(self, **kwargs) -> Dict[str, Any]:
        _ping_telemetry()
        return _mcp_call("run_exit_drill", {})
