"""
gateway — The player webapp's server: HTTPS, accounts and sessions, the
webapp's files, and the bridge to the Tablescreen app.

A separate program (``python -m gateway``, later in a container). It never
imports the app and the app never imports it: the two only talk over the
bridge (``bridge_protocol.py``), and the app checks everything it hears.
Passwords and sessions stay here.
"""
