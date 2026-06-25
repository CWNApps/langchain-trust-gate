"""langchain-trust-gate -- LangChain tools for Trust Gate post-quantum receipts.

Two tools, both backed by the hosted Trust Gate MCP server:

  MintActionReceiptTool   -- mints a tamper-evident receipt for any consequential agent
                              action (deploy, send_email, charge_card, write_db, ...).
  VerifyReceiptTool       -- verifies a Trust Gate receipt from its certificate alone.

The receipts inherit Ed25519 + ML-DSA-65 signing via the upstream OpenAgentOntology
primitive; verification defaults to PQ-required mode (defends against Ed25519-only
downgrade by requiring at least one verified PQ leg).

Usage:
    from langchain_trust_gate import MintActionReceiptTool, VerifyReceiptTool
    tools = [MintActionReceiptTool(), VerifyReceiptTool()]
"""
from langchain_trust_gate.tool import MintActionReceiptTool, VerifyReceiptTool

__version__ = "0.1.0"
__all__ = ["MintActionReceiptTool", "VerifyReceiptTool", "__version__"]
