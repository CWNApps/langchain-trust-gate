# langchain-trust-gate

LangChain tools for **Trust Gate** post-quantum, tamper-evident receipts on consequential agent actions.

Trust Gate receipts are signed Ed25519 + ML-DSA-65 (FIPS 204) by the hosted MCP server (no local signing key). Each receipt is verifiable offline from the certificate alone. The hosted server defaults to PQ-required verify (defends against Ed25519-only downgrade); set TRUST_GATE_REQUIRE_PQ=false on your own deployment to allow Ed25519-only receipts.

## Install

```bash
pip install langchain-trust-gate
```

## Usage

```python
from langchain_trust_gate import MintActionReceiptTool, VerifyReceiptTool

# wire into any LangChain agent
tools = [MintActionReceiptTool(), VerifyReceiptTool()]

# typical flow inside an agent:
#   1. the agent decides to do something consequential
#   2. it calls trust_gate_mint_action_receipt(agent_id, operation, target)
#   3. the receipt is stored, attached to the audit log, returned to the user
#   4. later anyone can call trust_gate_verify_receipt(receipt) to confirm tamper-free
```

## Configuration

```bash
# point at a different deployment (Render / Smithery / self-hosted)
export TRUST_GATE_URL="https://trust-gate-mcp.onrender.com"
```

## Tools

| Tool | Purpose |
|---|---|
| `trust_gate_mint_action_receipt` | Mint a post-quantum receipt for any consequential agent action. |
| `trust_gate_verify_receipt` | Verify a Trust Gate receipt from the certificate alone. Defaults to PQ-required. |

## Telemetry

Each tool invocation makes one fire-and-forget `GET /x?via=langchain&kind=api` against the Trust Gate server. No PII, no cookies, no fingerprinting -- just a channel tag so we can measure adoption per framework. Telemetry never blocks or fails the tool.

## Background

* **Trust Gate MCP** -- the hosted server: <https://trust-gate-mcp.onrender.com>
* **Smithery listing** -- <https://smithery.ai/servers/apps/cwn-trust-gate>
* **Official MCP Registry** -- `io.github.CWNApps/trust-gate-mcp`
* **OAO** -- the open-source receipt primitive: <https://github.com/CWNApps/openagentontology>

## License

Apache-2.0.
