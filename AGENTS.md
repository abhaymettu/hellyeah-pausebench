# AGENTS.md

Simulation study of ad pause, negate and fatigue rules. Python 3.11+, managed with uv.

- Setup: `uv sync`
- Run: `uv run python -m pausebench.r1_negate`, `uv run python -m pausebench.r2_cpa_guard --all`,
  `uv run python -m pausebench.r3_fatigue`, then `uv run python -m pausebench.figures`
- Outputs: `results/*.txt` and `results/*.json`, charts in `docs/`
- Rules of the repo: tune any threshold on seeds 0 to 9 only and score on seeds 100 and up. Any change to rules,
  metrics or scenarios after the preregistration commit gets a numbered entry at the bottom of `PREREG.md` saying why
  and which way it cuts.
- No network access is needed. Nothing here reads real account data.
