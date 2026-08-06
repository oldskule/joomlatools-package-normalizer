Joomlatools DOCman Package Normalizer
=========================================
Converts a Joomlatools wrapper installer ZIP into a format compatible
with Watchful.li and other remote Joomla management tools.
Two output modes:
**Package mode** (default)
- Produces a single standard Joomla `type="package"` ZIP
- Recreates the wrapper's preflight checks (PHP, Joomla, DOCman version, compat plugin)
- Preserves success URL, success text, and wrapper metadata
- Postflight auto-cleans the pkg_* entry from Extension Manager
**Split mode** (`--split`)
- Outputs individual extension ZIPs, numbered for install order
- Each ZIP is byte-for-byte identical to the original Joomlatools payload
- No pkg_* entry is created — site looks exactly as if the original installer was used
- Install one at a time via Watchful.li, in numbered order
Verified against com_docman_v6.x
Usage:
  python3 joomlatools_normalizer.py /path/to/input.zip
  python3 joomlatools_normalizer.py /path/to/input.zip /path/to/output.zip
  python3 joomlatools_normalizer.py --split /path/to/input.zip
  python3 joomlatools_normalizer.py --split /path/to/input.zip /path/to/output_dir
