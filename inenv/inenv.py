# Licensed under the MIT license:
# http://www.opensource.org/licenses/MIT-license
# Copyright (c) 2015, Parham Negahdar <pnegahdar@gmail.com>
from collections import defaultdict
import ConfigParser
import hashlib
import os
import shutil
import subprocess
import sys

from virtualenv import create_environment
import click
from click.termui import isatty

from version import __version__


FILE_NAME = 'inenv.ini'
ACTIVATE_FILE_NAME = 'inenv.sh'

ORIGINAL_PATH = None
INI_PATH = None
RECURSION_LIMIT = 100

SHELL_ACTIVATOR_SETUP_ENVVAR = 'INENV_SWITCH_SETUP'

ARG_SHOULD_EVAL = 'should_eval'

RE_ENTER_ERR_CODE = 255

NORMAL_CMDS = ['init', 'clean', 'runall', 'run']
REENTRANT_CMDS = ['jump']


### PATH STUFF
def get_ini_path():
    """Walks up till it finds a inenv.ini"""
    global INI_PATH
    directory = os.path.realpath(os.path.curdir)
    if INI_PATH:
        return INI_PATH
    x = RECURSION_LIMIT
    while x > 0:
        ini_path = os.path.join(directory, FILE_NAME)
        if not os.access(directory, os.W_OK):
            exit_with_err(
                "Lost permissions walking up to {}. Unable to find {}".format(directory, FILE_NAME))
        if os.path.isfile(ini_path):
            INI_PATH = ini_path
            return ini_path
        parent_dir = os.path.realpath(os.path.join(directory, '..'))
        if parent_dir == directory:
            exit_with_err("Walked all the way up to {} and was unable to find {}".format(parent_dir,
                                                                                         FILE_NAME))
        directory = parent_dir
        x -= 1
    exit_with_err("Recursion limit exceeded unable to find inenv.ini")


def get_working_path():
    '''Returns the path which the inenv command was envoked from'''
    return os.path.join(os.path.dirname(get_ini_path()), '.inenv/')


def rel_path_to_abs(path):
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(os.path.dirname(get_ini_path()), path))


def get_venv_path(venv_name):
    return os.path.join(get_working_path(), venv_name)


def get_execfile_path(venv_name):
    return os.path.join(get_venv_path(venv_name), 'bin/activate_this.py')


def is_subcmd(venv_name):
    return (venv_name in NORMAL_CMDS) or (venv_name in REENTRANT_CMDS)
    

def switch_script_upto_date():
    return os.getenv(SHELL_ACTIVATOR_SETUP_ENVVAR) == str(__version__)


### Venv Stuff

def subprocess_call(cmd_args, verbose):
    output = sys.stdout
    if not verbose:
        output = subprocess.PIPE

    proc = subprocess.Popen(' '.join(cmd_args), stdin=sys.stdin, stdout=output,
                            stderr=sys.stderr, shell=True)
    retcode = proc.wait()
    if retcode != 0:
        exit_with_err('Runtime Error')


def extract_ini_section(parser, section):
    section_parts = section.split(":")
    data = {}
    try:
        env_name, env_var = section_parts[0], section_parts[1]
    except IndexError:
        env_name, env_var = section_parts[0], None
    if env_var and not os.getenv(env_var):
        return env_name, {}
    data['deps'] = []
    try:
        deps = [dep.strip() for dep in parser.get(section, 'deps').split(',')]
        data['deps'] += deps
    except ConfigParser.NoOptionError:
        pass
    try:
        storage = parser.get(section, 'env_storage')
        if not os.access(os.path.abspath(storage), os.W_OK):
            exit_with_err(
                "INI Parse Error: The env_storage for {} provided is not a directory or doesn't "
                "have write permissions".format(
                    section))
        data['env_stoarge'] = storage
    except ConfigParser.NoOptionError:
        pass
    return env_name, data


def parse_ini(path_to_ini):
    """Should return something like { 'envname' : [deplist] }"""
    parsed = defaultdict(dict)
    parser = ConfigParser.ConfigParser()
    with open(path_to_ini) as parse_file:
        try:
            parser.readfp(parse_file)
        except ConfigParser.ParsingError:
            exit_with_err("Unable to parse your ini file")

    sections = parser.sections()
    for section in sections:
        parsed_section, data = extract_ini_section(parser, section)
        for k, v in data.items():
            parsed[parsed_section][k] = v
    return parsed


def venv_exists(venv_name):
    ini_path = get_ini_path()
    parsed = parse_ini(ini_path).get(venv_name)
    if not parsed:
        exit_with_err("Unable to find venv {} in ini {}".format(venv_name, ini_path))

    if is_subcmd(venv_name):
        exit_with_err("Cannot use subcommand '{}' as venv name.".format(venv_name))
        
    exec_path = get_execfile_path(venv_name)
    return os.path.isfile(exec_path)


