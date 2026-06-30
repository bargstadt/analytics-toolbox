"""Compatibility shim for usaddress-scourgify's hard dependency on `geocoder`.

scourgify's normalize.py does a bare, unconditional `import geocoder` at
module load time, even though `geocoder` is only actually used inside
get_geocoder_normalized_addr() — a function this codebase never calls (it
calls Google's geocoder API, which would violate the no-third-party-data
rule for this module). That means importing scourgify at all currently
requires `geocoder` to be installed and importable, regardless of use.

`geocoder` itself pulls in `click`, `future`, and `ratelim` — `future` and
`ratelim` are both effectively abandoned, and `geocoder` has a known
invalid-escape-sequence issue (DenisCarriere/geocoder#409) that is
currently just a SyntaxWarning but is the kind of thing CPython has a
history of eventually promoting to a hard SyntaxError. An upstream fix
exists (GreenBuildingRegistry/usaddress-scourgify#35, "Make geocoder an
optional dependency") but is unmerged and sits on a low-activity project,
so depending on that branch directly would just trade one fragile
dependency for another.

Since the only thing scourgify's import statement needs is something
named `geocoder` to exist in `sys.modules`, this module satisfies that
with an empty stub. Verified directly: with the real `geocoder` package
(and its dependencies) fully uninstalled, this stub still lets
`normalize_address_record` run correctly for every case this codebase
relies on. Imported once, at the top of `geospatial/__init__.py`, which
Python always fully executes before any submodule in this package — so
every submodule gets this protection without needing to import it itself.
"""

import sys
import types

if "geocoder" not in sys.modules:
    sys.modules["geocoder"] = types.ModuleType("geocoder")
