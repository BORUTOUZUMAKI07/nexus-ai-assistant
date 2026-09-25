import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import numpy as np
from backend.app.services.rag.retrieval import maximal_marginal_relevance


def test_mmr_diversification():
    # Query vector: pointing along [1, 0, 0]
    query = [1.0, 0.0, 0.0]

    # Doc 1: Very close to query: similarity ~ 0.9
    doc1 = [0.9, 0.435, 0.0]
    # Doc 2: Highly redundant with Doc 1: doc2 == doc1 (duplicate content), query_sim ~ 0.9
    doc2 = [0.9, 0.435, 0.0]
    # Doc 3: Meaningfully relevant (query_sim ~ 0.8), but orthogonal to Doc 1 along axis 2
    doc3 = [0.8, 0.0, 0.6]

    candidates = [
        {"id": "doc1", "content": "First relevant document"},
        {"id": "doc2", "content": "Duplicate of first document"},
        {"id": "doc3", "content": "Diverse document"},
    ]
    candidate_vectors = [doc1, doc2, doc3]

    # For doc2:
    #   sim(query, doc2) = 0.9. sim(doc2, doc1) = 1.0.
    #   MMR score = 0.5 * 0.9 - 0.5 * 1.0 = -0.05
    # For doc3:
    #   sim(query, doc3) = 0.8. sim(doc3, doc1) = 0.72.
    #   MMR score = 0.5 * 0.8 - 0.5 * 0.72 = +0.04
    # Therefore, MMR selects doc3 over the duplicate doc2!
    selected = maximal_marginal_relevance(
        query_vector=query,
        candidate_vectors=candidate_vectors,
        candidates=candidates,
        top_k=2,
        lambda_mult=0.5,
    )

    assert len(selected) == 2
    assert selected[0]["id"] == "doc1"
    assert selected[1]["id"] == "doc3", f"Expected doc3 to be chosen for diversity, got {selected[1]['id']}"
    print("MMR diversification test PASSED successfully!")


if __name__ == "__main__":
    test_mmr_diversification()
