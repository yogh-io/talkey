import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

import test_auto

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import commands
from speech import strip_end_cue


class DefinitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_builtin_alias_override_and_disabled_definition(self):
        (self.user/'enter-on.json').write_text(json.dumps({'phrases': ['command enable enter']}))
        (self.user/'enter-off.json').write_text('{"enabled": false}')
        self.assertEqual(commands.resolve('COMMAND enable enter!', ROOT, self.user)['builtin'], 'enter-on')
        self.assertEqual(commands.resolve('command enter off', ROOT, self.user)['kind'], 'unknown')

    def test_unknown_and_literal_and_discussion(self):
        self.assertEqual(commands.resolve('command nonexistent', ROOT, self.user)['kind'], 'unknown')
        self.assertEqual(commands.resolve('dictate command enter on', ROOT, self.user),
                         {'kind': 'dictation', 'text': 'command enter on'})
        text = 'I said command enter on yesterday'
        self.assertEqual(commands.resolve(text, ROOT, self.user)['text'], text)
        self.assertEqual(commands.resolve('Stop listening!', ROOT, self.user)['builtin'], 'auto-stop')
        self.assertEqual(commands.resolve('dictate stop listening', ROOT, self.user)['text'], 'stop listening')

    def test_stop_listening_can_be_disabled(self):
        (self.user/'stop-listening.json').write_text('{"enabled":false}')
        self.assertEqual(commands.resolve('stop listening', ROOT, self.user)['kind'], 'unknown')

    def test_duplicate_and_malformed_commands_are_rejected(self):
        (self.user/'duplicate.json').write_text('{"phrases":["command enter on"],"exec":["true"]}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            commands.registry(ROOT, self.user)
        (self.user/'duplicate.json').write_text('{"phrases":["command bad"],"exec":"touch something"}')
        with self.assertRaisesRegex(ValueError, 'argument list'):
            commands.registry(ROOT, self.user)

    def test_script_arguments_are_literal_and_timeouts_are_reported(self):
        literal = '$(touch must-not-exist); echo bad'
        result = commands.execute({'exec': [sys.executable, '-c', 'import sys; print(sys.argv[1])', literal],
                                   'timeout_seconds': 2})
        self.assertTrue(result['ok'])
        self.assertEqual(result['stdout'].strip(), literal)
        result = commands.execute({'exec': [sys.executable, '-c', 'import time; time.sleep(10)'],
                                   'timeout_seconds': .1})
        self.assertFalse(result['ok'])
        self.assertIn('timed out', result['error'])

    def test_end_cue_requires_complete_word_suffix(self):
        self.assertEqual(strip_end_cue('Please do it. END MESSAGE!', 'end message'), 'Please do it.')
        self.assertEqual(strip_end_cue('End message.', 'end message'), '')
        self.assertEqual(strip_end_cue('End message. End message.', 'end message'), '')
        for text in ('end messages', 'resend message', 'end message tomorrow', 'send'):
            self.assertIsNone(strip_end_cue(text, 'end message'))


class VoiceCommandTests(unittest.TestCase):
    run_key = test_auto.AutoRuntimeTests.run_key
    wait_for = test_auto.AutoRuntimeTests.wait_for
    keys = test_auto.AutoRuntimeTests.keys
    status = test_auto.AutoRuntimeTests.status
    listen = test_auto.AutoRuntimeTests.listen
    speak = test_auto.AutoRuntimeTests.speak
    end_phrase = test_auto.AutoRuntimeTests.end_phrase
    tearDown = test_auto.AutoRuntimeTests.tearDown

    def setUp(self):
        test_auto.AutoRuntimeTests.setUp(self)
        self.definitions = self.root/'commands'
        self.definitions.mkdir()
        self.env.update(TALKEY_COMMANDS='1', TALKEY_COMMANDS_DIR=str(self.definitions))

    def say(self, text):
        (self.root/'final-text').write_text(text)
        take = self.speak()
        self.end_phrase()
        return take

    def test_all_enter_commands_consume_text_and_do_not_press_enter(self):
        self.listen()
        for command, enabled in [('on', True), ('off', False), ('toggle', True)]:
            self.say('Command enter ' + command + '.')
            self.assertEqual(self.status()['enter_enabled'], enabled)
            self.assertFalse(self.keys())
        self.say('ordinary text')
        self.assertEqual(sum(k[-1] == 'Return' for k in self.keys()), 1)

    def test_unknown_and_disabled_commands_never_submit(self):
        self.run_key('auto', 'enter-on')
        (self.definitions/'enter-off.json').write_text('{"enabled":false}')
        self.listen()
        self.say('command no such task')
        self.say('command enter off')
        self.assertTrue(self.status()['enter_enabled'])
        self.assertFalse(self.keys())

    def test_literal_escape_and_disabled_global_switch(self):
        self.listen()
        self.say('dictate command enter on')
        self.assertFalse(self.status()['enter_enabled'])
        self.assertEqual([k[-1] for k in self.keys() if k[0] == 'type'], ['command enter on'])
        self.run_key('auto', 'stop')
        self.env['TALKEY_COMMANDS'] = '0'
        self.listen()
        self.say('command enter on')
        self.assertFalse(self.status()['enter_enabled'])
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 2)

    def test_command_after_end_cue_stripping(self):
        self.env['TALKEY_END_PHRASE'] = 'end message'
        self.listen()
        self.say('Command enter on. End message.')
        self.assertTrue(self.status()['enter_enabled'])
        self.assertFalse(self.keys())

    def test_cancelled_command_never_changes_preference(self):
        self.listen()
        (self.root/'final-text').write_text('command enter on')
        (self.root/'wait-inference').touch()
        self.speak()
        self.mute.touch()
        self.wait_for(lambda: (self.root/'inference-started').exists())
        self.run_key('auto', 'stop')
        (self.root/'wait-inference').unlink()
        self.wait_for(lambda: not (self.state/'dictate.pending').exists())
        self.assertFalse(self.status()['enter_enabled'])
        self.assertFalse(self.keys())

    def test_stop_listening_finishes_without_typing_or_enter(self):
        self.run_key('auto', 'enter-on')
        self.env.update(TALKEY_SMART_TURN='1', TALKEY_TURN_MAX_SILENCE_MS='20000',
                        TALKEY_END_PHRASE_SILENCE_MS='160')
        (self.root/'turn-incomplete').touch()
        (self.root/'cue-text').write_text('Stop listening.')
        (self.root/'final-text').write_text('Stop listening.')
        self.listen()
        self.speak()
        self.mute.touch()
        self.wait_for(lambda: not self.status()['enabled'])
        self.wait_for(lambda: not (self.state/'dictate.pending').exists())
        self.assertEqual(self.status()['state'], 'off')
        self.assertTrue(self.status()['enter_enabled'])
        self.assertFalse(self.keys())

    def test_external_script_can_call_talkey_without_holding_up_capture(self):
        script = self.root/'action.sh'
        script.write_text('#!/bin/sh\nsleep 0.3\n"$1" auto enter-on\n')
        script.chmod(0o755)
        (self.definitions/'custom.json').write_text(json.dumps({
            'phrases': ['command custom'], 'exec': [str(script), str(ROOT/'bin/talkey')]}))
        self.listen()
        take = self.say('command custom')
        self.wait_for(lambda: (take/'command.result.json').exists() and (take/'command.result.json').stat().st_size > 0)
        self.assertTrue(json.loads((take/'command.result.json').read_text())['ok'])
        self.assertTrue(self.status()['enter_enabled'])
        self.assertTrue(self.status()['enabled'])
        self.assertFalse(self.keys())


if __name__ == '__main__':
    unittest.main()
