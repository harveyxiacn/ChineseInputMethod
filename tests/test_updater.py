"""Updater trust boundaries, transactional state and archive semantics."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from ime import updater as module
from ime.updater import Updater, UpdateError, safe_extract


def fixture_archive(version='v0.4.0', target='linux-x64', extra=()):
    root = f'Shuangsheng-{version}-{target}'
    executable = module.executable_in(Path(root), target).as_posix()
    files = [(root + '/release-metadata/build-manifest.json', json.dumps({'version':version, 'target':target}).encode()),
             (executable, b'executable fixture')]
    stream = io.BytesIO()
    if target == 'linux-x64':
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name, value in files + list(extra):
                info = tarfile.TarInfo(name); info.size = len(value); info.mode = 0o755 if name == executable else 0o644
                archive.addfile(info, io.BytesIO(value))
    else:
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, value in files + list(extra):
                info = zipfile.ZipInfo(name); info.external_attr = (stat.S_IFREG | 0o755) << 16
                archive.writestr(info, value)
    return stream.getvalue()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.updater = Updater(root=self.root / 'updates', current_version='0.3.0', target='linux-x64')

    def offer(self, version='v0.4.0', data=None):
        data = fixture_archive(version) if data is None else data
        name = module.archive_name(version, 'linux-x64')
        base = f'https://github.com/{module.REPOSITORY}/releases/download/{version}/'
        return {'version':version, 'target':'linux-x64', 'name':name, 'size':len(data),
                'url':base+name, 'checksum_url':base+'SHA256SUMS'}, data

    def transport(self, offer, data, *, checksum=None):
        digest = checksum or hashlib.sha256(data).hexdigest()
        release = {'tag_name':offer['version'], 'draft':False, 'prerelease':False, 'assets':[
            {'name':offer['name'], 'size':len(data), 'browser_download_url':offer['url']},
            {'name':'SHA256SUMS', 'size':100, 'browser_download_url':offer['checksum_url']}]}
        responses = {module.API_URL:json.dumps(release).encode(), offer['url']:data,
                     offer['checksum_url']:f'{digest}  {offer["name"]}\n'.encode()}
        return patch.object(module, 'open_url', side_effect=lambda url:io.BytesIO(responses[url]))

    def prepare(self, version='v0.4.0'):
        offer, data = self.offer(version)
        with self.transport(offer, data), patch.object(self.updater, '_smoke', side_effect=self.updater._verify_package):
            return self.updater.download(self.updater.check())

    def test_check_download_activate_and_rollback_preserve_settings(self):
        settings = self.root / 'settings.json'; settings.write_bytes(b'private settings')
        first = self.prepare()
        self.assertIsNone(self.updater.state()['current'])
        self.updater.activate(first)
        second = self.prepare('v0.4.1')
        self.updater.activate(second)
        self.assertEqual(self.updater.state()['previous'], first)
        self.assertEqual(self.updater.rollback(), first)
        self.assertEqual(self.updater.state()['current'], first)
        self.assertEqual(settings.read_bytes(), b'private settings')
        directory, package = self.updater._package(second)
        self.assertTrue(module.executable_in(package, 'linux-x64').is_file())

    def test_corrupt_download_never_extracts_or_activates(self):
        offer, data = self.offer()
        with self.transport(offer, data, checksum='0'*64), patch.object(self.updater, '_smoke') as smoke:
            with self.assertRaisesRegex(UpdateError, 'SHA-256'):
                self.updater.download(offer)
            smoke.assert_not_called()
        self.assertIsNone(self.updater.state()['current'])
        self.assertEqual(list((self.updater.root / 'versions').iterdir()), [])

    def test_failed_smoke_leaves_old_active_and_removes_staging(self):
        first = self.prepare(); self.updater.activate(first)
        offer, data = self.offer('v0.4.1')
        with self.transport(offer, data), patch.object(self.updater, '_smoke', side_effect=UpdateError('broken runtime')):
            with self.assertRaisesRegex(UpdateError, 'broken runtime'):
                self.updater.download(offer)
        self.assertEqual(self.updater.state()['current'], first)
        self.assertFalse(any(item.name.startswith('.stage-') for item in (self.updater.root / 'versions').iterdir()))

    def test_failed_launch_restores_pointer_and_native_plugin(self):
        first = self.prepare(); self.updater.activate(first)
        second = self.prepare('v0.4.1')
        before = self.updater.state()
        with patch.object(self.updater, 'native_installed', return_value=True), \
             patch('ime.native_update.NativeUpgrade') as native, \
             patch.object(self.updater, 'launch', side_effect=UpdateError('launch failed')):
            with self.assertRaisesRegex(UpdateError, 'launch failed'):
                self.updater.install(second)
            native.return_value.prepare.assert_called_once_with()
            native.return_value.activate.assert_called_once_with()
            native.return_value.rollback.assert_called_once_with()
        self.assertEqual(self.updater.state(), before)

    def test_same_version_old_version_and_bad_versions_rejected(self):
        for value in ['v0.3.0','v0.2.9']:
            offer, _ = self.offer(value)
            with self.assertRaises(UpdateError):self.updater._validate_offer(offer)
        for value in ['v１.2.3','v1.2.3/../x','v01.2.3','v1.2.3\n','v1.2.3-beta','v1.2.3+build',True,'1.'+'2'*100+'.3']:
            with self.assertRaises(UpdateError):module.version_tuple(value)
        self.assertEqual(module.version_tuple('v10.2.3'), (10,2,3))

    def test_offer_requires_exact_repository_urls_size_and_target(self):
        offer, _ = self.offer()
        for field, value in [('url','https://github.com/evil/project/releases/x'), ('url','http://127.0.0.1/x'),
                             ('checksum_url','https://github.com/evil/SHA256SUMS'), ('size',True),
                             ('size',module.MAX_ARCHIVE+1), ('target','windows-x64')]:
            with self.subTest(field=field,value=value), self.assertRaises(UpdateError):
                self.updater._validate_offer({**offer,field:value})

    def test_redirect_hosts_and_scheme_are_bounded(self):
        for url in ['http://github.com/x','file:///etc/passwd','https://github.com.evil/x',
                    'https://github.com@evil/x','https://github.com:444/x','https://127.0.0.1/x']:
            with self.assertRaises(UpdateError):module.validate_url(url)
        module.validate_url('https://release-assets.githubusercontent.com/file?signature=value')

    def test_latest_release_needs_unique_assets_and_stable_public_state(self):
        offer, data = self.offer()
        for release in [
            {'tag_name':'v0.4.0','draft':True}, {'tag_name':'v0.4.0','prerelease':True},
            {'tag_name':'v0.4.0','assets':[]},
            {'tag_name':'v0.4.0','assets':[{'name':offer['name']}]*2}]:
            with patch.object(self.updater, '_bytes', return_value=json.dumps(release).encode()), self.assertRaises(UpdateError):
                self.updater.check()

    def test_no_update_and_http_failure_do_not_mutate_state(self):
        with patch.object(self.updater,'_bytes',return_value=b'{"tag_name":"v0.3.0"}'):
            self.assertIsNone(self.updater.check())
        with patch.object(self.updater,'_bytes',side_effect=OSError('offline')), self.assertRaises(UpdateError):
            self.updater.check()
        self.assertFalse(self.updater.root.exists())

    def test_archive_traversal_duplicate_links_and_size_limit(self):
        package = 'Shuangsheng-v0.4.0-linux-x64'
        for name in [package+'/../../outside','/absolute',package+'/bad\\name',package+'/a:stream',package+'/Shuangsheng/Shuangsheng']:
            data = fixture_archive(extra=[(name,b'bad')])
            archive = self.root / 'attack.tar.gz'; archive.write_bytes(data)
            with self.subTest(name=name), self.assertRaises(UpdateError):
                safe_extract(archive,self.root/'extracted',package,'linux-x64')
        archive.write_bytes(fixture_archive())
        with patch.object(module,'MAX_EXPANDED',1), self.assertRaises(UpdateError):
            safe_extract(archive,self.root/'extracted',package,'linux-x64')
        self.assertFalse((self.root/'outside').exists())

    def test_tar_external_link_and_special_file_rejected(self):
        root = 'Shuangsheng-v0.4.0-linux-x64'
        for kind, link in [(tarfile.SYMTYPE,'../../escape'),(tarfile.LNKTYPE,'outside'),(tarfile.FIFOTYPE,'')]:
            data=io.BytesIO()
            with tarfile.open(fileobj=data,mode='w:gz') as package:
                member=tarfile.TarInfo(root+'/link');member.type=kind;member.linkname=link
                package.addfile(member)
            archive=self.root/'link.tar.gz';archive.write_bytes(data.getvalue())
            with self.assertRaises(UpdateError):safe_extract(archive,self.root/'out',root,'linux-x64')

    @unittest.skipIf(os.name=='nt','POSIX package links')
    def test_internal_symlink_and_executable_mode_preserved(self):
        root='Shuangsheng-v0.4.0-linux-x64';data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as package:
            info=tarfile.TarInfo(root+'/bin/actual');info.mode=0o755;info.size=2;package.addfile(info,io.BytesIO(b'ok'))
            info=tarfile.TarInfo(root+'/alias');info.type=tarfile.SYMTYPE;info.linkname='bin/actual';package.addfile(info)
        archive=self.root/'links.tar.gz';archive.write_bytes(data.getvalue())
        safe_extract(archive,self.root/'out',root,'linux-x64')
        alias=self.root/'out'/root/'alias'
        self.assertTrue(alias.is_symlink());self.assertEqual(alias.read_bytes(),b'ok')
        self.assertTrue(alias.stat().st_mode & stat.S_IXUSR)

    def test_windows_and_mac_zip_layouts_and_case_collision(self):
        for target in ['windows-x64','macos-arm64','macos-x64']:
            root=f'Shuangsheng-v0.4.0-{target}';archive=self.root/(target+'.zip')
            archive.write_bytes(fixture_archive(target=target))
            safe_extract(archive,self.root/target,root,target)
            self.assertTrue(module.executable_in(self.root/target/root,target).is_file())
            archive.write_bytes(fixture_archive(target=target,extra=[(root+'/A',b'1'),(root+'/a',b'2')]))
            with self.assertRaises(UpdateError):safe_extract(archive,self.root/'bad',root,target)

    def test_modified_ready_record_and_manifest_cannot_activate(self):
        record=self.prepare();directory,package=self.updater._package(record)
        (package/'release-metadata/build-manifest.json').write_text('{"version":"v9.9.9","target":"linux-x64"}',encoding='utf-8')
        with self.assertRaises(UpdateError):self.updater.activate(record)
        self.assertIsNone(self.updater.state()['current'])

    def test_subprocess_environment_does_not_inherit_frozen_library_paths(self):
        with patch.dict(os.environ,{'LD_LIBRARY_PATH':'/old/app','TCL_LIBRARY':'/old/tcl','PYTHONPATH':'/old/code'},clear=True):
            environment=module.subprocess_environment()
        for key in ['LD_LIBRARY_PATH','TCL_LIBRARY','PYTHONPATH']:self.assertNotIn(key,environment)
        self.assertEqual(environment['PYINSTALLER_RESET_ENVIRONMENT'],'1')


@unittest.skipUnless(os.name=='posix','POSIX atomic symlinks')
class NativeActivationTests(unittest.TestCase):
    def test_restart_failure_restores_old_plugin_and_bridge(self):
        from ime.native_update import NativeUpgrade
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);updater=Updater(root=root/'updates',target='linux-x64')
            record={'version':'v0.4.0','target':'linux-x64','sha256':'0'*64}
            upgrade=NativeUpgrade(updater,record,prefix=root/'prefix')
            plugin=upgrade.prefix/'lib/fcitx5/shuangsheng.so';plugin.parent.mkdir(parents=True)
            plugin.write_bytes(b'old plugin')
            bridge=upgrade.prefix/'bin/shuangsheng-pinyin-bridge';bridge.parent.mkdir(parents=True)
            bridge.symlink_to(root/'old-bridge')
            upgrade.native.mkdir(parents=True);updater.root.mkdir(exist_ok=True)
            upgrade.was_running=True
            with patch.object(upgrade,'_restart',side_effect=[UpdateError('new failed'),None]):
                with self.assertRaisesRegex(UpdateError,'new failed'):upgrade.activate()
            self.assertEqual(plugin.read_bytes(),b'old plugin');self.assertFalse(plugin.is_symlink())
            self.assertEqual(os.readlink(bridge),str(root/'old-bridge'))
            self.assertFalse((updater.root/'native.json').exists())


if __name__=='__main__':unittest.main()
