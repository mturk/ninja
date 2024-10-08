#!/usr/bin/env python3

"""Runs ./ninja and checks if the output is correct.

In order to simulate a smart terminal it uses the 'script' command.
"""

import os
import platform
import subprocess
import sys
import tempfile
import unittest
from textwrap import dedent
from typing import Dict

default_env = dict(os.environ)
default_env.pop('NINJA_STATUS', None)
default_env.pop('CLICOLOR_FORCE', None)
default_env['TERM'] = ''
NINJA_PATH = os.path.abspath('./ninja')

class BuildDir:
    def __init__(self, build_ninja: str):
        self.build_ninja = dedent(build_ninja)
        self.d = None

    def __enter__(self):
        self.d = tempfile.TemporaryDirectory()
        with open(os.path.join(self.d.name, 'build.ninja'), 'w') as f:
            f.write(self.build_ninja)
            f.flush()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.d.cleanup()

    def run(
        self,
        flags: str = '',
        pipe: bool = False,
        env: Dict[str, str] = default_env,
    ) -> str:
        ninja_cmd = '{} {}'.format(NINJA_PATH, flags)
        try:
            if pipe:
                output = subprocess.check_output(
                    [ninja_cmd], shell=True, cwd=self.d.name, env=env)
            elif platform.system() == 'Darwin':
                output = subprocess.check_output(['script', '-q', '/dev/null', 'bash', '-c', ninja_cmd],
                                                 cwd=self.d.name, env=env)
            else:
                output = subprocess.check_output(['script', '-qfec', ninja_cmd, '/dev/null'],
                                                 cwd=self.d.name, env=env)
        except subprocess.CalledProcessError as err:
            sys.stdout.buffer.write(err.output)
            raise err
        final_output = ''
        for line in output.decode('utf-8').splitlines(True):
            if len(line) > 0 and line[-1] == '\r':
                continue
            final_output += line.replace('\r', '')
        return final_output

def run(
    build_ninja: str,
    flags: str = '',
    pipe: bool = False,
    env: Dict[str, str] = default_env,
) -> str:
    with BuildDir(build_ninja) as b:
        return b.run(flags, pipe, env)

