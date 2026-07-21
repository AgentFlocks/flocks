"""
Simple tests for the run_workflow tool.

本文件会"展示调用工具给 agent 的完整结果"，因此会打印 ToolResult 的完整 JSON。

Usage:
    # 使用默认 workflow 文件
    python tests/test_tool_run_workflow_simple.py
    
    # 指定 workflow 文件路径
    python tests/test_tool_run_workflow_simple.py --workflow path/to/workflow.json
    
    # 指定 workflow 和输入参数
    python tests/test_tool_run_workflow_simple.py --workflow examples/search_and_summarize/workflow.json --query "Python async" --num-results 10
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from flocks.tool import ToolContext, ToolRegistry  # noqa: E402


def _dump_tool_result(result) -> str:
    """Dump full ToolResult as JSON (what an agent effectively receives)."""
    if hasattr(result, "model_dump"):
        data = result.model_dump()
    else:
        data = result.dict()
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


async def run_simple_workflow_full_result():
    ToolRegistry.init()

    ctx = ToolContext(
        session_id="test-session-simple",
        message_id="test-message-simple",
        agent="test",
    )

    workflow = {
        "name": "simple",
        "start": "node-1",
        "metadata": {},
        "nodes": [
            {
                "id": "node-1",
                "type": "python",
                "code": "outputs['result'] = {'message': 'Hello from workflow!', 'value': 42}",
            }
        ],
        "edges": [],
    }

    result = await ToolRegistry.execute(
        "run_workflow",
        ctx=ctx,
        workflow=workflow,
        inputs={},
        ensure_requirements=False,
    )

    # 展示：给 agent 的完整结果（含 success/output/error/metadata/title/...）
    print(_dump_tool_result(result))

    assert result.success is True
    assert result.metadata.get("status") == "success"
    assert "Status: SUCCEEDED" in (result.output or "")


async def run_specified_workflow_file_full_result(workflow_path=None, query=None, num_results=None):
    """实际执行一个"指定的 workflow.json 文件"，并展示完整 ToolResult 给 agent。
    
    测试 search_and_summarize workflow：
    1. search_web 节点调用 websearch 工具执行搜索
    2. check_results 节点检查搜索结果有效性
    3. branch_on_results 节点根据 has_results 进行分支
    4. 有结果时走 generate_detailed_summary（logic 节点，需要 LLM）
    5. 无结果时走 generate_empty_report（python 节点）
    6. finalize_output 节点汇总最终输出
    
    Args:
        workflow_path: workflow 文件路径（可选，默认使用 examples/search_and_summarize/workflow.json）
        query: 搜索关键词（可选，默认使用预设值）
        num_results: 结果数量（可选，默认为 5）
    """

    ToolRegistry.init()

    ctx = ToolContext(
        session_id="test-session-specified-workflow",
        message_id="test-message-specified-workflow",
        agent="test",
    )

    # 通过文件路径指定 workflow
    if workflow_path:
        wf_path = Path(workflow_path)
    else:
        repo_root = Path(__file__).resolve().parents[1]
        wf_path = repo_root / "examples" / "search_and_summarize" / "workflow.json"
    
    print(f"📄 使用 workflow 文件: {wf_path}")
    
    # 检查文件是否存在
    if not wf_path.exists():
        error_msg = f"Workflow file not found: {wf_path}\nThis is expected if workflow.json hasn't been generated yet."
        print(f"⚠️  {error_msg}")
        # 如果在 pytest 环境中，使用 skip；否则抛出异常
        try:
            import pytest
            pytest.skip(error_msg)
        except (ImportError, NameError):
            # 不在 pytest 环境中，直接返回（允许直接运行脚本时跳过）
            return

    # search_and_summarize workflow 的输入参数：
    # - query: 搜索关键词（必需）
    # - numResults: 结果数量（可选，默认=8）
    # - type: 搜索类型（可选，默认="auto"）
    test_inputs = {
        "query": query or "Python async programming best practices",
        "numResults": num_results or 5,
        "type": "auto"
    }
    
    print(f"🔍 输入参数: query='{test_inputs['query']}', numResults={test_inputs['numResults']}")

    # 使用文件路径执行 workflow（而非内联 dict）
    result = await ToolRegistry.execute(
        "run_workflow",
        ctx=ctx,
        workflow=str(wf_path),  # 传入文件路径字符串
        inputs=test_inputs,
        ensure_requirements=False,
        use_llm=True,  # 需要 LLM 来执行 generate_detailed_summary logic 节点
    )

    # 展示：给 agent 的完整结果（含 success/output/error/metadata/title/...）
    print(_dump_tool_result(result))

    # 基础断言：工作流应该成功执行
    assert result.success is True, f"Workflow execution failed: {result.error}"
    assert result.metadata.get("status") == "success", f"Expected success status, got: {result.metadata.get('status')}"
    assert "Status: SUCCEEDED" in (result.output or ""), "Output should contain success status"
    
    # 验证最终节点：应该是 finalize_output
    last_node_id = result.metadata.get("last_node_id")
    assert last_node_id == "finalize_output", f"Expected last_node_id to be 'finalize_output', got: {last_node_id}"
    
    # 验证输出结构：应该包含 final_summary 和 metadata
    if result.metadata.get("final_payload"):
        final_payload = result.metadata.get("final_payload", {})
        assert "final_summary" in final_payload, "final_payload should contain 'final_summary'"
        assert "metadata" in final_payload, "final_payload should contain 'metadata'"
        
        # 验证 metadata 结构
        metadata = final_payload.get("metadata", {})
        assert "query" in metadata, "metadata should contain 'query'"
        assert "type" in metadata, "metadata should contain 'type'"
        assert metadata["query"] == test_inputs["query"], f"metadata.query should match input query"
        assert metadata["type"] in ["detailed", "empty"], f"metadata.type should be 'detailed' or 'empty', got: {metadata['type']}"
        
        # 验证摘要内容
        final_summary = final_payload.get("final_summary", "")
        assert len(final_summary) > 0, "final_summary should not be empty"
        
        # 如果有结果，摘要应该包含搜索查询
        if metadata["type"] == "detailed":
            assert test_inputs["query"] in final_summary or "搜索" in final_summary, "Detailed summary should contain query or search-related content"
        elif metadata["type"] == "empty":
            assert "未找到" in final_summary or "No results" in final_summary.lower(), "Empty report should indicate no results found"


# ----------------------------
# Optional pytest integration
# ----------------------------
try:
    import pytest  # type: ignore

    @pytest.mark.anyio
    async def test_run_workflow_simple_full_result():
        await run_simple_workflow_full_result()

    @pytest.mark.anyio
    async def test_run_workflow_execute_specified_workflow_file_full_result():
        await run_specified_workflow_file_full_result()
except Exception:
    # Allow direct execution without pytest installed.
    pytest = None  # type: ignore


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="测试 run_workflow 工具，支持指定 workflow 文件路径",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认 workflow 文件
  python tests/test_tool_run_workflow_simple.py
  
  # 指定 workflow 文件路径
  python tests/test_tool_run_workflow_simple.py --workflow examples/search_and_summarize/workflow.json
  
  # 指定 workflow 和输入参数
  python tests/test_tool_run_workflow_simple.py --workflow examples/search_and_summarize/workflow.json --query "Python async" --num-results 10
        """
    )
    
    parser.add_argument(
        "--workflow",
        type=str,
        help="workflow 文件路径（默认: examples/search_and_summarize/workflow.json）"
    )
    
    parser.add_argument(
        "--query",
        type=str,
        help="搜索关键词（默认: 'Python async programming best practices'）"
    )
    
    parser.add_argument(
        "--num-results",
        type=int,
        help="搜索结果数量（默认: 5）"
    )
    
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    
    print("=== run_workflow: simple inline workflow ===")
    await run_simple_workflow_full_result()
    
    print("\n=== run_workflow: specified workflow.json file ===")
    await run_specified_workflow_file_full_result(
        workflow_path=args.workflow,
        query=args.query,
        num_results=args.num_results
    )


if __name__ == "__main__":
    asyncio.run(main())
