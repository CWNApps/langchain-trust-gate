"""langchain-trust-gate -- LangChain tools for Trust Gate post-quantum receipts.

Five tools, all backed by the hosted Trust Gate MCP server:

  MintActionReceiptTool   -- mints a tamper-evident receipt for any consequential agent
                              action (deploy, send_email, charge_card, write_db, ...).
  VerifyReceiptTool       -- verifies a Trust Gate receipt from its certificate alone.
  GateDecisionTool        -- two-phase PREVIEW -> COMMIT gate; PREVIEW assesses risk
                              without acting, COMMIT verifies the inputs still match
                              and mints a receipt carrying an execution permit.
  CheckEgressTool         -- classifies data PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED
                              before it leaves; blocks RESTRICTED.
  RunExitDrillTool        -- vendor exit-readiness drill. Informational, no side effects.

Receipts are signed Ed25519 + ML-DSA-65 (FIPS 204) via the upstream OpenAgentOntology
primitive. Verification defaults to PQ-required mode, which demands at least one verified
post-quantum leg and so defeats Ed25519-only downgrade attacks.

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

__version__ = "0.2.0"
__all__ = [
    "MintActionReceiptTool",
    "VerifyReceiptTool",
    "GateDecisionTool",
    "CheckEgressTool",
    "RunExitDrillTool",
    "__version__",
]
