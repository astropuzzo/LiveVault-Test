"""SD acquisition and verified import under interruptions, in isolated filesystems."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_serra_camera as archive_tests
camera,ROOT=archive_tests.camera,archive_tests.ROOT
import serra_spool as spool


class ImportTests(unittest.TestCase):
    native_image=archive_tests.SerraTests.native_image
    # Reuse the archive fixture, not all of its inherited test methods.
    def setUp(self):
        archive_tests.SerraTests.setUp(self)
        patch.dict(sys.modules,{camera.__name__:camera}).start()
        camera.CONFIG.write_text(json.dumps({'sd_spool':True}))
        self.payload=b'\xff\xd8native clock 10:00\xff\xd9'
        self.source={}
        self.operations=[]
        self.fail_ack=False
        patch.object(camera,'_upstream',self.upstream).start()

    def add(self, *, rotation=0,tier='b',timestamp=None,payload=None):
        timestamp=int(time.time()) if timestamp is None else timestamp
        name=f'{timestamp}-00000000-0000-4000-8000-{len(self.source)+1:012d}-r{rotation}-t{tier}.jpg'
        data=self.payload if payload is None else payload
        self.source[name]={'payload':data,'digest':hashlib.sha256(data).hexdigest()}
        return name

    def upstream(self, resource, *, body=None, **params):
        self.assertEqual(resource,'serra-spool')
        op=params['op'];self.operations.append((op,body))
        if op=='manifest':
            s=camera.settings()
            status={'ok':True,'online':True,'enabled':int(s['enabled']),'interval':s['interval_seconds'],
                    'rotation':s['rotation_degrees'],'quota_kb':8*1024**2,'used_kb':100,'pending':len(self.source),'last_capture':int(time.time()),'error':''}
            rows=[f"{name} {len(row['payload'])} {row['digest']}" for name,row in sorted(self.source.items())]
            return io.BytesIO(('\n'.join([json.dumps(status)]+rows)+'\n').encode())
        if op=='get': return io.BytesIO(self.source[params['name']]['payload'])
        if op=='ack':
            if self.fail_ack: raise camera.SerraError('Conferma interrotta.',502)
            # The archive must already be durable/indexed before the camera removes anything.
            for line in body.decode().splitlines():
                name,digest=line.split()
                with camera._db() as db:self.assertEqual(db.execute('SELECT digest FROM spool_receipts WHERE name=?',(name,)).fetchone()[0],digest)
                del self.source[name]
        return io.BytesIO(b'{"ok":true}')

    def test_lost_ack_retries_without_duplicate_photos(self):
        self.add();self.fail_ack=True
        with self.assertRaises(camera.SerraError):spool.sync(camera)
        with camera._db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM frames').fetchone()[0],2)
        self.assertEqual(len(self.source),1)
        self.fail_ack=False;self.operations.clear();spool.sync(camera)
        self.assertEqual(self.source,{})
        self.assertNotIn('get',[op for op,_ in self.operations])
        with camera._db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM frames').fetchone()[0],2)

    def test_absent_nvme_never_acknowledges_and_status_remains_available(self):
        self.add()
        @contextmanager
        def absent(**kwargs):raise camera.SerraError('NVMe assente.');yield
        with patch.object(camera,'storage',absent):
            with self.assertRaises(camera.SerraError):spool.sync(camera)
        self.assertEqual(camera._state()['sd_status']['pending'],1)
        self.assertNotIn('ack',[op for op,_ in self.operations])
        self.assertEqual(len(self.source),1)
        spool.sync(camera,import_media=False)
        self.assertNotIn('get',[op for op,_ in self.operations])
        spool.sync(camera);self.assertEqual(self.source,{})

    def test_hash_mismatch_is_kept_on_sd_while_other_valid_photos_progress(self):
        bad=self.add(timestamp=int(time.time())-10);self.source[bad]['digest']='0'*64
        self.add(timestamp=int(time.time())-5)
        with self.assertRaisesRegex(camera.SerraError,'diversa'):spool.sync(camera)
        self.assertEqual(list(self.source),[bad])
        with camera._db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM spool_receipts').fetchone()[0],1)

    def test_crash_before_receipt_commit_recovers_stable_files_without_duplicates(self):
        self.add();real_db=camera._db
        @contextmanager
        def interrupted():
            with real_db() as db:
                class Proxy:
                    def execute(self,sql,args=()):
                        if sql.startswith('INSERT INTO spool_receipts'):raise OSError('simulated power loss')
                        return db.execute(sql,args)
                yield Proxy()
        with patch.object(camera,'_db',interrupted):
            with self.assertRaises(OSError):spool.sync(camera)
        self.assertEqual(len(list((self.media/'pending').glob('*.jpg'))),2)
        self.assertNotIn('ack',[op for op,_ in self.operations])
        camera.recover();spool.sync(camera)
        with real_db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM frames').fetchone()[0],2)
        self.assertEqual(self.source,{})

    @unittest.skipUnless(shutil.which('jpegtran') and shutil.which('ffmpeg'),'JPEG tools required')
    def test_backlog_preserves_original_time_and_physical_rotation(self):
        stamp=int(time.time())-3600
        self.add(rotation=90,timestamp=stamp,payload=self.native_image())
        self.fail_ack=True
        with self.assertRaises(camera.SerraError):spool.sync(camera)
        with camera._db() as db:rows=list(db.execute('SELECT * FROM frames'))
        self.assertEqual([(row['rotation'],row['ts']) for row in rows],[(90,stamp),(90,stamp)])
        path=self.media/'pending'/rows[0]['name']
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=width,height','-of','json',str(path)]))['streams'][0]
        self.assertEqual((probe['width'],probe['height']),(1296,2304))
        camera.compact('history')
        self.fail_ack=False;self.operations.clear();spool.sync(camera)
        self.assertNotIn('get',[op for op,_ in self.operations])
        with camera._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM segments WHERE tier='history'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frames WHERE tier='history'").fetchone()[0],0)

    def test_pause_settings_are_saved_and_forwarded_without_touching_archive_limits(self):
        camera.set_settings({'enabled':False,'interval_seconds':60,'quota_gb':8,'rotation_degrees':270})
        config=next(body for op,body in self.operations if op=='config')
        self.assertEqual(config,b'0 60 270 8388608\n')
        self.assertEqual(camera.settings()['quota_gb'],8)
        self.assertEqual(camera.handle_post({'action':'capture'}),{'ok':True,'queued':True})

    def test_invalid_manifest_and_path_traversal_cannot_fetch_or_ack(self):
        status={'ok':True,'online':True,'enabled':1,'interval':60,'rotation':0,'quota_kb':8*1024**2,'used_kb':0,'pending':1,'last_capture':0,'error':''}
        for name in ['../../private.jpg','1793869200-bad-r0-tb.jpg']:
            data=json.dumps(status)+'\n'+name+' 20 '+'a'*64+'\n'
            with patch.object(spool,'request',return_value=data.encode()):
                with self.assertRaises(camera.SerraError):spool.sync(camera)
        self.assertEqual(self.operations,[])


class CameraScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.sd=self.root/'sd';self.run=self.root/'run';self.bin=self.root/'bin'
        for p in (self.sd/'pending',self.run,self.bin):p.mkdir(parents=True)
        (self.sd/'.identity').write_text('isolated-test-card\n')
        self.image=self.root/'image.jpg';self.image.write_bytes(b'\xff\xd8native fixture\xff\xd9')
        self.config=self.root/'spool.conf'
        self.config.write_text(f"SERRA_ENABLED=1\nSERRA_INTERVAL=60\nSERRA_ROTATION=0\nSERRA_QUOTA_KB=8388608\nSERRA_MOUNT='{self.sd}'\nSERRA_ROOT='{self.sd}'\nSERRA_IDENTITY='isolated-test-card'\n")
        for name,body in {'mountpoint':'[ ! -f "$SERRA_TEST_OFF" ]','prudyntctl':'cat "$SERRA_TEST_IMAGE"','sync':':'} .items():
            p=self.bin/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o700)
        self.env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],'SERRA_SPOOL_CONFIG':str(self.config),
                  'SERRA_RUN_ROOT':str(self.run),'SERRA_TEST_IMAGE':str(self.image),'SERRA_TEST_OFF':str(self.root/'absent')}
        self.script=ROOT/'scripts/serra-camera-spool.sh'

    def call(self,command='http',op='manifest',body=None,method=None,name=None):
        query='op='+op+('&name='+name if name else '')
        env={**self.env,'QUERY_STRING':query,'REQUEST_METHOD':method or ('POST' if body is not None else 'GET'),'CONTENT_LENGTH':str(len(body or b''))}
        result=subprocess.run(['sh',str(self.script),command],input=body,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=20)
        self.assertIn(result.returncode,(0,1) if command=='capture' else (0,),result.stderr.decode())
        return result.stdout.decode()

    def files(self):return sorted((self.sd/'pending').glob('*/*.jpg'))

    def test_camera_captures_without_server_and_only_matching_post_ack_can_remove(self):
        self.call('capture');path=self.files()[0];digest=hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(path.read_bytes(),self.image.read_bytes())
        listing=self.call();self.assertIn(path.name,listing)
        self.assertIn('405',self.call(op='ack'))
        self.assertIn('409',self.call(op='ack',body=f'{path.name} {"0"*64}\n'.encode()))
        self.assertTrue(path.exists())
        self.assertIn('400',self.call(op='get',name='../../private.jpg'))
        self.assertIn('"ok":true',self.call(op='ack',body=f'{path.name} {digest}\n'.encode()))
        self.assertEqual(self.files(),[])
        self.assertIn('"ok":true',self.call(op='ack',body=f'{path.name} {digest}\n'.encode()))

    def test_sd_removal_and_full_quota_preserve_every_unacknowledged_photo(self):
        self.call('capture');first=self.files()[0]
        (self.root/'absent').touch()
        self.call('capture');self.assertEqual(self.files(),[first]);self.assertIn('sd_unavailable',self.call())
        (self.root/'absent').unlink()
        (self.run/'serra-spool.usage').write_text('8388608 1\n')
        self.call('capture');self.assertEqual(self.files(),[first]);self.assertIn('queue_full',self.call())

    def test_restart_recovers_complete_orphan_and_keeps_hourly_schedule(self):
        self.call('capture');path=self.files()[0]
        self.assertIn('-tb.jpg',path.name)
        path.with_name('m-'+path.stem+'.txt').unlink()
        self.call('recover');self.assertIn(path.name,self.call())
        (self.run/'serra-spool.clock').unlink()
        self.call('capture');self.assertEqual(len(self.files()),2)
        self.assertEqual(sum('-tb.jpg' in p.name for p in self.files()),1)
        self.assertEqual(sum('-tr.jpg' in p.name for p in self.files()),1)

    def test_config_rejects_shell_injection_and_keeps_rotation_and_interval(self):
        self.assertIn('400',self.call(op='config',body=b'1 60 0 8388608;touch /tmp/unsafe\n'))
        self.assertIn('"ok":true',self.call(op='config',body=b'1 5 270 8388608\n'))
        self.call('capture');self.assertIn('-r270-',self.files()[0].name)
        self.assertIn('"interval":5',self.call())

    def test_restarted_video_daemon_cannot_inherit_the_health_watchdog_lock(self):
        restart=self.bin/'restart-video'
        marker=self.root/'restart-without-inherited-lock'
        restart.write_text('#!/bin/sh\n[ ! -e /proc/$$/fd/9 ] || exit 1\nprintf ok > "$SERRA_TEST_RESTART_MARKER"\n')
        restart.chmod(0o700)
        env={**self.env,'SERRA_HEALTH_RUN':str(self.run),'SERRA_HEALTH_RESTART':str(restart),'SERRA_TEST_RESTART_MARKER':str(marker)}
        health=ROOT/'scripts/serra-camera-health.sh'
        for _ in range(3):subprocess.run(['sh',str(health)],env=env,check=True,timeout=20)
        self.assertEqual(marker.read_text(),'ok')
        # A future invocation can still take the lock; no daemon holds it forever.
        subprocess.run(['sh',str(health)],env=env,check=True,timeout=20)


if __name__=='__main__':unittest.main()
