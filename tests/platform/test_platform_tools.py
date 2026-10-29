"""Regression checks for Windows/Ubuntu launch command construction."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import platform_tools as tools

class PlatformToolsTests(unittest.TestCase):
    def test_windows_venv_path(self):
        self.assertEqual(tools.venv_python(Path('/project with spaces'),'Windows'),Path('/project with spaces/.venv/Scripts/python.exe'))

    def test_ubuntu_venv_path(self):
        self.assertEqual(tools.venv_python(Path('/project'),'Linux'),Path('/project/.venv/bin/python'))

    def test_windows_npm_uses_node_and_js_instead_of_cmd(self):
        with tempfile.TemporaryDirectory(prefix='duewise path ') as directory:
            root=Path(directory);cli=root/'node_modules/npm/bin/npm-cli.js';cli.parent.mkdir(parents=True);cli.write_text('// npm test')
            def which(name):return str(root/('node.exe' if name=='node' else 'npm.cmd'))
            with patch.object(tools.platform,'system',return_value='Windows'),patch.object(tools.shutil,'which',side_effect=which):
                command=tools.npm_command('ci')
            self.assertEqual(command,[str(root/'node.exe'),str(cli),'ci'])

    def test_ubuntu_npm_symlink_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cli=root/'npm-cli.js';cli.write_text('// npm test')
            link=root/'npm'
            try:link.symlink_to(cli)
            except OSError:self.skipTest('Symlink creation is not permitted on this runner')
            with patch.object(tools.platform,'system',return_value='Linux'),patch.object(tools.shutil,'which',side_effect=lambda name:str(root/'node') if name=='node' else str(link)):
                self.assertEqual(tools.npm_command('ci'),[str(root/'node'),str(cli.resolve()),'ci'])

    def test_missing_node_reports_setup_instruction(self):
        with patch.object(tools.shutil,'which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'Node.js 24'):tools.npm_command('ci')

    def test_python_command_preserves_spaces_and_arguments(self):
        with tempfile.TemporaryDirectory(prefix='duewise path ') as directory:
            root=Path(directory);exe=tools.venv_python(root);exe.parent.mkdir(parents=True);exe.touch()
            command=tools.python_module('app.cli',['create-admin','--name','Test Name'],root)
            self.assertEqual(command[:2],[str(exe),'-c'])
            self.assertIn("'Test Name'",command[2]);compile(command[2],'<launcher>','exec')

    def test_windows_shutdown_targets_only_its_process_tree(self):
        process=Mock(pid=1234);process.poll.return_value=None
        with patch.object(tools.platform,'system',return_value='Windows'),patch.object(tools.subprocess,'run') as run:
            tools.stop_process_tree(process)
            self.assertEqual(run.call_args.args[0],['taskkill','/PID','1234','/T','/F'])

    def test_missing_venv_is_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,'setup.py'):tools.python_module('app.cli',root=Path(directory))

if __name__=='__main__':unittest.main()