@unittest.skipIf(platform.system() == 'Windows', 'These test methods do not work on Windows')
class Output(unittest.TestCase):
    BUILD_SIMPLE_ECHO = '\n'.join((
        'rule echo',
        '  command = printf "do thing"',
        '  description = echo $out',
        '',
        'build a: echo',
        '',
    ))

    def test_issue_1418(self) -> None:
        self.assertEqual(run(
'''rule echo
  command = sleep $delay && echo $out
  description = echo $out

build a: echo
  delay = 3
build b: echo
  delay = 2
build c: echo
  delay = 1
''', '-j3'),
'''[1/3] echo c\x1b[K
c
[2/3] echo b\x1b[K
b
[3/3] echo a\x1b[K
a
''')

    def test_issue_1214(self) -> None:
        print_red = '''rule echo
  command = printf '\x1b[31mred\x1b[0m'
  description = echo $out

build a: echo
'''
        # Only strip color when ninja's output is piped.
        self.assertEqual(run(print_red),
'''[1/1] echo a\x1b[K
\x1b[31mred\x1b[0m
''')
        self.assertEqual(run(print_red, pipe=True),
'''[1/1] echo a
red
''')
        # Even in verbose mode, colors should still only be stripped when piped.
        self.assertEqual(run(print_red, flags='-v'),
'''[1/1] printf '\x1b[31mred\x1b[0m'
\x1b[31mred\x1b[0m
''')
        self.assertEqual(run(print_red, flags='-v', pipe=True),
'''[1/1] printf '\x1b[31mred\x1b[0m'
red
''')

        # CLICOLOR_FORCE=1 can be used to disable escape code stripping.
        env = default_env.copy()
        env['CLICOLOR_FORCE'] = '1'
        self.assertEqual(run(print_red, pipe=True, env=env),
'''[1/1] echo a
\x1b[31mred\x1b[0m
''')

    def test_issue_1966(self) -> None:
        self.assertEqual(run(
'''rule cat
  command = cat $rspfile $rspfile > $out
  rspfile = cat.rsp
  rspfile_content = a b c

build a: cat
''', '-j3'),
'''[1/1] cat cat.rsp cat.rsp > a\x1b[K
''')


    def test_pr_1685(self) -> None:
        # Running those tools without .ninja_deps and .ninja_log shouldn't fail.
        self.assertEqual(run('', flags='-t recompact'), '')
        self.assertEqual(run('', flags='-t restat'), '')

    def test_issue_2048(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'build.ninja'), 'w'):
                pass

            with open(os.path.join(d, '.ninja_log'), 'w') as f:
                f.write('# ninja log v4\n')

            try:
                output = subprocess.check_output([NINJA_PATH, '-t', 'recompact'],
                                                 cwd=d,
                                                 env=default_env,
                                                 stderr=subprocess.STDOUT,
                                                 text=True
                                                 )

                self.assertEqual(
                    output.strip(),
                    "ninja: warning: build log version is too old; starting over"
                )
            except subprocess.CalledProcessError as err:
                self.fail("non-zero exit code with: " + err.output)

    def test_depfile_directory_creation(self) -> None:
        b = BuildDir('''\
            rule touch
              command = touch $out && echo "$out: extra" > $depfile

            build somewhere/out: touch
              depfile = somewhere_else/out.d
            ''')
        with b:
            self.assertEqual(b.run('', pipe=True), dedent('''\
                [1/1] touch somewhere/out && echo "somewhere/out: extra" > somewhere_else/out.d
                '''))
            self.assertTrue(os.path.isfile(os.path.join(b.d.name, "somewhere", "out")))
            self.assertTrue(os.path.isfile(os.path.join(b.d.name, "somewhere_else", "out.d")))

    def test_status(self) -> None:
        self.assertEqual(run(''), 'ninja: no work to do.\n')
        self.assertEqual(run('', pipe=True), 'ninja: no work to do.\n')
        self.assertEqual(run('', flags='--quiet'), '')

    def test_ninja_status_default(self) -> None:
        'Do we show the default status by default?'
        self.assertEqual(run(Output.BUILD_SIMPLE_ECHO), '[1/1] echo a\x1b[K\ndo thing\n')

    def test_ninja_status_quiet(self) -> None:
        'Do we suppress the status information when --quiet is specified?'
        output = run(Output.BUILD_SIMPLE_ECHO, flags='--quiet')
        self.assertEqual(output, 'do thing\n')

    def test_entering_directory_on_stdout(self) -> None:
        output = run(Output.BUILD_SIMPLE_ECHO, flags='-C$PWD', pipe=True)
        self.assertEqual(output.splitlines()[0][:25], "ninja: Entering directory")

    def test_tool_inputs(self) -> None:
        plan = '''
rule cat
  command = cat $in $out
build out1 : cat in1
build out2 : cat in2 out1
build out3 : cat out2 out1 | implicit || order_only
'''
        self.assertEqual(run(plan, flags='-t inputs out3'),
'''implicit
in1
in2
order_only
out1
out2
''')

    def test_explain_output(self):
        b = BuildDir('''\
            build .FORCE: phony
            rule create_if_non_exist
              command = [ -e $out ] || touch $out
              restat = true
            rule write
              command = cp $in $out
            build input : create_if_non_exist .FORCE
            build mid : write input
            build output : write mid
            default output
            ''')
        with b:
            # The explain output is shown just before the relevant build:
            self.assertEqual(b.run('-v -d explain'), dedent('''\
                ninja explain: .FORCE is dirty
                [1/3] [ -e input ] || touch input
                ninja explain: input is dirty
                [2/3] cp input mid
                ninja explain: mid is dirty
                [3/3] cp mid output
                '''))
            # Don't print "ninja explain: XXX is dirty" for inputs that are
            # pruned from the graph by an earlier restat.
            self.assertEqual(b.run('-v -d explain'), dedent('''\
                ninja explain: .FORCE is dirty
                [1/3] [ -e input ] || touch input
                '''))

if __name__ == '__main__':
    unittest.main()
