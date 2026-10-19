import math

import spacy

from mem0.reranker.huggingface_reranker import HuggingFaceReranker

nlp = spacy.load("en_core_web_sm")
assert [token.lemma_ for token in nlp("cats running")] == ["cat", "run"]
reranker = HuggingFaceReranker({"model": "/opt/models/bge-reranker-base", "device": "cpu"})
results = reranker.rerank(
    "Where is Paris?",
    [{"id": "relevant", "memory": "Paris is in France."}, {"id": "other", "memory": "Bananas are yellow."}],
    top_k=2,
)
assert len(results) == 2
assert results[0]["id"] == "relevant"
assert all(math.isfinite(item["rerank_score"]) and 0 < item["rerank_score"] < 1 for item in results)
print("Offline retrieval runtime: NLP and HuggingFace reranking passed")
