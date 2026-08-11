Joomlatools Package Normalizer
=========================================

Converts a Joomlatools wrapper installer ZIP into a format compatible
with Watchful.li and other remote Joomla management tools.

Two output modes:

**Wrapper mode** (default)
- Produces a single standard Joomla `type="component"` ZIP
- Uses a temporary `com_joomlatools_installer` wrapper, matching the vendor install primitive
- Recreates the wrapper's preflight checks (PHP, Joomla, DOCman version, compat plugin, framework downgrade)
- Installs the payload synchronously during `postflight()`
- Self-uninstalls the temporary wrapper component after the payload finishes
- Avoids the browser-only AJAX installer flow that breaks remote bulk installers
- This method has been fully tested

**Split mode** (`--split`)
- Outputs individual extension ZIPs, numbered for install order
- Each ZIP is byte-for-byte identical to the original Joomlatools payload
- No temporary wrapper component is created
- Install one at a time via Watchful.li, in numbered order
- **Note on missing checks**: Because this installs the extensions directly, it bypasses the wrapper's preflight checks. Ensure your environment meets these requirements manually:
  - Minimum PHP version (e.g. PHP 7.3+)
  - Minimum Joomla version (e.g. Joomla 3.10+)
  - Joomla 5/6 backward compatibility plugin is enabled
  - No ancient versions of DOCman (< 2.1.5) are installed
  - This method has not been fully tested

Verified against com_docman_v6.2.x, com_fileman_6.x, com_logman_6.x

Usage:
  python3 joomlatools_normalizer.py /path/to/input.zip
  python3 joomlatools_normalizer.py /path/to/input.zip /path/to/output.zip
  python3 joomlatools_normalizer.py --split /path/to/input.zip
  python3 joomlatools_normalizer.py --split /path/to/input.zip /path/to/output_dir
