"""Tests — MCP server tool registry (unit tests, no live API)."""
import pytest

pytest.importorskip("mcp", reason="pip install mcp for MCP tests")

from prismrag.mcp import server as mcp_server


class TestMcpTools:
    def test_all_tools_have_handlers(self):
        tool_names = {t["name"] for t in mcp_server.TOOLS}
        handler_names = set(mcp_server._HANDLERS.keys())
        assert tool_names == handler_names, (
            f"Mismatch: tools={tool_names - handler_names}, handlers={handler_names - tool_names}"
        )

    def test_tool_schemas_valid(self):
        for tool in mcp_server.TOOLS:
            assert tool["name"]
            assert tool["description"]
            schema = tool["inputSchema"]
            assert schema.get("type") == "object"
            assert "properties" in schema

    def test_expected_tools_present(self):
        names = {t["name"] for t in mcp_server.TOOLS}
        for expected in (
            "search",
            "list_workspaces",
            "list_communities",
            "submit_job",
            "get_job_status",
            "create_bridge",
            "deliberate",
            "get_deliberation_session",
            "deliberation_followup",
            # chunk portability + quality
            "append_chunks",
            "export_chunks",
            "score_chunk_quality",
        ):
            assert expected in names

    def test_list_workspaces_calls_tenants_endpoint(self, monkeypatch):
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return [{"tenant_id": "t1", "name": "QA", "role": "owner", "data_region": "us-east"}]

            def raise_for_status(self):
                pass

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, path, **kwargs):
                calls.append(path)
                return FakeResponse()

        monkeypatch.setattr(mcp_server.httpx, "Client", FakeClient)
        monkeypatch.setattr(mcp_server, "API_KEY", "prk_test")
        result = mcp_server._handle_list_workspaces({})
        assert "QA" in result
        assert any("tenants" in p for p in calls)
