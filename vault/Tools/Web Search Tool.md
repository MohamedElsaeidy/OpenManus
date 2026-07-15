---
tags: [tool, search]
type: class
source_path: app/tool/web_search.py
---

# Web Search Tool

`WebSearch` (defined in `app/tool/web_search.py`) is a search retrieval tool. It provides search indexing access to Google, DuckDuckGo, and Wikipedia.

## Integration
- **Query Processing**: Normalizes queries, removes syntax modifiers, and executes searches through configured API endpoints.
- **Content Parsing**: Parses HTML response trees to extract clean Markdown text paragraphs and links, bypassing cookie notices and page banners.

## Links
- [[Tools MOC]]
- [[Browser Use Tool]]
