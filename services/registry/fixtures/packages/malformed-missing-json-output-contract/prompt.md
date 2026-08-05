# Prompt — fixture function

You are a fixture function used only to exercise
`services/registry/validate_package.py`. This copy of the `good` fixture
deliberately omits the "Output contract" section — F-PROMPT-OUTPUT-CONTRACT
(5 Aug 2026) — to prove `check_prompt_json_contract` rejects a prompt.md
that never instructs the model to return JSON.

Return the string OK.
