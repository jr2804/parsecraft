"""Built-in native backends: dependency-light document parsers.

Entry-point modules stay light: each exposes a ``factory`` (descriptor +
``__call__``) and defers optional/heavy imports to ``import_module`` seams
(:mod:`parsecraft.backends.native.pdf_text` for PyMuPDF). Nothing is
imported here — the registry loads entry modules individually.
"""
