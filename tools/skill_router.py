import math
import re
import os
import json
from typing import List, Dict, Any, Tuple


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "between", "by", "can", "could",
    "did", "do", "does", "for", "from", "had", "has", "have", "how", "i", "if",
    "in", "into", "is", "it", "its", "me", "my", "of", "on", "or", "our",
    "please", "so", "some", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "to", "us", "was", "we", "were", "what", "when",
    "where", "which", "who", "why", "will", "with", "would", "you", "your",
}


def _tokenize(text: str) -> List[str]:
    """Basic lowercased alphanumeric tokenizer with stopword filtering."""
    raw_tokens = re.findall(r"\b[a-zA-Z0-9_]+\b", text.lower())
    return [t for t in raw_tokens if t not in _STOPWORDS]



class BM25CatalogRouter:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = 0
        self.avg_doc_len = 0.0
        self.doc_lengths: List[int] = []
        self.doc_freqs: Dict[str, int] = {}  # term -> doc count
        self.catalog_skills: List[Dict[str, Any]] = []
        self.tokenized_corpus: List[List[str]] = []

    def build_index(self, skills: List[Dict[str, Any]]):
        """Indexes skills catalog using combined metadata (name, intent, docstring/tags)."""
        self.catalog_skills = skills
        self.corpus_size = len(skills)
        self.doc_lengths = []
        self.doc_freqs = {}
        self.tokenized_corpus = []

        if self.corpus_size == 0:
            self.avg_doc_len = 0.0
            return

        total_length = 0
        for skill in skills:
            # Aggregate searchable text (including underscore-split skill name and tags)
            name = str(skill.get("name", ""))
            name_words = name.replace("_", " ")
            tags = skill.get("tags", "")
            if isinstance(tags, list):
                tags = " ".join(str(t) for t in tags)
            searchable_text = (
                f"{name} {name_words} {skill.get('intent', '')} "
                f"{skill.get('description', '')} {tags}"
            )
            tokens = _tokenize(searchable_text)
            self.tokenized_corpus.append(tokens)
            doc_len = len(tokens)
            self.doc_lengths.append(doc_len)
            total_length += doc_len

            # Calculate document frequencies
            unique_tokens = set(tokens)
            for token in unique_tokens:
                self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1

        self.avg_doc_len = total_length / self.corpus_size if self.corpus_size > 0 else 0.0

    def query(self, prompt: str, top_k: int = 3, min_score_threshold: float = 1.2) -> List[Dict[str, Any]]:
        """
        Calculates BM25 relevance scores for the query string.
        Returns top_k matching skills that satisfy min_score_threshold.
        """
        if self.corpus_size == 0:
            return []

        query_tokens = _tokenize(prompt)
        if not query_tokens:
            return []

        scores: List[float] = [0.0] * self.corpus_size
        # Effective corpus smoothing ensures 1-2 skill catalogs still produce meaningful IDF
        effective_n = max(self.corpus_size, 3)

        for token in query_tokens:
            if token not in self.doc_freqs:
                continue

            # Standard Robertson-Spärck Jones IDF
            n_q = self.doc_freqs[token]
            idf = math.log(1.0 + (effective_n - n_q + 0.5) / (n_q + 0.5))

            for doc_idx, doc_tokens in enumerate(self.tokenized_corpus):
                f_q = doc_tokens.count(token)
                if f_q == 0:
                    continue

                doc_len = self.doc_lengths[doc_idx]
                denom = f_q + self.k1 * (1.0 - self.b + self.b * (doc_len / (self.avg_doc_len or 1.0)))
                scores[doc_idx] += idf * ((f_q * (self.k1 + 1.0)) / denom)

        # Pair scores with original skills and sort descending
        ranked = sorted(
            [(scores[i], self.catalog_skills[i]) for i in range(self.corpus_size) if scores[i] >= min_score_threshold],
            key=lambda x: x[0],
            reverse=True
        )

        return [skill for score, skill in ranked[:top_k]]
