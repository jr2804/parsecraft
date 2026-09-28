"""Pandoc backend family — office/ODF/EPUB/RTF conversion (ADR-0005).

Entry-point module stays light; the heavy impl loads through
``importlib`` at instantiation (``_impl``). The GPL pandoc binary is a
system dependency of the optional ``pandoc`` extra — never core, never
``mise dev``/CI.
"""
