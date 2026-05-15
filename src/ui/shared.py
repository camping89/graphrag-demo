"""Shared utilities for UI tabs: cached resources + helpers."""

from __future__ import annotations

import dataclasses
import re

import streamlit as st
from pymongo import MongoClient

from src.config import Config, load_config
from src.query_engine import GraphRAGQueryEngine


@st.cache_resource(show_spinner=False)
def get_config() -> Config:
    return load_config()


@st.cache_resource(show_spinner="Connecting to MongoDB...")
def get_query_engine(collection_name: str) -> GraphRAGQueryEngine:
    """Initialize a query engine for a specific collection (cache keyed by name)."""
    base = get_config()
    cfg = dataclasses.replace(base, mongodb_collection=collection_name)
    return GraphRAGQueryEngine(cfg)


@st.cache_data(ttl=10, show_spinner=False)
def list_collections() -> list[str]:
    """List collections in DB. Cached 10s to avoid hammering Mongo."""
    cfg = get_config()
    client = MongoClient(cfg.mongodb_uri)
    try:
        return sorted(client[cfg.mongodb_db].list_collection_names())
    finally:
        client.close()


def count_entities(collection_name: str) -> int:
    """Count actual entities (rows) in a collection.

    Not cached — called after build to show accurate numbers immediately.
    """
    cfg = get_config()
    client = MongoClient(cfg.mongodb_uri)
    try:
        return client[cfg.mongodb_db][collection_name].estimated_document_count()
    finally:
        client.close()


def slugify_collection_name(name: str) -> str:
    """Slugify a name into a valid MongoDB collection name (a-z, 0-9, _)."""
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip()).strip("_").lower()
    return slug or "default_kg"


def active_collection() -> str:
    """Get the active collection from session, fall back to config default."""
    return st.session_state.get(
        "active_collection", get_config().mongodb_collection
    )
