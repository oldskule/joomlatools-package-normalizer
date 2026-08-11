#!/usr/bin/env python3
"""
Joomlatools package normalizer

Converts a Joomlatools wrapper installer ZIP into a standard Joomla package ZIP,
while preserving the real payload archives byte-for-byte and recreating the
wrapper's important preflight checks and success redirect behavior inside a
normal package installer script.

Tested against:
- com_docman_v6.0.3.zip

Usage:
    python3 joomlatools_normalizer_v2.py /path/to/input.zip [/path/to/output.zip]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, asdict
from typing import List, Optional


@dataclass
class ExtensionPackage:
    title: str
    type: str
    group: str
    element: str
    version: str
    archive_name: str
    manifest_path: str
    payload_path: str
    payload_root: str
    source_sha256: str


@dataclass
class WrapperMetadata:
    min_php: str
    min_joomla: str
    old_docman_floor: str
    success_url: str
    success_text: str
    creation_date: str
    author: str
    author_email: str
    author_url: str
    copyright: str
    license: str


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def xml_text(root: ET.Element, name: str, default: str = '') -> str:
    value = root.findtext(name)
    return value.strip() if value else default


def find_package_manifest(names: List[str], payload_root: str) -> Optional[str]:
    root_depth = payload_root.count('/')
    candidates = [
        name for name in names
        if name.startswith(payload_root)
        and name.endswith('.xml')
        and not name.endswith('/language/en-GB/en-GB.sys.xml')
    ]
    if not candidates:
        return None

    def score(name: str) -> tuple:
        rel = name[len(payload_root):]
        basename = os.path.basename(name)
        preferred = 0
        if rel.count('/') == 0:
            preferred = -10
        elif basename.startswith('com_') or basename.startswith('pkg_') or basename.startswith('plg_'):
            preferred = -5
        return (rel.count('/'), preferred, len(name), name)

    return sorted(candidates, key=score)[0]


def build_extension_definition(source_zip: zipfile.ZipFile, package_path: str, manifest_path: str) -> ExtensionPackage:
    xml_bytes = source_zip.read(manifest_path)
    xml = ET.fromstring(xml_bytes)
    ext_type = xml.attrib.get('type', '').strip()
    group = xml.attrib.get('group', '').strip()
    title = xml_text(xml, 'name')
    version = xml_text(xml, 'version')
    element = title

    if ext_type == 'plugin':
        match = re.match(r'^plg_([a-z0-9_-]+)_(.+)$', title, re.I)
        if match:
            group = group or match.group(1)
            element = match.group(2)
        archive_name = f'plg_{group}_{element}.zip'
    else:
        archive_name = f'{title}.zip'

    return ExtensionPackage(
        title=title,
        type=ext_type,
        group=group,
        element=element,
        version=version,
        archive_name=archive_name,
        manifest_path=manifest_path,
        payload_path=package_path,
        payload_root=f'payload/{package_path.strip("/")}/',
        source_sha256=sha256_bytes(xml_bytes),
    )


def write_subzip_bytes(source_zip: zipfile.ZipFile, ext: ExtensionPackage) -> bytes:
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    try:
        added = 0
        with zipfile.ZipFile(tmp.name, 'w', compression=zipfile.ZIP_DEFLATED) as out:
            for info in source_zip.infolist():
                name = info.filename
                if not name.startswith(ext.payload_root) or name == ext.payload_root:
                    continue
                relative = name[len(ext.payload_root):]
                if not relative:
                    continue
                if name.endswith('/'):
                    zinfo = zipfile.ZipInfo(relative if relative.endswith('/') else relative + '/')
                    zinfo.date_time = info.date_time
                    zinfo.external_attr = info.external_attr
                    zinfo.compress_type = zipfile.ZIP_STORED
                    out.writestr(zinfo, b'')
                else:
                    zinfo = zipfile.ZipInfo(relative)
                    zinfo.date_time = info.date_time
                    zinfo.external_attr = info.external_attr
                    zinfo.comment = info.comment
                    zinfo.extra = info.extra
                    zinfo.compress_type = zipfile.ZIP_DEFLATED
                    out.writestr(zinfo, source_zip.read(name))
                    added += 1
        if added == 0:
            fail(f'No files found under payload root: {ext.payload_root}')
        with open(tmp.name, 'rb') as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def parse_wrapper_metadata(source_zip: zipfile.ZipFile, payload_manifest: dict) -> WrapperMetadata:
    wrapper_xml = ET.fromstring(source_zip.read('joomlatools_installer.xml'))
    wrapper_script = source_zip.read('script.php').decode('utf-8', 'ignore')

    def rx(pattern: str, default: str) -> str:
        match = re.search(pattern, wrapper_script, re.S)
        return match.group(1) if match else default

    min_php = rx(r"\$minimum_php_version\s*=\s*'([^']+)'", '7.3')
    min_joomla = rx(r"version_compare\(JVERSION,\s*'([^']+)',\s*'<'\)", '3.10')
    old_docman_floor = rx(r"version_compare\(\$docman_version,\s*'([^']+)',\s*'<'\)", '2.1.5')

    return WrapperMetadata(
        min_php=min_php,
        min_joomla=min_joomla,
        old_docman_floor=old_docman_floor,
        success_url=str(payload_manifest.get('success_url') or 'index.php'),
        success_text=str(payload_manifest.get('success_text') or 'Go to extension'),
        creation_date=xml_text(wrapper_xml, 'creationDate'),
        author=xml_text(wrapper_xml, 'author', 'Joomlatools'),
        author_email=xml_text(wrapper_xml, 'authorEmail', ''),
        author_url=xml_text(wrapper_xml, 'authorUrl', ''),
        copyright=xml_text(wrapper_xml, 'copyright', ''),
        license=xml_text(wrapper_xml, 'license', ''),
    )


def build_component_manifest(meta: WrapperMetadata) -> str:
    return "\n".join([
        '<?xml version="1.0" encoding="utf-8"?>',
        '<extension type="component" version="3.0" method="upgrade">',
        '    <name>com_joomlatools_installer</name>',
        f'    <author>{meta.author}</author>',
        f'    <creationDate>{meta.creation_date}</creationDate>',
        f'    <copyright>{meta.copyright}</copyright>',
        f'    <license>{meta.license}</license>',
        f'    <authorEmail>{meta.author_email}</authorEmail>',
        f'    <authorUrl>{meta.author_url}</authorUrl>',
        '    <version>1.0.0</version>',
        '    <description>Standard Joomla component wrapper generated from a Joomlatools installer for remote-safe synchronous payload installs.</description>',
        '    <scriptfile>script.php</scriptfile>',
        '    <administration>',
        '        <files>',
        '            <filename>joomlatools_installer.php</filename>',
        '        </files>',
        '    </administration>',
        '    <files>',
        '        <filename>joomlatools_installer.php</filename>',
        '        <folder>payload</folder>',
        '    </files>',
        '</extension>',
        '',
    ])


PHP_WRAPPER_SCRIPT_TEMPLATE = r'''<?php
defined('_JEXEC') or die;

class com_joomlatools_installerInstallerScript
{{
    protected $minimumPhpVersion = '{min_php}';
    protected $minimumJoomlaVersion = '{min_joomla}';
    protected $oldDocmanFloor = '{old_docman_floor}';
    protected $successUrl = '{success_url}';
    protected $successText = '{success_text}';

    protected function getAbortHandler($adapter)
    {{
        if (is_object($adapter) && method_exists($adapter, 'getParent')) {{
            $parent = $adapter->getParent();
            if (is_object($parent) && method_exists($parent, 'abort')) {{
                return $parent;
            }}
        }}

        return $adapter;
    }}

    protected function abortInstall($adapter, $message)
    {{
        $target = $this->getAbortHandler($adapter);

        if (is_object($target) && method_exists($target, 'abort')) {{
            $target->abort($message);
        }}

        try {{
            \Joomla\CMS\Factory::getApplication()->enqueueMessage($message, 'error');
        }} catch (\Throwable $e) {{
        }} catch (\Exception $e) {{
        }}

        return false;
    }}

    protected function getComponentVersion($component)
    {{
        try {{
            $db = \Joomla\CMS\Factory::getDbo();
            $query = $db->getQuery(true)
                ->select($db->quoteName('manifest_cache'))
                ->from($db->quoteName('#__extensions'))
                ->where($db->quoteName('type') . ' = ' . $db->quote('component'))
                ->where($db->quoteName('element') . ' = ' . $db->quote('com_' . $component));

            $result = $db->setQuery($query)->loadResult();

            if ($result) {{
                $manifest = new \Joomla\Registry\Registry($result);
                return $manifest->get('version', null);
            }}
        }} catch (\Throwable $e) {{
        }} catch (\Exception $e) {{
        }}

        return null;
    }}

    protected function getSourcePath($adapter)
    {{
        $target = $this->getAbortHandler($adapter);

        if (is_object($target) && method_exists($target, 'getPath')) {{
            return $target->getPath('source');
        }}

        if (is_object($adapter) && method_exists($adapter, 'getPath')) {{
            return $adapter->getPath('source');
        }}

        return null;
    }}

    protected function getJoomlaVersion()
    {{
        if (defined('JVERSION')) {{
            return JVERSION;
        }}

        if (class_exists('\\Joomla\\CMS\\Version')) {{
            try {{
                $version = new \Joomla\CMS\Version();
                return $version->getShortVersion();
            }} catch (\Throwable $e) {{
            }} catch (\Exception $e) {{
            }}
        }}

        return '0.0.0';
    }}

    protected function isCompatPluginEnabled($joomlaVersion)
    {{
        if (!class_exists('\\Joomla\\CMS\\Plugin\\PluginHelper')) {{
            return true;
        }}

        if (version_compare($joomlaVersion, '5.0', '<')) {{
            return true;
        }}

        if (version_compare($joomlaVersion, '6.0', '<')) {{
            return \Joomla\CMS\Plugin\PluginHelper::isEnabled('behaviour', 'compat');
        }}

        return \Joomla\CMS\Plugin\PluginHelper::isEnabled('behaviour', 'compat6');
    }}

    protected function validateFrameworkVersion($adapter)
    {{
        $source = $this->getSourcePath($adapter);

        if (!$source) {{
            return true;
        }}

        $payloadKoowa = rtrim($source, '/\\') . '/payload/framework/libraries/joomlatools/library/koowa.php';
        $currentKoowa = JPATH_LIBRARIES . '/joomlatools/library/koowa.php';

        if (!is_file($payloadKoowa) || !is_file($currentKoowa)) {{
            return true;
        }}

        $payloadVersion = null;
        $currentVersion = null;

        if (preg_match("#const\s+VERSION\s+=\s+'(.*?)'#i", file_get_contents($payloadKoowa), $matches)) {{
            $payloadVersion = $matches[1];
        }}

        if (preg_match("#const\s+VERSION\s+=\s+'(.*?)'#i", file_get_contents($currentKoowa), $matches)) {{
            $currentVersion = $matches[1];
        }}

        if ($payloadVersion && $currentVersion && version_compare($payloadVersion, $currentVersion, '<')) {{
            $message = sprintf(
                'Your site is running Joomlatools Framework %s. This package ships version %s which is older and cannot be installed. Please use a newer version of the package.',
                $currentVersion,
                $payloadVersion
            );

            return $this->abortInstall($adapter, $message);
        }}

        return true;
    }}

    protected function loadPayloadManifest($adapter)
    {{
        $source = $this->getSourcePath($adapter);

        if (!$source) {{
            return null;
        }}

        $manifestPath = rtrim($source, '/\\') . '/payload/manifest.json';

        if (!is_file($manifestPath)) {{
            return null;
        }}

        $manifest = json_decode(file_get_contents($manifestPath));

        return (is_object($manifest) && !empty($manifest->packages) && is_array($manifest->packages)) ? $manifest : null;
    }}

    protected function installPayload($adapter, $manifest)
    {{
        $source = $this->getSourcePath($adapter);

        if (!$source) {{
            return $this->abortInstall($adapter, 'The Joomlatools installer source path could not be resolved.');
        }}

        foreach ($manifest->packages as $package) {{
            if (empty($package->path)) {{
                return $this->abortInstall($adapter, 'The Joomlatools installer payload contains an invalid package.');
            }}

            $path = rtrim($source, '/\\') . '/payload/' . trim($package->path, '/\\');
            $title = !empty($package->title) ? $package->title : $package->path;

            if (!is_dir($path)) {{
                return $this->abortInstall($adapter, sprintf("The '%s' installer payload could not be found.", $title));
            }}

            $payloadInstaller = new \Joomla\CMS\Installer\Installer();

            if (method_exists($payloadInstaller, 'setDatabase')) {{
                $payloadInstaller->setDatabase(\Joomla\CMS\Factory::getDbo());
            }}

            if (!$payloadInstaller->install($path)) {{
                return $this->abortInstall($adapter, sprintf("The '%s' installer payload failed to install.", $title));
            }}
        }}

        return true;
    }}

    protected function setSuccessState($adapter, $manifest)
    {{
        $successUrl = isset($manifest->success_url) && $manifest->success_url ? $manifest->success_url : $this->successUrl;
        $successText = isset($manifest->success_text) && $manifest->success_text ? $manifest->success_text : $this->successText;

        try {{
            $app = \Joomla\CMS\Factory::getApplication();
            $app->setUserState('com_installer.redirect_url', $successUrl);
            $app->enqueueMessage($successText, 'message');
        }} catch (\Throwable $e) {{
        }} catch (\Exception $e) {{
        }}

        try {{
            $target = $this->getAbortHandler($adapter);
            if (is_object($target) && method_exists($target, 'setRedirectUrl')) {{
                $target->setRedirectUrl($successUrl);
            }}
        }} catch (\Throwable $e) {{
        }} catch (\Exception $e) {{
        }}
    }}

    protected function selfDestruct()
    {{
        try {{
            $db = \Joomla\CMS\Factory::getDbo();
            $query = $db->getQuery(true)
                ->select($db->quoteName('extension_id'))
                ->from($db->quoteName('#__extensions'))
                ->where($db->quoteName('type') . ' = ' . $db->quote('component'))
                ->where($db->quoteName('element') . ' = ' . $db->quote('com_joomlatools_installer'));

            $extensionId = (int) $db->setQuery($query, 0, 1)->loadResult();

            if ($extensionId > 0) {{
                $installer = new \JInstaller();

                if (method_exists($installer, 'setDatabase')) {{
                    $installer->setDatabase($db);
                }}

                $installer->uninstall('component', $extensionId, 1);
            }}
        }} catch (\Throwable $e) {{
        }} catch (\Exception $e) {{
        }}
    }}

    public function preflight($type, $adapter)
    {{
        $docmanVersion = $this->getComponentVersion('docman');

        if ($docmanVersion && version_compare($docmanVersion, $this->oldDocmanFloor, '<')) {{
            $warning = 'Your site has DOCman %s installed. Please upgrade DOCman first to 2.1.6 and then to 3.0 in this order. '
                . 'This will ensure your data is properly migrated. '
                . 'We advise you to read our <a target="_blank" href="https://www.joomlatools.com/extensions/docman/documentation/upgrading/">upgrading guide</a>.';

            return $this->abortInstall($adapter, sprintf($warning, $docmanVersion));
        }}

        if (version_compare(PHP_VERSION, $this->minimumPhpVersion, '<')) {{
            $message = sprintf(
                'Your server is running PHP %s. This version is end of life and no longer supported. It contains possible bugs and security vulnerabilities. '
                . 'Please contact your host and ask them to upgrade PHP to at least %s version on your server.',
                PHP_VERSION,
                $this->minimumPhpVersion
            );

            return $this->abortInstall($adapter, $message);
        }}

        $joomlaVersion = $this->getJoomlaVersion();

        if (version_compare($joomlaVersion, $this->minimumJoomlaVersion, '<')) {{
            $message = sprintf(
                'Your site is running Joomla %s which is an unsupported version. Please upgrade Joomla to the latest version or at least to Joomla %s first.',
                $joomlaVersion,
                $this->minimumJoomlaVersion
            );

            return $this->abortInstall($adapter, $message);
        }}

        if (!$this->isCompatPluginEnabled($joomlaVersion)) {{
            $pluginName = version_compare($joomlaVersion, '6.0', '>=')
                ? 'Behaviour - Backward Compatibility 6'
                : 'Behaviour - Backward Compatibility';
            $url = 'index.php?option=com_plugins&view=plugins&filter_folder=behaviour';
            $message = 'This component requires \'' . $pluginName . '\' plugin to be enabled. '
                . 'Please go to <a href="' . $url . '">Plugin Manager</a>, enable <strong>' . $pluginName . '</strong> and try again.';

            return $this->abortInstall($adapter, $message);
        }}

        if (!$this->validateFrameworkVersion($adapter)) {{
            return false;
        }}

        return true;
    }}

    public function postflight($type, $adapter)
    {{
        if ($type === 'discover_install') {{
            return true;
        }}

        if ($type !== 'install' && $type !== 'update') {{
            return true;
        }}

        $manifest = $this->loadPayloadManifest($adapter);

        if (!$manifest) {{
            return $this->abortInstall($adapter, 'The Joomlatools installer payload manifest is invalid or missing.');
        }}

        if (!$this->installPayload($adapter, $manifest)) {{
            return false;
        }}

        $this->setSuccessState($adapter, $manifest);
        $this->selfDestruct();

        return true;
    }}
}}
'''


def php_single_quote(value: str) -> str:
    return value.replace('\\', '\\\\').replace("'", "\\'")


def build_component_script(meta: WrapperMetadata) -> str:
    return PHP_WRAPPER_SCRIPT_TEMPLATE.format(
        min_php=php_single_quote(meta.min_php),
        min_joomla=php_single_quote(meta.min_joomla),
        old_docman_floor=php_single_quote(meta.old_docman_floor),
        success_url=php_single_quote(meta.success_url),
        success_text=php_single_quote(meta.success_text),
    )


def build_readme(extensions: List[ExtensionPackage], meta: WrapperMetadata, output_name: str) -> str:
    rows = [
        f'Generated wrapper: {output_name}',
        'Wrapper type: standard Joomla component',
        'Temporary component: com_joomlatools_installer',
        '',
        'Embedded payload installers:',
    ]
    for ext in extensions:
        rows.append(f'- {ext.payload_path} ({ext.type}: {ext.title} {ext.version})')
    rows += [
        '',
        'Wrapper behavior recreated in script.php:',
        f'- DOCman old-version floor check: < {meta.old_docman_floor}',
        f'- Minimum PHP version check: {meta.min_php}',
        f'- Minimum Joomla version check: {meta.min_joomla}',
        '- Framework downgrade protection',
        '- Joomla 5/6 backward compatibility plugin requirement',
        f'- Success redirect URL: {meta.success_url}',
        f'- Success message: {meta.success_text}',
        '- Synchronous payload install during postflight()',
        '- Automatic self-uninstall of com_joomlatools_installer after payload install',
        '',
        'Wrapper behavior intentionally omitted:',
        '- Browser-only AJAX progress UI',
        '- Redirect to a temporary installer screen before payload install',
    ]
    return '\n'.join(rows) + '\n'


def build_install_order_txt(extensions: List[ExtensionPackage], meta: WrapperMetadata, source_name: str) -> str:
    lines = [
        'JOOMLATOOLS EXTENSION INSTALL ORDER',
        '=' * 40,
        '',
        f'Generated from: {source_name}',
        '',
        'Install these extensions one at a time via Joomla Extension Manager',
        'or Watchful.li, in the order listed below.',
        '',
        'IMPORTANT: Install in this exact order — the framework/plugin must',
        'be installed before the component.',
        '',
        'Install Order:',
    ]
    for i, ext in enumerate(extensions, 1):
        lines.append(f'  {i}. {i}_{ext.archive_name}  ({ext.type}: {ext.title} {ext.version})')
    lines += [
        '',
        'Notes:',
        '- Each ZIP is byte-for-byte identical to the original Joomlatools payload',
        '- No package (pkg_*) entry will be created in Extension Manager',
        '- The site will look exactly as if the original Joomlatools installer was used',
        '',
        'Original wrapper preflight checks (not included in individual ZIPs):',
        f'  - Minimum PHP version: {meta.min_php}',
        f'  - Minimum Joomla version: {meta.min_joomla}',
        f'  - DOCman old-version floor: < {meta.old_docman_floor}',
        '  - Joomla 5/6 backward compatibility plugin requirement',
        '',
    ]
    return '\n'.join(lines) + '\n'


def build_component_entrypoint() -> str:
    return "\n".join([
        '<?php',
        "defined('_JEXEC') or die;",
        '',
        '// This temporary component exists only so Joomla can register the wrapper',
        '// and run script.php, which installs the real payload extensions and',
        '// then uninstalls this wrapper again.',
        '',
    ])


def parse_args() -> tuple:
    """Parse command-line arguments, returning (input_zip, output_path, split_mode)."""
    args = [a for a in sys.argv[1:] if a != '--split']
    split_mode = '--split' in sys.argv

    if not args:
        script = os.path.basename(sys.argv[0])
        fail(
            f'Usage:\n'
            f'  {script} /path/to/input.zip [/path/to/output.zip]\n'
            f'  {script} --split /path/to/input.zip [/path/to/output_dir]\n'
            f'\n'
            f'Options:\n'
            f'  --split   Output individual extension ZIPs instead of a single wrapper ZIP.\n'
            f'            This avoids creating a temporary installer component.'
        )

    input_zip = args[0]
    output_path = args[1] if len(args) > 1 else None
    return input_zip, output_path, split_mode


def main() -> int:
    input_zip, output_path, split_mode = parse_args()

    if not os.path.isfile(input_zip):
        fail(f'Input ZIP not found: {input_zip}')

    with zipfile.ZipFile(input_zip, 'r') as source_zip:
        if 'payload/manifest.json' not in source_zip.namelist():
            fail('This ZIP does not look like a supported Joomlatools wrapper package.')

        payload_manifest = json.loads(source_zip.read('payload/manifest.json').decode('utf-8'))
        package_entries = payload_manifest.get('packages')
        if not isinstance(package_entries, list) or not package_entries:
            fail('Invalid payload/manifest.json structure.')

        meta = parse_wrapper_metadata(source_zip, payload_manifest)

        extensions: List[ExtensionPackage] = []
        main_component: Optional[ExtensionPackage] = None
        extension_bytes = {}

        for entry in package_entries:
            package_path = str(entry.get('path', '')).strip('/')
            if not package_path:
                fail('A payload package entry is missing its path.')
            payload_root = f'payload/{package_path}/'
            manifest_path = find_package_manifest(source_zip.namelist(), payload_root)
            if manifest_path is None:
                fail(f'Could not find manifest XML in {payload_root}')
            ext = build_extension_definition(source_zip, package_path, manifest_path)
            ext_bytes = write_subzip_bytes(source_zip, ext)
            extension_bytes[ext.archive_name] = ext_bytes
            if ext.type == 'component' and main_component is None:
                main_component = ext
            extensions.append(ext)

        main_name = main_component.element if main_component else 'joomlatools'

        # ── Split mode: output individual extension ZIPs ──────────────
        if split_mode:
            if output_path is None:
                base = os.path.splitext(os.path.basename(input_zip))[0]
                output_path = os.path.join(
                    os.path.dirname(os.path.abspath(input_zip)),
                    f'{base}_split'
                )

            os.makedirs(output_path, exist_ok=True)

            report = {
                'mode': 'split',
                'source_file': os.path.basename(input_zip),
                'source_sha256': sha256_file(input_zip),
                'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'wrapper_metadata': asdict(meta),
                'install_order': [],
            }

            for i, ext in enumerate(extensions, 1):
                numbered_name = f'{i}_{ext.archive_name}'
                out_file = os.path.join(output_path, numbered_name)
                with open(out_file, 'wb') as fh:
                    fh.write(extension_bytes[ext.archive_name])

                report['install_order'].append({
                    'step': i,
                    'filename': numbered_name,
                    **asdict(ext),
                    'archive_sha256': sha256_bytes(extension_bytes[ext.archive_name]),
                    'archive_size': len(extension_bytes[ext.archive_name]),
                })
                print(f'  {i}. {out_file}')

            # Write install order instructions
            order_file = os.path.join(output_path, 'install_order.txt')
            with open(order_file, 'w') as fh:
                fh.write(build_install_order_txt(extensions, meta, os.path.basename(input_zip)))

            # Write conversion report
            report_file = os.path.join(output_path, 'conversion-report.json')
            with open(report_file, 'w') as fh:
                fh.write(json.dumps(report, indent=2))

            print(f'\nSplit output: {output_path}')
            print(f'Install the {len(extensions)} ZIPs in numbered order.')
            return 0

        # ── Wrapper mode (default): single component ZIP ──────────────
        output_zip = output_path
        if output_zip is None:
            output_zip = os.path.join(
                os.path.dirname(os.path.abspath(input_zip)),
                f'{main_name}_normalized.zip'
            )

        report = {
            'mode': 'component_wrapper',
            'component': 'com_joomlatools_installer',
            'script_class': 'com_joomlatools_installerInstallerScript',
            'source_file': os.path.basename(input_zip),
            'source_sha256': sha256_file(input_zip),
            'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'wrapper_metadata': asdict(meta),
            'extensions': [],
        }

        for ext in extensions:
            report['extensions'].append({
                **asdict(ext),
                'archive_sha256': sha256_bytes(extension_bytes[ext.archive_name]),
                'archive_size': len(extension_bytes[ext.archive_name]),
            })

        with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as out:
            for info in source_zip.infolist():
                name = info.filename
                if not (name == 'payload/' or name.startswith('payload/')):
                    continue
                if name.endswith('/'):
                    zinfo = zipfile.ZipInfo(name)
                    zinfo.date_time = info.date_time
                    zinfo.external_attr = info.external_attr
                    zinfo.compress_type = zipfile.ZIP_STORED
                    out.writestr(zinfo, b'')
                else:
                    zinfo = zipfile.ZipInfo(name)
                    zinfo.date_time = info.date_time
                    zinfo.external_attr = info.external_attr
                    zinfo.comment = info.comment
                    zinfo.extra = info.extra
                    zinfo.compress_type = zipfile.ZIP_DEFLATED
                    out.writestr(zinfo, source_zip.read(name))

            if 'joomlatools_installer.php' in source_zip.namelist():
                out.writestr('joomlatools_installer.php', source_zip.read('joomlatools_installer.php'))
            else:
                out.writestr('joomlatools_installer.php', build_component_entrypoint())

            out.writestr('joomlatools_installer.xml', build_component_manifest(meta))
            out.writestr('script.php', build_component_script(meta))
            out.writestr('conversion-report.json', json.dumps(report, indent=2))
            out.writestr('README-normalized-wrapper.txt', build_readme(extensions, meta, os.path.basename(output_zip)))

        print(f'Created: {output_zip}')
        print(f'SHA256: {sha256_file(output_zip)}')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
