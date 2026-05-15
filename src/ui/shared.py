"""Tiện ích dùng chung cho các tab UI: cached resources + helpers."""

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


@st.cache_resource(show_spinner="Đang kết nối MongoDB...")
def get_query_engine(collection_name: str) -> GraphRAGQueryEngine:
    """Khởi tạo query engine cho 1 collection cụ thể (cache theo tên)."""
    base = get_config()
    cfg = dataclasses.replace(base, mongodb_collection=collection_name)
    return GraphRAGQueryEngine(cfg)


@st.cache_data(ttl=10, show_spinner=False)
def list_collections() -> list[str]:
    """Liệt kê collections trong DB. Cache 10s để tránh hit Mongo liên tục."""
    cfg = get_config()
    client = MongoClient(cfg.mongodb_uri)
    try:
        return sorted(client[cfg.mongodb_db].list_collection_names())
    finally:
        client.close()


def count_entities(collection_name: str) -> int:
    """Đếm số entity (rows) thật trong collection.

    Không cache — gọi sau build để show số liệu chính xác ngay tức thì.
    """
    cfg = get_config()
    client = MongoClient(cfg.mongodb_uri)
    try:
        return client[cfg.mongodb_db][collection_name].estimated_document_count()
    finally:
        client.close()


def slugify_collection_name(name: str) -> str:
    """Chuyển tên thành slug hợp lệ cho MongoDB collection (a-z, 0-9, _)."""
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip()).strip("_").lower()
    return slug or "default_kg"


def active_collection() -> str:
    """Lấy collection đang active từ session, fallback về default trong config."""
    return st.session_state.get(
        "active_collection", get_config().mongodb_collection
    )
