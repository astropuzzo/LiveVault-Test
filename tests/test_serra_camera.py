"""Storage, recovery and real FFmpeg contracts for the native greenhouse archive."""
from contextlib import contextmanager
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import sys
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'control-panel'))
spec = importlib.util.spec_from_file_location('serra_camera', ROOT / 'control-panel/serra_camera.py')
camera = importlib.util.module_from_spec(spec)
spec.loader.exec_module(camera)
REAL_STORAGE = camera.storage


class SerraTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.media = self.root / 'nvme/serra'
        for name in ('pending', 'segments'):
            (self.media / name).mkdir(parents=True)
        self.addCleanup(patch.stopall)
        patch.object(camera, 'STATE', self.state).start()
        patch.object(camera, 'CONFIG', self.root / 'credentials.json').start()
        patch.object(camera, 'STORAGE_STATE', self.root / 'storage-state.json').start()

        @contextmanager
        def storage(**_kwargs):
            yield self.media
        patch.object(camera, 'storage', storage).start()

    def frame(self, timestamp, content=b'original frame', batch=None, rotation=0, tier='recent'):
        name = ('h' if tier=='history' else '')+str(int(timestamp * 1e9)) + '.jpg'
        (self.media / 'pending' / name).write_bytes(content)
        with camera._db() as db:
            db.execute('INSERT INTO frames(name,ts,size,batch,rotation,tier) VALUES(?,?,?,?,?,?)', (name, timestamp, len(content), batch, rotation, tier))
        return name

    def segment(self, timestamp, content=b'compact video', tier='recent', rotation=0):
        name = ('h' if tier=='history' else '')+str(int(timestamp * 1e9)) + '.mp4'
        (self.media / 'segments' / name).write_bytes(content)
        with camera._db() as db:
            db.execute('INSERT INTO segments(name,start,end,frames,size,init,duration,tier,rotation) VALUES(?,?,?,?,?,?,?,?,?)',
                       (name, timestamp, timestamp + 1, 2, len(content), 4, 2/24,tier,rotation))
        return name

    def native_image(self):
        target = self.root/'quadrants.jpg'
        expression = 'color=black:s=2304x1296,drawbox=x=0:y=0:w=1152:h=648:color=red:t=fill,drawbox=x=1152:y=0:w=1152:h=648:color=green:t=fill,drawbox=x=0:y=648:w=1152:h=648:color=blue:t=fill,drawbox=x=1152:y=648:w=1152:h=648:color=yellow:t=fill'
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i',expression,'-frames:v','1','-threads','1','-y',str(target)],check=True)
        return target.read_bytes()

    def test_existing_schema_is_extended_without_losing_rows(self):
        self.state.mkdir()
        db = camera.sqlite3.connect(self.state/'archive.sqlite3')
        with db:
            db.execute('CREATE TABLE frames(name TEXT PRIMARY KEY,ts REAL,size INTEGER,batch TEXT)')
            db.execute("INSERT INTO frames VALUES('legacy.jpg',1,2,NULL)")
        db.close()
        with camera._db() as db:
            row = db.execute('SELECT * FROM frames').fetchone()
        self.assertEqual((row['name'],row['rotation'],row['tier']),('legacy.jpg',0,'recent'))

    def test_hourly_history_reuses_capture_and_is_independent_of_recent_interval(self):
        now = time.time()
        with patch.object(camera,'photo',side_effect=[b'frame1',b'frame2',b'frame3']) as photo, patch.object(camera.time,'time',return_value=now) as clock:
            camera.capture();clock.return_value=now+10;camera.capture()
            clock.return_value=now+3600;camera.capture(recent=False)
        with camera._db() as db:
            counts = dict(db.execute('SELECT tier,COUNT(*) FROM frames GROUP BY tier'))
        self.assertEqual(counts,{'recent':2,'history':2})
        self.assertEqual(photo.call_count,3)

    def test_frozen_snapshot_is_not_archived_as_a_new_capture_and_recovers(self):
        camera.set_settings({'enabled':True,'interval_seconds':60,'quota_gb':8})
        now=time.time()
        with patch.object(camera,'photo',return_value=b'jpeg with burned-in clock') as photo, patch.object(camera.time,'time',return_value=now) as clock:
            camera.capture()
            # One repeat can straddle a JPEG/clock boundary; a persistent repeat cannot.
            clock.return_value=now+1;camera.capture()
            last=camera._state()['last_capture']
            clock.return_value=now+121
            with self.assertRaisesRegex(camera.SerraError,'fotogramma fermo'): camera.capture()
            self.assertEqual(camera._state()['last_capture'],last)
            with camera._db() as db: self.assertEqual(db.execute("SELECT COUNT(*) FROM frames WHERE tier='recent'").fetchone()[0],2)
            photo.return_value=b'new clock';clock.return_value=now+122;camera.capture()
        self.assertEqual(camera._state()['last_capture'],now+122)
        self.assertEqual(camera.settings()['quota_gb'],8)

    def test_cleanup_future_end_is_frozen_at_preview_time_without_deleting(self):
        now=time.time();name=self.frame(now-30)
        with patch.object(camera.time,'time',return_value=now):
            with patch.object(Path,'unlink',side_effect=AssertionError('preview must not delete')):
                plan=camera.purge_archive({'from_ts':now-60,'to_ts':now+3600},execute=False)
            self.assertEqual(plan['to_ts'],now);self.assertEqual(plan['photos'],1)
            self.assertTrue((self.media/'pending'/name).exists())
            with self.assertRaises(camera.SerraError): camera.purge_archive({**plan,'to_ts':now+3600},execute=True)
            with self.assertRaises(camera.SerraError): camera.purge_archive({'from_ts':now+10,'to_ts':now+3600})
            with self.assertRaises(camera.SerraError): camera.purge_archive({'to_ts':float('inf')})
            self.assertEqual(camera.purge_archive(plan,execute=True)['photos'],1)

    def test_cleanup_requires_matching_preview_and_preserves_active_partial_and_history(self):
        now=time.time();first=self.frame(now-100);active=self.frame(now-90,batch='busy')
        history=self.frame(now-80,tier='history');whole=self.segment(now-110);partial=self.segment(now-150)
        with camera._db() as db: db.execute('UPDATE segments SET end=? WHERE name=?',(now-90,partial))
        foreign=self.root/'family.mp4';foreign.write_bytes(b'keep')
        request={'from_ts':now-120,'to_ts':now-50}
        preview=camera.purge_archive(request)
        self.assertEqual((preview['photos'],preview['blocks'],preview['frames']),(1,1,3))
        self.assertTrue((self.media/'pending'/first).exists())
        with camera._lock('retention',shared=True):
            with self.assertRaises(camera.SerraError): camera.purge_archive(preview,execute=True)
        changed=self.frame(now-70)
        with self.assertRaises(camera.SerraError): camera.purge_archive(preview,execute=True)
        preview=camera.purge_archive(request);result=camera.purge_archive(preview,execute=True)
        self.assertEqual(result['frames'],4)
        self.assertFalse((self.media/'pending'/first).exists());self.assertFalse((self.media/'pending'/changed).exists())
        self.assertFalse((self.media/'segments'/whole).exists())
        for folder,name in [('pending',active),('pending',history),('segments',partial)]: self.assertTrue((self.media/folder/name).exists())
        self.assertEqual(foreign.read_bytes(),b'keep')
        preview=camera.purge_archive({**request,'include_history':True})
        self.assertEqual(preview['photos'],1)

    def test_quota_prefers_recent_detail_over_older_year_history(self):
        now=time.time();old=self.segment(now-1000,b'h'*40,tier='history');recent=self.segment(now-10,b'r'*40)
        patch.object(camera,'settings',lambda:{'quota_gb':100/1024**3}).start()
        camera._room(self.media,30)
        self.assertTrue((self.media/'segments'/old).exists());self.assertFalse((self.media/'segments'/recent).exists())

    def test_history_defers_during_export_and_clears_recovered_transient_errors(self):
        with camera._lock('retention',shared=True):camera.maintain_history()
        self.assertEqual(camera._state().get('history_error',''),'')
        camera._state(history_error='transient')
        camera.maintain_history()
        self.assertEqual(camera._state()['history_error'],'')

    @unittest.skipUnless(shutil.which('jpegtran') and shutil.which('ffmpeg'),'JPEG tools required')
    def test_rotation_changes_jpeg_pixels_at_all_four_native_orientations(self):
        image=self.native_image();original=None
        for angle,expected in [(0,[0,1,2,3]),(90,[2,0,3,1]),(180,[3,2,1,0]),(270,[1,3,0,2])]:
            rotated=camera.rotate_jpeg(image,angle)
            if angle==0:self.assertEqual(rotated,image)
            self.assertEqual(camera._jpeg_rotation(rotated),angle)
            path=self.root/f'rotation-{angle}.jpg';path.write_bytes(rotated)
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','json',str(path)]))['streams'][0]
            w,h=probe['width'],probe['height'];self.assertEqual((w,h),(1296,2304) if angle in (90,270) else (2304,1296))
            rgb=subprocess.check_output(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-i',str(path),'-frames:v','1','-threads','1','-pix_fmt','rgb24','-f','rawvideo','-'])
            colors=[rgb[(y*w+x)*3:(y*w+x)*3+3] for y in (h//4,3*h//4) for x in (w//4,3*w//4)]
            if original is None:original=colors
            for color,index in zip(colors,expected):self.assertLessEqual(max(abs(a-b) for a,b in zip(color,original[index])),2)
        camera.set_settings({'enabled':True,'interval_seconds':60,'quota_gb':4,'rotation_degrees':90})
        camera.set_settings({'enabled':True,'interval_seconds':15,'quota_gb':4})
        self.assertEqual(camera.settings()['rotation_degrees'],90)

    @unittest.skipUnless(shutil.which('jpegtran') and shutil.which('ffmpeg'),'JPEG tools required')
    def test_portrait_blocks_are_kept_separate_from_landscape_and_survive_recovery(self):
        image=self.native_image();rotated=camera.rotate_jpeg(image,90);now=time.time()
        for i in range(2):self.frame(now-20+i,image)
        camera.compact()
        self.frame(now-15,image)
        for i in range(2):self.frame(now-10+i,rotated,rotation=90)
        camera.compact();camera.compact()
        with camera._db() as db:rows=list(db.execute('SELECT * FROM segments ORDER BY id'))
        self.assertEqual([(r['rotation'],r['width'],r['height']) for r in rows],[(0,2304,1296),(0,2304,1296),(90,1296,2304)])
        camera.mp4_view.build([self.media/'segments'/r['name'] for r in rows if r['rotation']==0])
        self.assertEqual(len(camera.window(24,90)),1)
        orphan='h'+str(time.time_ns())+'.jpg';(self.media/'pending'/orphan).write_bytes(rotated)
        camera.recover()
        with camera._db() as db:row=db.execute('SELECT * FROM frames WHERE name=?',(orphan,)).fetchone()
        self.assertEqual((row['rotation'],row['tier']),(90,'history'))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_old_history_keeps_local_10_14_18_and_still_builds_a_continuous_native_movie(self):
        from datetime import datetime,timedelta
        image=self.native_image();day=datetime.now(camera.HISTORY_ZONE).replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=40)
        for hour in (8,10,12,14,16,18):self.frame(day.replace(hour=hour).timestamp(),image,tier='history')
        camera.compact('history')
        with camera._db() as db:original=dict(db.execute("SELECT * FROM segments WHERE tier='history'").fetchone())
        def hashes(path):
            data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_packets','-show_data_hash','sha256','-select_streams','v:0','-show_entries','packet=data_hash','-of','json',str(path)]))
            return [packet['data_hash'] for packet in data['packets']]
        before=hashes(self.media/'segments'/original['name'])
        camera.maintain_history()
        with camera._db() as db:old=dict(db.execute("SELECT * FROM segments WHERE tier='history'").fetchone())
        self.assertEqual(old['frames'],3)
        self.assertEqual(hashes(self.media/'segments'/old['name']),[before[index] for index in (1,3,5)])
        self.assertEqual([datetime.fromtimestamp(ts,camera.HISTORY_ZONE).hour for ts in json.loads(old['sample_times'])],[10,14,18])
        now=time.time()
        for i in range(2):self.frame(now-3600+i*1800,image,tier='history')
        camera.compact('history')
        rows=camera.window(8760);self.assertEqual(sum(r['frames'] for r in rows),5)
        view=camera.mp4_view.build([self.media/'segments'/r['name'] for r in rows]);path=self.root/'year.mp4'
        with view.open() as reader:path.write_bytes(reader.read(view.size))
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=width,height,nb_read_frames','-of','json',str(path)]))['streams'][0]
        self.assertEqual((probe['width'],probe['height'],int(probe['nb_read_frames'])),(2304,1296,5))

    def test_allowed_intervals_quota_and_validation(self):
        for interval in (1, 5, 15, 30, 60):
            camera.set_settings({'enabled':True, 'interval_seconds':interval, 'quota_gb':4})
            self.assertEqual(camera.settings()['interval_seconds'], interval)
        for bad in ({'enabled':True,'interval_seconds':True,'quota_gb':4},
                    {'enabled':True,'interval_seconds':1,'quota_gb':0},
                    {'enabled':True,'interval_seconds':1,'quota_gb':999}):
            with self.assertRaises(camera.SerraError):
                camera.set_settings(bad)

    def test_playback_diagnostics_are_bounded_private_and_do_not_change_capture(self):
        camera.set_settings({'enabled':True,'interval_seconds':60,'quota_gb':4})
        with patch.object(camera.time,'time',side_effect=range(100,120)):
            for number in range(20):
                camera.handle_post({'action':'playback-report','report':{'build':'20261104-7','position':number,'unrecognized':'discard'}})
        path = self.state / 'playback-diagnostics.json'
        reports = json.loads(path.read_text())['reports']
        self.assertEqual(len(reports),16)
        self.assertEqual(reports[0]['position'],4)
        self.assertNotIn('unrecognized',reports[-1])
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual(camera.settings()['interval_seconds'],60)
        for invalid in (None,{}, {'build':'20261104-7','position':float('nan')},
                        {'build':'20261104-7','user_agent':'x'*5000}):
            with self.assertRaises(camera.SerraError):
                camera.playback_report(invalid)

    def test_absent_nvme_never_creates_an_archive_on_the_system_disk(self):
        absent = self.root / 'absent-mount'
        absent.mkdir()
        with patch.object(camera, 'storage', REAL_STORAGE), patch.object(camera, 'MOUNT', absent), patch.object(camera, '_mounted', return_value=False), patch.object(camera, 'photo') as photo:
            with self.assertRaises(camera.SerraError):
                camera.capture()
            photo.assert_not_called()
        self.assertFalse((absent / 'serra').exists())

    def test_camera_errors_do_not_disclose_api_token(self):
        from urllib.error import HTTPError
        camera.CONFIG.write_text(json.dumps({'hostname':'camera.local','api_key':'private-test-token'}))
        with patch.object(camera._http, 'open', side_effect=HTTPError('http://camera/?token=private-test-token',401,'Unauthorized',{},None)):
            with self.assertRaises(camera.SerraError) as raised:
                camera.photo()
        self.assertNotIn('private-test-token',str(raised.exception))
        self.assertIn('401',str(raised.exception))

    def test_quota_removes_oldest_only_and_preserves_unrelated_files(self):
        now = time.time()
        oldest = self.segment(now - 100, b'a' * 40)
        newest = self.segment(now - 10, b'b' * 40)
        foreign = self.root / 'family-video.mp4'
        foreign.write_bytes(b'keep me')
        patch.object(camera, 'settings', lambda:{'quota_gb':100 / 1024**3}).start()
        camera._room(self.media, 30)
        self.assertFalse((self.media / 'segments' / oldest).exists())
        self.assertTrue((self.media / 'segments' / newest).exists())
        self.assertEqual(foreign.read_bytes(), b'keep me')
        self.assertLessEqual(camera._usage(self.media) + 30, 100)

    def test_active_encode_input_is_never_deleted_for_space(self):
        name = self.frame(time.time(), b'a' * 80, batch='active')
        patch.object(camera, 'settings', lambda:{'quota_gb':100 / 1024**3}).start()
        with self.assertRaises(camera.SerraError):
            camera._room(self.media, 30)
        self.assertTrue((self.media / 'pending' / name).exists())

    def test_export_retention_lock_prevents_source_deletion(self):
        name = self.segment(time.time(), b'a' * 80)
        patch.object(camera, 'settings', lambda:{'quota_gb':100 / 1024**3}).start()
        with camera._lock('retention', shared=True):
            with self.assertRaises(camera.SerraError):
                camera._room(self.media, 30)
        self.assertTrue((self.media / 'segments' / name).exists())

    def test_recovery_keeps_unencoded_and_removes_committed_duplicates(self):
        now = time.time()
        done = self.frame(now - 20, batch='interrupted')
        pending = self.frame(now, batch='interrupted')
        self.segment(now - 20)
        camera.recover()
        self.assertFalse((self.media / 'pending' / done).exists())
        self.assertTrue((self.media / 'pending' / pending).exists())
        with camera._db() as db:
            rows = list(db.execute('SELECT name,batch FROM frames'))
        self.assertEqual([(r['name'],r['batch']) for r in rows], [(pending,None)])

    def test_failed_encode_keeps_every_input_for_retry(self):
        names = [self.frame(time.time() + n) for n in range(3)]
        patch.object(camera, '_run_encoder', side_effect=camera.SerraError('encoder unavailable')).start()
        camera.compact()
        self.assertTrue(all((self.media / 'pending' / n).exists() for n in names))
        self.assertIn('encoder unavailable', camera._state()['encode_error'])
        self.assertEqual(camera._state()['reserved_bytes'], 0)

    def test_windows_do_not_claim_uncollected_time(self):
        now = time.time()
        self.segment(now - 25 * 3600)
        self.segment(now - 3600)
        self.assertEqual(len(camera.window(24)), 1)
        self.assertEqual(len(camera.window(48)), 2)
        self.assertEqual(len(camera.window(72)), 2)
        for invalid in ('../secret', 0, 8761):
            with self.assertRaises(camera.SerraError):
                camera.window(invalid)

    def test_hls_uses_authenticated_same_origin_ranges_without_secrets(self):
        self.segment(time.time() - 30, b'x' * 20)
        self.segment(time.time() - 10, b'y' * 20)
        text = camera.playlist(24).decode()
        self.assertIn('#EXT-X-MAP:URI="/api/serra/segment?id=', text)
        self.assertIn('#EXT-X-BYTERANGE:16@4', text)
        self.assertIn('#EXT-X-DISCONTINUITY\n', text)
        self.assertNotIn('token', text)
        self.assertNotIn('192.168.', text)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_indexed_preview_has_full_duration_seek_and_identical_video_packets(self):
        payloads = []
        for color in ('green','red'):
            image = self.root / (color + '.jpg')
            subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi',
                            '-i',f'color=c={color}:s=2304x1296','-frames:v','1','-threads','1','-y',str(image)],check=True)
            payloads.append(image.read_bytes())
        base = time.time()
        for index in range(2): self.frame(base + index, payloads[0])
        camera.compact()
        for index in range(7): self.frame(base + 10 + index, payloads[1])
        camera.compact()
        with camera._db() as db:
            paths = [self.media / 'segments' / row['name'] for row in db.execute('SELECT name FROM segments ORDER BY id')]
        view = camera.mp4_view.build(paths)
        with view.open() as reader:
            contents = reader.read(view.size)
            for start,length in [(0,16),(len(view.header)-4,24),(view.size-20,20)]:
                reader.seek(start)
                self.assertEqual(reader.read(length),contents[start:start+length])
        movie = self.root / 'indexed.mp4'
        movie.write_bytes(contents)
        probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
                           '-show_entries','stream=width,height,nb_read_frames:format=duration','-of','json',str(movie)]))
        stream = probe['streams'][0]
        self.assertEqual((stream['width'],stream['height']),(2304,1296))
        self.assertEqual(int(stream['nb_read_frames']),9)
        self.assertAlmostEqual(float(probe['format']['duration']),9/24,places=5)
        def packet_hashes(path):
            output = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_packets','-show_data_hash','sha256',
                              '-select_streams','v:0','-show_entries','packet=data_hash','-of','json',str(path)]))
            return [row['data_hash'] for row in output['packets']]
        self.assertEqual(packet_hashes(movie),[value for path in paths for value in packet_hashes(path)])
        self.assertFalse(any((self.media / 'segments').glob('*.tmp')))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_continuous_preview_crosses_two_frame_boundary_without_losing_samples(self):
        image = self.root / 'native.jpg'
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi',
                        '-i','color=c=green:s=2304x1296','-frames:v','1','-threads','1','-y',str(image)],check=True)
        payload = image.read_bytes()
        base = time.time()
        for index in range(2):
            self.frame(base + index, payload)
        camera.compact()
        for index in range(7):
            self.frame(base + 10 + index, payload)
        camera.compact()

        class Request:
            close_connection = False
            wfile = io.BytesIO()
            headers = {}
            def send_response(self, code): self.code = code
            def send_header(self, name, value): self.headers[name] = value
            def end_headers(self): pass
            def authenticated_session(self): return {'test': True}

        request = Request()
        camera._mp4(request, 24, download=False)
        self.assertEqual(request.code, 200)
        self.assertTrue(request.headers['Content-Disposition'].startswith('inline;'))
        movie = self.root / 'continuous.mp4'
        movie.write_bytes(request.wfile.getvalue())
        probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
                           '-show_entries','stream=width,height,nb_read_frames','-of','json',str(movie)]))
        stream = probe['streams'][0]
        self.assertEqual((stream['width'],stream['height']), (2304,1296))
        self.assertEqual(int(stream['nb_read_frames']),9)
        self.assertEqual(len(list((self.media / 'segments').glob('*.mp4'))),2)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_native_mp4_preserves_resolution_and_every_frame(self):
        image = self.root / 'native.jpg'
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi',
                        '-i','color=c=green:s=2304x1296','-frames:v','1','-threads','1','-y',str(image)],check=True)
        payload = image.read_bytes()
        for index in range(3):
            self.frame(time.time() + index, payload)
        camera.compact()
        with camera._db() as db:
            rows = list(db.execute('SELECT * FROM segments'))
            pending = db.execute('SELECT COUNT(*) FROM frames').fetchone()[0]
        self.assertEqual(len(rows),1)
        self.assertEqual(pending,0)
        movie = self.media / 'segments' / rows[0]['name']
        probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
                           '-show_entries','stream=width,height,nb_read_frames','-of','json',str(movie)]))
        stream = probe['streams'][0]
        self.assertEqual((stream['width'],stream['height']), (2304,1296))
        self.assertEqual(int(stream['nb_read_frames']),3)
        self.assertGreater(rows[0]['init'],0)
        self.assertLess(movie.stat().st_size,len(payload)*3)


if __name__ == '__main__':
    unittest.main()
