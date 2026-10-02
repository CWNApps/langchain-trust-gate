"""langchain-trust-gate -- LangChain tools for Trust Gate signed receipts.

Five tools, all backed by the hosted Trust Gate MCP server:

  MintActionReceiptTool   -- mints a tamper-evident receipt for any consequential agent
                              action (deploy, send_email, charge_card, write_db, ...).
  VerifyReceiptTool       -- verifies a Trust Gate receipt; pass expected_kid to pin the signer.
  GateDecisionTool        -- two-phase PREVIEW -> COMMIT gate; PREVIEW returns a verdict
                               (ALLOW, DENY, ESCALATE), COMMIT signs a receipt and returns
                               a permit (GRANTED only for ALLOW).
  CheckEgressTool         -- flags data NO_MARKERS_FOUND/INTERNAL/CONFIDENTIAL/RESTRICTED
                               before it leaves; it cannot block.
  RunExitDrillTool        -- vendor exit-readiness drill. Informational; it signs a receipt.

Receipts are signed Ed25519 + ML-DSA-65 via the upstream OpenAgentOntology primitive.
Verification defaults to PQ-required mode, which rejects a receipt with no verified
post-quantum signature. Pin the signer with expected_kid.

Usage:
    from langchain_trust_gate import MintActionReceiptTool, VerifyReceiptTool
    tools = [MintActionReceiptTool(), VerifyReceiptTool()]
"""
from langchain_trust_gate.tool import (
    CheckEgressTool,
    GateDecisionTool,
    MintActionReceiptTool,
    RunExitDrillTool,
    VerifyReceiptTool,
)

__version__ = "0.3.0"
__all__ = [
    "MintActionReceiptTool",
    "VerifyReceiptTool",
    "GateDecisionTool",
    "CheckEgressTool",
    "RunExitDrillTool",
    "__version__",
]
