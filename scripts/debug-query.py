"""Debug script — inspect graph state cho 1 collection cụ thể."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

COLLECTION = sys.argv[1] if len(sys.argv) > 1 else "soc2_entities"
SEARCH_REGEX = sys.argv[2] if len(sys.argv) > 2 else "Veek"

client = MongoClient(os.environ["MONGODB_URI"])
coll = client["graphrag_demo"][COLLECTION]

print(f"=== Search _id matching '{SEARCH_REGEX}' ===")
for doc in coll.find(
    {"_id": {"$regex": SEARCH_REGEX, "$options": "i"}},
    {"embedding": 0},
):
    print(json.dumps(doc, indent=2, ensure_ascii=False))
    print("---")

print("\n=== All Organization entities ===")
for doc in coll.find({"type": "Organization"}, {"_id": 1}):
    print(f"  - {doc['_id']}")

print("\n=== Pham Tuyen target_ids ===")
pt = coll.find_one({"_id": "Pham Tuyen"}, {"relationships": 1})
if pt and pt.get("relationships"):
    rels = pt["relationships"]
    for t, ty in zip(rels.get("target_ids", []), rels.get("types", [])):
        print(f"  [{ty}] {t}")

print(f"\nTotal docs in collection: {coll.count_documents({})}")
client.close()
