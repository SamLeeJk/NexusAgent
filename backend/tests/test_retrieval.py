import pytest
from retrieval import search_policy
from tools import POLICY_PATH, search_refund_policy


@pytest.mark.parametrize(
    "query", ["astronomy galaxies", "what is the weather", "", "?!", "the and is"]
)
def test_unrelated_or_empty_queries_have_no_sources(query):
    result = search_refund_policy.invoke({"query": query})
    assert result["found"] is False
    assert result["sources"] == []
    assert result["best_chunk"] is None


def test_matching_policy_has_verifiable_source():
    result = search_policy(POLICY_PATH, "shipped")
    assert result["found"] is True
    source = result["sources"][0]
    assert "not eligible" in source["text"]
    assert source["text"] == POLICY_PATH.read_text().splitlines()[source["line"] - 1]
    assert source["source"] == "refund_policy.md"
    assert len(source["version"]) == 64


@pytest.mark.parametrize("content", ["", "\n   \n", "the and is"])
def test_empty_policy_does_not_crash(tmp_path, content):
    path = tmp_path / "policy.md"
    path.write_text(content)
    assert search_policy(path, "refund")["reason"] == "empty_policy"


def test_missing_policy_does_not_crash(tmp_path):
    assert (
        search_policy(tmp_path / "missing.md", "refund")["reason"]
        == "policy_unavailable"
    )


def test_single_chunk_with_nonpositive_score_still_matches(tmp_path):
    path = tmp_path / "policy.md"
    path.write_text("Refund requires confirmation.")
    assert search_policy(path, "refund")["found"] is True


def test_same_size_edit_invalidates_index_and_version(tmp_path):
    path = tmp_path / "policy.md"
    path.write_text("alpha policy")
    first = search_policy(path, "alpha")
    path.write_text("bravo policy")
    second = search_policy(path, "bravo")
    assert first["sources"][0]["version"] != second["sources"][0]["version"]
    assert search_policy(path, "alpha")["found"] is False


def test_top_k_limits_results():
    result = search_policy(POLICY_PATH, "refund", top_k=2)
    assert len(result["sources"]) == 2
