import os
from pathlib import Path
import subprocess

import pytest

import build_shell_updater

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'update-flocks.sh'
pytestmark = pytest.mark.no_database


@pytest.fixture
def shell_tools(tmp_path):
    binary = tmp_path/'bin'
    binary.mkdir()
    for name, source in {
        'curl': '''#!/usr/bin/env bash
printf '%s\\0' "$@" > "$FLOCKS_TEST_CURL_ARGS"
if [[ "${FLOCKS_TEST_CURL_EXIT:-0}" -ne 0 ]]; then exit "$FLOCKS_TEST_CURL_EXIT"; fi
while [[ "$#" -gt 0 ]]; do
    if [[ "$1" == --output ]]; then printf fixture > "$2"; break; fi
    shift
done
''',
        'python3': '''#!/usr/bin/env bash
printf '%s\\0' "$@" > "$FLOCKS_TEST_PYTHON_ARGS"
cat > "$FLOCKS_TEST_PYTHON_SOURCE"
exit "${FLOCKS_TEST_PYTHON_EXIT:-0}"
''',
    }.items():
        path = binary/name
        path.write_text(source)
        path.chmod(0o755)
    env = dict(os.environ, PATH=str(binary)+os.pathsep+os.environ['PATH'],
               FLOCKS_TEST_CURL_ARGS=str(tmp_path/'curl.args'),
               FLOCKS_TEST_PYTHON_ARGS=str(tmp_path/'python.args'),
               FLOCKS_TEST_PYTHON_SOURCE=str(tmp_path/'engine.py'))
    return tmp_path, env


def read_args(path):
    return path.read_bytes().decode().rstrip('\0').split('\0')


def test_shell_syntax_and_embedded_engine_match_canonical_source():
    subprocess.run(['bash','-n',str(SCRIPT)],check=True)
    canonical=(ROOT/'update-flocks.py').read_text().rpartition("\nif __name__ == '__main__':\n")[0]
    generated=SCRIPT.read_text().split("<<'FLOCKS_UPDATER_PY'\n",1)[1]
    assert generated == canonical+"\nsys.argv[0] = 'update-flocks.sh'\nraise SystemExit(main())\nFLOCKS_UPDATER_PY\n"


def test_shell_help_executes_without_network_or_install_changes():
    result=subprocess.run(['bash',str(SCRIPT),'--help'],capture_output=True,text=True)
    assert result.returncode==0 and 'usage: update-flocks.sh' in result.stdout


def test_bash_downloads_once_preserves_arguments_and_cleans_temp_files(shell_tools):
    directory,env=shell_tools
    customizations=directory/'Pro account/customizations'
    result=subprocess.run(['bash',str(SCRIPT),'--pro-root','/home/pro/Pro install',
                           '--customizations-root',str(customizations)],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    curl=read_args(directory/'curl.args')
    assert curl[-1]=='https://gitee.com/flocks/flocks/repository/archive/fix/process_11.12_en'
    assert '--location' in curl and '--fail' in curl
    arguments=read_args(directory/'python.args')
    assert arguments[:3]==['-','--pro-root','/home/pro/Pro install']
    assert arguments[-2]=='--repository-archive'
    assert not Path(arguments[-1]).exists()
    assert not list(customizations.glob('.update-download.*'))
    assert 'def update(args):' in (directory/'engine.py').read_text()


def test_bash_offline_mode_does_not_download(shell_tools):
    directory,env=shell_tools
    result=subprocess.run(['bash',str(SCRIPT),'--repository-archive=/tmp/package with spaces.zip'],
                          env=env,capture_output=True,text=True)
    assert result.returncode==0
    assert not (directory/'curl.args').exists()
    assert read_args(directory/'python.args')==['-','--repository-archive=/tmp/package with spaces.zip']


def test_download_failure_never_invokes_installer_and_cleans_temp(shell_tools):
    directory,env=shell_tools;env['FLOCKS_TEST_CURL_EXIT']='22'
    customizations=directory/'customizations'
    result=subprocess.run(['bash',str(SCRIPT),'--customizations-root',str(customizations)],
                          env=env,capture_output=True,text=True)
    assert result.returncode==22
    assert not (directory/'python.args').exists()
    assert not list(customizations.glob('.update-download.*'))


def test_engine_failure_exit_status_is_preserved(shell_tools):
    directory,env=shell_tools;env['FLOCKS_TEST_PYTHON_EXIT']='7'
    customizations=directory/'customizations'
    result=subprocess.run(['bash',str(SCRIPT),'--customizations-root',str(customizations)],
                          env=env,capture_output=True,text=True)
    assert result.returncode==7
    assert not list(customizations.glob('.update-download.*'))


@pytest.mark.parametrize('arguments', [['--archive-url','http://invalid.example/archive'],['--pro-root'],['--unknown']])
def test_invalid_shell_arguments_do_not_reach_network_or_installer(shell_tools,arguments):
    directory,env=shell_tools
    result=subprocess.run(['bash',str(SCRIPT),*arguments],env=env,capture_output=True,text=True)
    assert result.returncode!=0
    assert not (directory/'curl.args').exists()
    assert not (directory/'python.args').exists()
