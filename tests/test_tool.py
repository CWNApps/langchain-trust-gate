"""Tests for langchain-trust-gate.

Mocks the hosted MCP transport so the suite runs offline. Confirms:
  - the JSON-RPC envelope shape is right
  - telemetry fires on each tool call (best-effort, never blocking)
  - PQ-required parameter passes through
  - tool metadata (name, args_schema) is what LangChain expects
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

import langchain_trust_gate.tool as tool_mod
from langchain_trust_gate import MintActionReceiptTool, VerifyReceiptTool


# ---- helpers --------------------------------------------------------------------------
def _mcp_response(structured: dict):
    """Build a JSON-RPC tools/call response matching the FastMCP shape."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={
        "jsonrpc": "2.0", "id": 1,
        "result": {"structuredContent": structured},
    })
    return resp


# ---- transport shape ------------------------------------------------------------------
def test_mcp_call_uses_jsonrpc_envelope():
    captured = {}
    def fake_post(self, url, json=None, headers=None, **kw):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _mcp_response({"ok": True})

    with patch("httpx.Client.post", new=fake_post), patch("httpx.Client.get", new=lambda *a, **kw: MagicMock()):
        result = tool_mod._mcp_call("mint_action_receipt", {"agent_id": "a", "operation": "o", "target": "t"})

    assert "/mcp" in captured["url"]
    assert captured["json"]["jsonrpc"] == "2.0"
    assert captured["json"]["method"] == "tools/call"
    assert captured["json"]["params"]["name"] == "mint_action_receipt"
    assert captured["json"]["params"]["arguments"]["operation"] == "o"
    assert "application/json" in captured["headers"]["Accept"]


def test_mcp_call_raises_on_jsonrpc_error():
    err_resp = MagicMock()
    err_resp.raise_for_status = MagicMock()
    err_resp.json = MagicMock(return_value={"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid"}})
    with patch("httpx.Client.post", return_value=err_resp), patch("httpx.Client.get", return_value=MagicMock()):
        with pytest.raises(RuntimeError, match="Trust Gate MCP error"):
            tool_mod._mcp_call("verify_receipt", {"receipt": {}})


# ---- telemetry (best-effort, never blocks) -------------------------------------------
def test_telemetry_fires_on_tool_call():
    pings = []
    def fake_get(self, url, params=None, **kw):
        pings.append(params)
        return MagicMock()

    with patch("httpx.Client.post", return_value=_mcp_response({"ok": True})), \
         patch("httpx.Client.get", new=fake_get):
        MintActionReceiptTool().invoke({"agent_id": "a", "operation": "o", "target": "t"})

    assert pings, "telemetry never fired"
    assert pings[0]["via"] == "langchain"
    assert pings[0]["kind"] == "api"


def test_telemetry_failure_does_not_break_tool():
    def fake_get(self, *a, **kw):
        import httpx
        raise httpx.ConnectError("simulated network down")

    with patch("httpx.Client.post", return_value=_mcp_response({"ok": True, "kid": "abc"})), \
         patch("httpx.Client.get", new=fake_get):
        # tool must still return the real result even if telemetry fails
        out = MintActionReceiptTool().invoke({"agent_id": "a", "operation": "o", "target": "t"})
    assert out["ok"] is True


# ---- PQ-required passthrough ----------------------------------------------------------
def test_verify_receipt_passes_require_pq_through():
    captured = {}
    def fake_post(self, url, json=None, **kw):
        captured["args"] = json["params"]["arguments"]
        return _mcp_response({"ok": True, "reason": "fine"})

    with patch("httpx.Client.post", new=fake_post), patch("httpx.Client.get", return_value=MagicMock()):
        VerifyReceiptTool().invoke({"receipt": {"atom_id": "x"}, "require_pq": False})

    assert captured["args"]["require_pq"] is False
    assert "receipt" in captured["args"]


def test_verify_receipt_defaults_omit_require_pq():
    """When the caller doesn't set require_pq, we let the server's env default apply.
    Sending require_pq=None explicitly would be misleading -- leave it out of the args."""
    captured = {}
    def fake_post(self, url, json=None, **kw):
        captured["args"] = json["params"]["arguments"]
        return _mcp_response({"ok": True})

    with patch("httpx.Client.post", new=fake_post), patch("httpx.Client.get", return_value=MagicMock()):
        VerifyReceiptTool().invoke({"receipt": {"atom_id": "x"}})

    assert "require_pq" not in captured["args"]


# ---- LangChain integration shape -----------------------------------------------------
def test_tool_metadata_is_langchain_compatible():
    t = MintActionReceiptTool()
    assert t.name == "trust_gate_mint_action_receipt"
    assert "post-quantum" in t.description.lower()
    assert t.args_schema is not None
    # args_schema must accept the four required fields
    sch = t.args_schema.model_json_schema()
    assert "agent_id" in sch["properties"]
    assert "operation" in sch["properties"]
    assert "target" in sch["properties"]


def test_verify_tool_metadata():
    t = VerifyReceiptTool()
    assert t.name == "trust_gate_verify_receipt"
    assert "offline" in t.description.lower()
