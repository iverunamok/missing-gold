# Plug-in commands

Each file here is one command. The CLI imports every module in this folder
and calls `register(subparsers)`.

```python
from ytlib import Api, load_key, fetch_videos, fetch_channels, ledger_read, thumb, fmt_int

def cmd_example(args):
    api = Api(load_key())
    ...

def register(sub):
    p = sub.add_parser("example", help="one line")
    p.add_argument("thing")
    p.set_defaults(func=cmd_example)
```

Rules: standard library only (Pillow optional), Python 3.9 syntax, every
API call through `Api.get` or `Api.paged` so the quota ledger stays right,
read-only unless the command name says otherwise, tests in `test_<name>.py`
at the repo root that run with no key and no network.
