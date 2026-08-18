"""Application configuration.

The package keeps two kinds of configuration together but separate:

- :mod:`app.config.runtime` answers "how is this deployment configured?" --
  hosts, timeouts and feature switches loaded from the environment.
- :mod:`app.config.game_config` answers "what does this game look like?" --
  versioned names, regions and targets loaded from the shipped JSON files.

:mod:`app.core.config` remains as a compatibility import for the runtime
settings. Data files sit beside the module that reads them, so a new game is
one JSON file and no code change at all.
"""