def create_venv(venv_name):
    create_environment(get_venv_path(venv_name))


def delete_venv(venv_name):
    shutil.rmtree(get_venv_path(venv_name))


def file_md5(path):
    """Temporary until pip file parsing is done"""
    hashlib.md5(open(path, 'rb').read()).hexdigest()


def activate_and_get_deps(venv_name):
    if not venv_exists(venv_name):
        create_venv(venv_name)
    activate_venv(venv_name)
    ini_path = get_ini_path()
    parsed = parse_ini(ini_path).get(venv_name)
    if not parsed:
        exit_with_err("Unable to find venv {} in ini {}".format(venv_name, ini_path))
    deps = parsed.get('deps', [])
    return deps

def setup_venv(venv_name, verbose, quiet):
    """Main venv functionality entry point, run before doing things"""
    deps = activate_and_get_deps(venv_name)
    for dep in deps:
        if not quiet:
            print "Installing {}".format(dep)
        if dep.startswith('file:'):
            dep = rel_path_to_abs(dep.split('file:')[1])
            subprocess_call(['pip', 'install', '-r', dep], verbose)
        else:
            subprocess_call(['pip', 'install', dep], verbose)


def jump_to_req_dir(venv_name):
    """Jumps to the directory of the reqs file"""
    deps = activate_and_get_deps(venv_name)
    switch(venv_name)
    for dep in deps:
        if dep.startswith('file:'):
            dep = rel_path_to_abs(dep.split('file:')[1])
            dirname = os.path.dirname(dep)
            print "cd {}".format(dirname)
            break
    
def activate_venv(venv_name):
    if not venv_exists(venv_name):
        exit_with_err("Cannot activate venv {} because it does not exist".format(venv_name))
        return
    global ORIGINAL_PATH
    if not ORIGINAL_PATH:
        ORIGINAL_PATH = sys.path
    sys.path = ORIGINAL_PATH
    exec_file_path = get_execfile_path(venv_name)
    execfile(exec_file_path, dict(__file__=exec_file_path))


def print_err(msg):
    click.echo(click.style(msg, fg='red'), err=True)

def exit_with_err(msg=None):
    if msg:
        print_err(msg)
    sys.exit(1)

def sub_shell():
    shell = os.getenv('SHELL')
    args = ['-l']
    if 'zsh' in shell:
        args.append('-f')
    os.execv(shell, args)
    # proc = subprocess.Popen(shell, stdin=sys.stdin, stdout=sys.stdout,
    # stderr=sys.stderr, shell=True, executable=shell)
    # proc.wait()


def setup_inenv_shell_activator():
    '''Creates switch script. Returns true if switch script is up to date, otherwise false.'''
    if switch_script_upto_date():
        return True
    
    # Write activate scripts
    activate_template = '''export {}={}
function inenv() {{
    inenv_helper {} $@ 
    rc=$?
    if [[ $rc == {} ]]; then
        tmpout=`inenv_helper $@`
        eval $tmpout
    elif [[ $rc == 0 ]]; then
        inenv_helper $@
    fi
}}
'''.format(SHELL_ACTIVATOR_SETUP_ENVVAR, __version__, ARG_SHOULD_EVAL, RE_ENTER_ERR_CODE)

    activate_file = os.path.join(get_venv_path(get_working_path()), ACTIVATE_FILE_NAME)
    with open(activate_file, "w") as activate_template_file:
        activate_template_file.write(activate_template)
    
    # Note: this must be printed to stderr or these statements will be evaluated on switch
    print_err("Error! Your inenv switch script is out of date.")
    print_err("Please source the following in your rc file if you want to switch envs:")
    print_err(activate_file)
    
    return False
    

def init(venv_names, quiet=False):
    """Sets up all the venvs for the project"""
    ini_path = get_ini_path()
    if venv_names:
        venvs = venv_names
    else:
        venvs = parse_ini(ini_path).keys()
    
    map(lambda x: setup_venv(x, verbose=True, quiet=quiet), venvs)
    setup_inenv_shell_activator()


def clean(venv_name):
    """Deletes the given venv to start over"""
    venv_path = venv_exists(venv_name)
    if not venv_path:
        exit_with_err('The venv does not exists at {}'.format(get_venv_path(venv_name)))
    run = click.confirm('Going to delete {} venv'.format(venv_name))
    if run:
        delete_venv(venv_name)


