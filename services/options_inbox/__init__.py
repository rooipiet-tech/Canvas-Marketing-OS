"""options_inbox - the service that makes 'humans only approve options' survivable.

Modules
- cards.py        build + validate OptionCards against the contract and the autonomy matrix
- policy.py       approval budget, timeouts, standing permissions, non-negotiable guards
- teams_render.py Adaptive Card renderer: N sections x 3 buttons + rejection picker
- store.py        decision store interface (Postgres vault in production; in-memory here)
"""
