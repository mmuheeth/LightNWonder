"""Configuration *data* that ships with the code, and the readers for it.

Not to be confused with :mod:`app.core.config`, which is runtime settings read
from the environment. The split is deliberate:

- :mod:`app.core.config` answers "how is this deployment configured?" -- hosts,
  timeouts, feature switches. It changes per machine and lives in ``.env``.
- This package answers "what does this game look like?" -- the per-game names,
  regions and targets that are the same everywhere the app runs. It is versioned
  with the source because it describes the games, not the deployment.

Data files sit beside the module that reads them, so a new game is one JSON file
in :mod:`app.config.game_config` and no code change at all.
"""
