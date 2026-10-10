"""Heading-aware policy retrieval with an in-memory Chroma collection."""
from collections import Counter
from hashlib import blake2b
from math import log, sqrt
from pathlib import Path
import re
import chromadb

ROOT = Path(__file__).resolve().parents[1]


def input_dir(name):
    for path in (ROOT / name, ROOT.parent / "data" / name):
        pattern = "*.md" if name == "knowledge_base" else "*.csv"
        if path.is_dir() and any(path.glob(pattern)):
            return path
    return ROOT / name


def anchor(heading):
    return re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip().replace(" ", "-")


def terms(text):
    return re.findall(r"[a-z0-9]+", text.lower())


class PolicyIndex:
    def __init__(self, max_distance=1.8):
        self.sections = []
        self.max_distance = max_distance
        for path in sorted(input_dir("knowledge_base").glob("*.md")):
            heading = []
            body = []
            def flush():
                if body and heading and len(" ".join(body).split()) >= 25:
                    source = f"{path.name}#{anchor(heading[-1])}"
                    words = " ".join(body).split()
                    for start in range(0, len(words), 300):
                        self.sections.append({"source": source, "heading": " > ".join(heading),
                                              "text": " ".join(words[start:start + 300])})
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                match = re.match(r"^(#{1,6})\s+(.+)", line)
                if match:
                    flush()
                    depth = len(match.group(1))
                    heading = heading[:depth - 1] + [match.group(2).strip()]
                    body = []
                else:
                    body.append(line)
            flush()
        self.collection = None
        if self.sections:
            documents = [s["heading"] + "\n" + s["text"] for s in self.sections]
            counts = Counter(term for doc in documents for term in set(terms(doc)))
            self.weights = {term: log((len(documents) + 1) / (count + 1)) + 1 for term, count in counts.items()}
            client = chromadb.EphemeralClient()
            self.collection = client.get_or_create_collection("retail_policy_sections")
            if self.collection.count() == 0:
                self.collection.add(
                    ids=[str(i) for i in range(len(self.sections))],
                    documents=documents,
                    embeddings=[self.embed(doc) for doc in documents],
                    metadatas=[{"source": s["source"], "heading": s["heading"]} for s in self.sections],
                )

    def embed(self, text):
        """Small stable hashed term vector; no remote embedding service or model file."""
        vector = [0.0] * 384
        for term, count in Counter(terms(text)).items():
            digest = int.from_bytes(blake2b(term.encode(), digest_size=8).digest(), "big")
            vector[digest % len(vector)] += (1 if digest & (1 << 63) else -1) * (1 + log(count)) * self.weights.get(term, 1)
        size = sqrt(sum(value * value for value in vector)) or 1.0
        return [value / size for value in vector]

    def retrieve(self, query, limit=4):
        if self.collection is None:
            return []
        found = self.collection.query(query_embeddings=[self.embed(query)], n_results=min(limit, len(self.sections)), include=["distances"])
        return [dict(self.sections[int(i)], distance=round(float(distance), 3))
                for i, distance in zip(found["ids"][0], found["distances"][0])
                if distance <= self.max_distance]

    def source(self, ref):
        return next((s for s in self.sections if s["source"] == ref), None)
