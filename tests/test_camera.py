import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'lib'))
sys.path.insert(0, str(ROOT/'tests'))
import captions  # noqa: E402
import test_runtime as runtime  # noqa: E402
import test_translate  # noqa: E402

loader = importlib.machinery.SourceFileLoader('camera_worker', str(ROOT/'bin/talkey-camera-worker'))
spec = importlib.util.spec_from_loader(loader.name, loader)
worker = importlib.util.module_from_spec(spec)
loader.exec_module(worker)

LISTING = '''[video4linux2,v4l2 @ 0x5] Raw       :     yuyv422 :           YUYV 4:2:2 : 640x480 176x144 320x240 1280x720
[video4linux2,v4l2 @ 0x5] Compressed:       mjpeg :          Motion-JPEG : 640x480 960x540 1280x720 1920x1080
[video4linux2,v4l2 @ 0x5] Raw       :        nv12 :         Y/UV 4:2:0 : 640x480 1920x1080
'''


def line(n, dutch, pieces, live=False, done=True):
    return {'n': n, 'dutch': dutch, 'live': live, 'done': done,
            'pieces': [{'text': text, 'start': start, 'end': end} for text, start, end in pieces]}


class CaptionTests(unittest.TestCase):
    def test_the_phrase_on_air_shows_before_a_newer_one(self):
        lines = [line(1, 'Eerste zin.', [('First sentence.', 10.0, 12.0)]),
                 line(2, 'Tweede', [], live=True, done=False)]
        dutch, english = captions.words(lines, 11.0)
        self.assertEqual([w for w, _ in dutch], ['Eerste', 'zin.'])
        dutch, english = captions.words(lines, 12.5)
        self.assertEqual(dutch, [('Tweede', 'heard')])
        self.assertEqual(english, [('…', 'wait')])

    def test_words_are_marked_as_the_voice_reaches_them(self):
        lines = [line(1, 'Ja.', [('Yes and', 0.0, 1.0), ('one two three', 1.0, 2.0), ('later', None, None)])]
        _, english = captions.words(lines, 1.5)
        self.assertEqual(english, [('Yes', 'played'), ('and', 'played'), ('one', 'said'), ('two', 'said'),
                                   ('three', 'next'), ('later', 'queued')])

    def test_rows_follow_the_voice(self):
        rows = [[('a', 'played', 0)], [('b', 'said', 0)], [('c', 'next', 0)], [('d', 'queued', 0)]]
        self.assertEqual(captions.following(rows), rows[1:3])
        rows = [[('a', 'played', 0)], [('b', 'played', 0)], [('c', 'played', 0)]]
        self.assertEqual(captions.following(rows), rows[1:])

    def test_subtitles_are_drawn_inside_their_box_only(self):
        width, height = 320, 180
        frame = bytearray([90]) * (width * height * 3 // 2)
        before = bytes(frame)
        drawer = captions.Captions(width, height)
        drawer.draw(frame, [('Hallo', 'nl')], [('Hello', 'said')])
        top, left, y = drawer.layer[:3]
        luma = np.frombuffer(frame, dtype=np.uint8)[:width * height].reshape(height, width)
        old = np.frombuffer(before, dtype=np.uint8)[:width * height].reshape(height, width)
        self.assertFalse(np.array_equal(luma[top:top + y.shape[0], left:left + y.shape[1]],
                                        old[top:top + y.shape[0], left:left + y.shape[1]]))
        luma[top:top + y.shape[0], left:left + y.shape[1]] = 90
        self.assertTrue(np.array_equal(luma, old))

    def test_nothing_is_drawn_without_words(self):
        frame = bytearray([90]) * (320 * 180 * 3 // 2)
        captions.Captions(320, 180).draw(frame, [], [])
        self.assertEqual(set(frame), {90})


class CameraWorkerTests(unittest.TestCase):
    def test_ioctl_numbers_match_the_kernel(self):
        self.assertEqual(worker.VIDIOC_S_FMT, 0xC0D05605)
        self.assertEqual(worker.VIDIOC_S_PARM, 0xC0CC5616)
        self.assertEqual(worker.VIDIOC_DQEVENT, 0x80885659)
        self.assertEqual(worker.VIDIOC_SUBSCRIBE_EVENT, 0x4020565A)

    def test_the_webcam_is_asked_for_a_compressed_format_when_it_has_the_size(self):
        self.assertEqual(worker.input_format(LISTING, '1280x720'), 'mjpeg')
        self.assertEqual(worker.input_format(LISTING, '176x144'), 'yuyv422')
        self.assertIsNone(worker.input_format(LISTING, '1024x768'))

    def test_the_webcam_is_the_first_real_main_node(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, place, index in (('video0', 'devices/virtual/video4linux', '0'),
                                       ('video2', 'devices/pci0/usb4/video4linux', '1'),
                                       ('video10', 'devices/pci0/usb4/video4linux', '0')):
                node = root/place/name
                node.mkdir(parents=True)
                (node/'index').write_text(index + '\n')
                (root/'class').mkdir(exist_ok=True)
                (root/'class'/name).symlink_to(node)
            sysfs, worker.SYSFS = worker.SYSFS, root/'class'
            try:
                self.assertEqual(worker.webcam(), '/dev/video10')
            finally:
                worker.SYSFS = sysfs

    def test_subtitles_stay_a_moment_after_the_english_has_played(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state/'translate.subtitles').write_text('{"lines": [{"n": 1, "dutch": "Ja.", "done": true, '
                                                     '"pieces": [{"text": "Yes.", "start": 1, "end": 2}]}]}')
            (state/'translate.showing').touch()
            subtitles = worker.Subtitles(state)
            self.assertEqual(subtitles.now(100.0)[0], [('Ja.', 'nl')])
            (state/'translate.showing').unlink()
            self.assertEqual(subtitles.now(100.0 + worker.LINGER_S - 0.1)[0], [('Ja.', 'nl')])
            self.assertEqual(subtitles.now(100.0 + worker.LINGER_S), ([], []))

    def test_the_size_must_be_even(self):
        self.assertEqual(worker.size('1280x720'), (1280, 720))
        for bad in ('1281x720', '1280', '0x0'):
            with self.assertRaises(ValueError):
                worker.size(bad)


class CameraRuntimeTests(unittest.TestCase):
    run_key = runtime.RuntimeTests.run_key
    wait_for = runtime.RuntimeTests.wait_for
    listen = test_translate.TranslateRuntimeTests.listen

    def setUp(self):
        test_translate.TranslateRuntimeTests.setUp(self)
        wrapper = self.commands/'python3'
        fake = '''if sys.argv[1].endswith('/talkey-camera-worker'):
    pathlib.Path(os.environ['TEST_ROOT'], 'camera-device').write_text(os.environ['TALKEY_CAMERA_DEVICE'])
    while True: time.sleep(1)
'''
        wrapper.write_text(wrapper.read_text().replace('os.execv(', fake + 'os.execv(', 1))

    def tearDown(self):
        self.run_key('camera', 'stop', check=False)
        test_translate.TranslateRuntimeTests.tearDown(self)

    def status(self, mode='translate'):
        return test_translate.TranslateRuntimeTests.status(self, mode)

    def device(self):
        node = self.root/'video4linux'/'video7'
        node.mkdir(parents=True)
        (node/'name').write_text('talkey camera\n')

    def test_translate_brings_up_the_camera_and_leaves_it_on(self):
        self.device()
        self.listen()
        self.assertEqual(self.status('camera'), {'enabled': True, 'device': '/dev/video7'})
        self.wait_for(lambda: (self.root/'camera-device').exists())
        self.assertEqual((self.root/'camera-device').read_text(), '/dev/video7')
        self.run_key('translate', 'stop')
        self.assertTrue(self.status('camera')['enabled'])
        self.run_key('camera', 'stop')
        self.assertFalse(self.status('camera')['enabled'])

    def test_translate_without_the_device_leaves_the_camera_alone(self):
        self.listen()
        self.assertEqual(self.status('camera'), {'enabled': False, 'device': ''})
        self.assertNotEqual(self.run_key('camera', 'start', check=False).returncode, 0)

    def test_translate_can_leave_the_camera_out(self):
        self.device()
        self.env['TALKEY_TRANSLATE_CAMERA'] = '0'
        self.listen()
        self.assertFalse(self.status('camera')['enabled'])
        self.run_key('camera', 'toggle')
        self.assertTrue(self.status('camera')['enabled'])


if __name__ == '__main__':
    unittest.main()