def run(venv_name, cmd, nobuild=False, verbose=False, quiet=False):
    """Runs a command in the env provided"""
    if nobuild:
        activate_venv(venv_name)
    else:
        setup_venv(venv_name, verbose, quiet)
    subprocess_call(cmd, True)


def switch(venv_name):
    """Switch to a different virtual env"""
    if not setup_inenv_shell_activator():
        # Switch script out of date, so refuse to continue
        sys.exit(1)
    
    SHELL = os.getenv('SHELL')
    to_run = ""
    if not venv_exists(venv_name):
        to_run += "inenv init {}\n".format(venv_name)

    to_source = os.path.join(get_venv_path(venv_name), 'bin/activate')
    if any([shell in SHELL for shell in ['bash', 'zsh']]):
        source_cmd = 'source'
    else:
        source_cmd = '.'
    to_run += "{source_cmd} {rest}".format(source_cmd=source_cmd, rest=to_source)
    print to_run


def runall(args, nobuild, verbose, quiet):
    ini_path = get_ini_path()
    venvs = parse_ini(ini_path).keys()
    for cmd in venvs:
        run(cmd, args, nobuild, verbose, quiet)


def print_help():
    help_text = '''Usage:
    1. inenv ENV_NAME OPTIONS
    Switches to venv ENV_NAME.

    2. inenv ENV_NAME OPTIONS -- COMMANDS
    Runs commands in the specified venv.
    Alternatively, you can run: inenv run ENV_NAME OPTIONS -- COMMANDS

    3. inenv runall OPTIONS -- COMMANDS
    Runs commands in all existing venvs.

    4. inenv SUB_COMMAND ARGS OPTIONS
    See list of sub-commands.

    Options:
      --help, -h: Print the help message and exit
      --quiet, -q: Does not print anything to stdout.
      --verbose, -v: Prints output of installations
      --nobuild, -n: Does not install packages

    Sub-commands:
      init ENV_NAME_1 ENV_NAME_2 Etc.:
           Initializes all listed venvs.
           If no venvs are listed, it initializes all of them.

      clean ENV_NAME_1 ENV_NAME_2 Etc.:
           Deletes the listed venvs to start over.

      jump ENV_NAME:
           Jumps to the directory of the requirements.txt file and
           switches to ENV_NAME.
'''
    click.echo(help_text)


def print_version():
    '''Prints the version number'''
    print "inenv, version {}".format(__version__)    


@click.command()
@click.option('-v', '--verbose', is_flag=True)
@click.option('-n', '--nobuild', is_flag=True)
@click.option('-q', '--quiet', is_flag=True)
@click.option('-h', '--help', is_flag=True)
@click.option('--version', is_flag=True)
@click.argument('cmdargs', nargs=-1)
def main_cli(cmdargs, verbose, nobuild, quiet, help, version):
    # Default to quiet if not tty
    if not isatty(sys.stdout):
        quiet = True

    # if we do not need to re-enter, then print version/help
    # if appropriate, ignore all other arguments
    if not cmdargs or cmdargs[0] != ARG_SHOULD_EVAL:
        if version:
            print_version()
            return
        elif help:
            print_help()
            return
        elif not cmdargs:
            # only flags passed, so we don't want to reenter
            return
        
    # if we do need to re-enter, then return immediately on
    # version/help so as not to evaluate their output
    if cmdargs[0] == ARG_SHOULD_EVAL:
        if version or help:
            return
    
    cmd = cmdargs[0]
    args = cmdargs[1:]
    
    # Check if stdout of command needs to be evaluated in shell
    if cmd == ARG_SHOULD_EVAL:
        if not args:
            sys.exit()

        subcmd = args[0]
        subargs = args[1:]
        
        if (not subargs and subcmd not in NORMAL_CMDS) or (subcmd in REENTRANT_CMDS):
            # this is a switch command, so re-enter
            sys.exit(RE_ENTER_ERR_CODE)            
        sys.exit(0)

    if cmd == 'init':
        init(args, quiet=quiet) 
        return
    elif cmd == 'clean':
        if not args:
            exit_with_err('Subcommand clean expects at least 1 argument.')
        
        map(clean, args)
        return
    elif cmd == 'jump':
        if len(args) != 1:
            exit_with_err('Subcommand jump expects only 1 argument.')            
        jump_to_req_dir(args[0])
        return
    elif cmd == 'runall':
        runall(args, nobuild, verbose, quiet)
        return
    elif cmd == 'run':
        if not args:
            exit_with_err('Subcommand run expects arguments.')
            
        venv = args[0]
        args = args[1:]
        run(venv, args, nobuild, verbose, quiet)
        return

    if not args:
        switch(cmd)
        return

    run(cmd, args, nobuild, verbose, quiet)


if __name__ == "__main__":
    main_cli()
