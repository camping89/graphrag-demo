"""App version — bump mỗi khi fix bug / ship feature.

Mục đích: sau khi reload Streamlit, user nhìn sidebar biết code đã update
hay vẫn dùng bản cache cũ.

Format: SemVer-ish — MAJOR.MINOR.PATCH
  - MAJOR: thay đổi breaking (schema, API)
  - MINOR: feature mới (tab mới, agent mới)
  - PATCH: bug fix / UI tweak
"""

from __future__ import annotations

__version__ = "0.5.10"
