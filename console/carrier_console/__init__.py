"""Carrier operator console (Tisya's lane). Separate uv project, like carriers/.

Deliberately does not import freight_exchange: the console is a different actor
with its own process and its own Tiger connection, and keeping it uncoupled means
Parth's package can change without breaking this one.
"""
