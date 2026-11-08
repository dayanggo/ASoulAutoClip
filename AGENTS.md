# Repository working rules

- Main pipeline implementation lives in `core/`; commands and maintenance utilities live in `scripts/`.
- Root Python/CMD entry points are compatibility shims. Do not add business logic to them.
- Keep optional original workflows under `legacy/`; do not quietly route the default pipeline into them.
- Resolve project resources through `core/paths.py`. Do not hardcode a developer's absolute path or rely on the caller's working directory.
- Public config templates belong in `config/examples/`; private profiles belong in ignored `config/local/`.
- Prompt changes belong in `resources/prompts/`, dictionary rules in `resources/dictionaries/asr_dict.txt`.
- New runs default to `outputs/`. Historical `ouput/` and `workspace/` data remain local; do not move or delete existing recordings, models, caches or outputs as part of source cleanup.
- Preserve subtitle preferences: youth font, size 84, uniform yellow, original bottom position, and phrase timing based on verified word timestamps.
- Model scores are editorial judgments, not accuracy percentages. Do not weaken evidence or completeness validation to fill a clip quota.
- Verification: `python -X utf8 -m unittest discover -s tests -q`, then `python -X utf8 -m scripts.check_repository`.
- Tests must not call paid APIs, download ASR weights or modify real recording outputs. Use local fixtures and mocked model responses.
- Update the appropriate guide when commands, config or output behavior changes. Follow `CONTRIBUTING.md` and `docs/architecture/project-structure.md`.
