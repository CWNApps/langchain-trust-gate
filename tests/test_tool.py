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
    assert "ML-DSA-65" in t.description
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


# ---- 0.3.0: pinning the signer, and no claim the server's 0.3.0 documentation withdrew ------------
def test_verify_passes_expected_kid_through():
    captured = {}
    def fake_post(self, url, json=None, **kw):
        captured["args"] = json["params"]["arguments"]
        return _mcp_response({"ok": True, "signer_pinned": True})
    with patch("httpx.Client.post", new=fake_post), patch("httpx.Client.get", return_value=MagicMock()):
        VerifyReceiptTool().invoke({"receipt": {"atom_id": "x"}, "expected_kid": "0123456789abcdef0123456789abcdef"})
    assert captured["args"]["expected_kid"] == "0123456789abcdef0123456789abcdef"


def test_verify_default_omits_expected_kid():
    captured = {}
    def fake_post(self, url, json=None, **kw):
        captured["args"] = json["params"]["arguments"]
        return _mcp_response({"ok": True})
    with patch("httpx.Client.post", new=fake_post), patch("httpx.Client.get", return_value=MagicMock()):
        VerifyReceiptTool().invoke({"receipt": {"atom_id": "x"}})
    assert "expected_kid" not in captured["args"]


WITHDRAWN = ("certificate alone", "same notary", "same-notary", "execution permit", "Blocks RESTRICTED",
             "blocks RESTRICTED", "no side effects", "No side effects", "SLH-DSA", "defeats", "defends against",
             "data export", "who signed")


def _all_descriptions():
    from langchain_trust_gate import (CheckEgressTool, GateDecisionTool, MintActionReceiptTool,
                                 RunExitDrillTool, VerifyReceiptTool)
    return {c.__name__: c().description for c in (MintActionReceiptTool, VerifyReceiptTool, GateDecisionTool,
                                                  CheckEgressTool, RunExitDrillTool)}


def test_no_description_makes_a_claim_the_server_withdrew():
    for name, text in _all_descriptions().items():
        for phrase in WITHDRAWN:
            assert phrase not in text, (name, phrase)


def test_descriptions_state_what_the_server_does_and_does_not_do():
    d = _all_descriptions()
    assert "expected_kid" in d["VerifyReceiptTool"]
    assert "ALLOW" in d["GateDecisionTool"] and "0.3.0" in d["GateDecisionTool"]
    assert "does not observe or block" in d["GateDecisionTool"]
    assert "cannot block" in d["CheckEgressTool"] and "NO_MARKERS_FOUND" in d["CheckEgressTool"]
    assert "signs a receipt" in d["RunExitDrillTool"] and "local signing key" in d["RunExitDrillTool"]
    assert "not contacted" in d["RunExitDrillTool"]


def test_version_is_030_everywhere():
    import pathlib, re
    import langchain_trust_gate as pkg
    toml = (pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert pkg.__version__ == "0.3.0"
    assert re.search(r'^version = "0.3.0"', toml, re.M)
