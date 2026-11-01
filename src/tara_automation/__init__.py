"""tara_automation — config-driven automation around PowerGEM TARA.

Package layout:

    machine.py    Per-machine paths (tara.exe, scratch dirs) from settings/machine.json
    config.py     (planned) Study/run configuration — migrate from reference tara_wrapper
    comm.py       (planned) TARA command-file syntax builders
    runner.py     (planned) Run-numbered dirs, master comm + .bat, engine launch
    results/      (planned) Parsers for TARA CSV outputs -> tidy DataFrames
    analyses/     (planned) One module per analysis type
"""

__version__ = "0.1.0"
