import sys
from pathlib import Path
from types import SimpleNamespace

APP_DIR = Path(__file__).resolve().parents[2] / "ai-services" / "rag" / "app"
sys.path.insert(0, str(APP_DIR))

import vector_store


class FakeQdrantClient:
    def __init__(self, collection_info):
        self.collection_info = collection_info
        self.deleted_collections = []
        self.created_collections = []
        self.created_payload_indexes = []

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=vector_store.COLLECTION)])

    def get_collection(self, collection_name):
        assert collection_name == vector_store.COLLECTION
        return self.collection_info

    def delete_collection(self, collection_name):
        self.deleted_collections.append(collection_name)

    def create_collection(self, **kwargs):
        self.created_collections.append(kwargs)

    def create_payload_index(self, **kwargs):
        self.created_payload_indexes.append(kwargs)


def test_vector_store_recreates_incompatible_collection(monkeypatch):
    bad_collection = SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(vectors={}, sparse_vectors={})
        )
    )
    fake_client = FakeQdrantClient(bad_collection)

    monkeypatch.setattr(vector_store, "QdrantClient", lambda *args, **kwargs: fake_client)

    vector_store.VectorStoreClient(embedder=object())

    assert fake_client.deleted_collections == [vector_store.COLLECTION]
    assert len(fake_client.created_collections) == 1
    assert fake_client.created_collections[0]["collection_name"] == vector_store.COLLECTION
    assert len(fake_client.created_payload_indexes) >= 1


def test_vector_store_keeps_matching_collection(monkeypatch):
    good_collection = SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors={
                    "dense": SimpleNamespace(
                        size=vector_store.VECTOR_SIZE,
                        distance=vector_store.Distance.COSINE,
                    )
                },
                sparse_vectors={
                    "sparse": SimpleNamespace(modifier=vector_store.Modifier.IDF)
                },
            )
        )
    )
    fake_client = FakeQdrantClient(good_collection)

    monkeypatch.setattr(vector_store, "QdrantClient", lambda *args, **kwargs: fake_client)

    vector_store.VectorStoreClient(embedder=object())

    assert fake_client.deleted_collections == []
    assert fake_client.created_collections == []
    assert len(fake_client.created_payload_indexes) >= 1