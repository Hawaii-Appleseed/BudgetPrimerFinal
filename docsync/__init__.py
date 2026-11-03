"""The docsync engine: a report's prose (content.md) and layout (layout.json)
rendered by the report's own Python, in the browser through Pyodide and in a
repo's build alike.

    content.py    [[key]] slots in content.md
    layout.py     positions, shapes and styles as data
    blocks.py     building blocks any renderer can import
    registry.py   docsync.yml -> Binding objects

(Until 2026-10-30 this package also held a two-way Google Docs sync, with
bind, doctor, sync, state and fragment modules. It was one-way and manual
from 2026-09: File > Import text from the Doc is docimport.py.)
"""
from .content import Content, ContentError                       # noqa: F401
from .registry import Binding, load_registry, RegistryError      # noqa: F401
