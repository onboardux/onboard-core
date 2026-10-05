# Vendored third-party notices

Licence texts the packed `adopt` binary must carry but that no installed
distribution's metadata supplies. `scripts/third_party_notices.py` appends them
to `THIRD_PARTY_NOTICES.txt`, which `release.yml` embeds in every binary.

| File | What it covers | Source (fetched 2026-10-01) | SHA-256 |
|---|---|---|---|
| `cpython-3.12-license.rst` | CPython's "Licenses and Acknowledgements for Incorporated Software": OpenSSL, expat, libffi, zlib, libmpdec and the rest the interpreter links | `https://raw.githubusercontent.com/python/cpython/3.12/Doc/license.rst` | `341832873fd316a37927e79385093fbbfd40a467428480835fe435a80cadf4e5` |
| `bzip2-LICENSE.txt` | libbzip2, linked by `_bz2` | `https://sourceware.org/git/?p=bzip2.git;a=blob_plain;f=LICENSE;hb=HEAD` | `c6dbbf828498be844a89eaa3b84adbab3199e342eb5cb2ed2f0d4ba7ec0f38a3` |

The CPython file is per minor version. The generator refuses to run on an
interpreter whose `major.minor` has no file here, because the incorporated
software differs between minors. A Python upgrade fetches the matching file in
the same change.

SQLite and liblzma are in the public domain (liblzma is 0BSD from xz 5.6), and
the Microsoft Visual C++ runtime is redistributed under its own redistribution
terms, so none of them adds a text here.
