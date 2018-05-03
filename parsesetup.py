import contextlib
import collections
import json
import setuptools
import distutils.core
import numpy.distutils.core
import types
import sys
import os

_STDOUT_TERMINATOR = "\n{{ENDOUTPUT}}\n"

# Want a numpy + cython enabled docker image, since so many packages require one of them
DOCKER_IMAGE = "benfred/alpine_numpy"

__version__ = "0.0.1"


def parse_setup(filename, trusted=False, mock_imports=True):
    # if we trust this package, don't bother wrapping in a docker contiainer
    if trusted:
        return _unsafe_parse_setup(filename, mock_imports=mock_imports)

    filename = os.path.abspath(filename)
    package_path = os.path.dirname(filename)

    try:
        with DockerSetupParser(package_path, docker_image=DOCKER_IMAGE + ":latest") as parser:
            return parser.parse(filename, mock_imports=mock_imports)
    except Exception as e:
        # if at first we don't succeed, try again with python 2.7
        try:
            with DockerSetupParser(package_path, docker_image=DOCKER_IMAGE + ":2.7") as parser:
                ret = parser.parse(filename, mock_imports=mock_imports)
                ret['python3_error'] = str(e)
                return ret
        except Exception:
            pass

        # raise original exception, rather than new one
        raise


def _unsafe_parse_setup(setup_py_filename, mock_imports=False):
    """ this function monkey patches setuptools and imports the setup.py file located
    at package_path.

    This operation is unsafe for untrusted code, so this will normally be wrapped in
    a docker container """
    setup_py_filename = os.path.abspath(setup_py_filename)
    package_path = os.path.dirname(setup_py_filename)

    setup_args = [None]
    setup_modules = [setuptools, distutils.core, numpy.distutils.core]

    # keep track of unpatched functions so we can restore afterwards
    unpatched = [(m, m.setup) for m in setup_modules]

    # patch setup functions to just keep track of arguments passed to them
    def patched_setup(**kwargs):
        setup_args[0] = kwargs

    for module in setup_modules:
        module.setup = patched_setup

    # set up pythonpath/argv like most setup.py scripts expect
    old_path = sys.path
    old_argv = sys.argv
    os.chdir(package_path)
    sys.path = [package_path] + sys.path
    sys.argv = [setup_py_filename, "install"]

    # hack for getsentry/sentry
    sys.modules['__main__'].__file__ = setup_py_filename

    def parse():
        exec(open(setup_py_filename).read(), {
             "__name__": "__main__",
             "__builtins__": __builtins__,
             "__file__": setup_py_filename})

    try:
        parse()

    except ImportError:
        if mock_imports:
            with disable_importerror():
                parse()
        else:
            raise

    finally:
        # restore setup functions that have been patched over
        for module, setupfn in unpatched:
            module.setup = setupfn
        sys.path = old_path
        sys.argv = old_argv

    ret = setup_args[0]
    if ret is None:
        raise ValueError("setup wasn't called from setup.py")

    return ret


class DockerSetupParser(object):
    """ DockerSetupParser runs this code inside a docker container in order to
    provide some safety from untrusted setup.py files.

    This lets us parse multiple files without the expense of launching a container
    for every call. """

    def __init__(self, package_root, docker_image=DOCKER_IMAGE):
        self.package_root = os.path.abspath(package_root)
        self.docker_image = docker_image
        self.container = None
        self.docker_client = None

        if not os.path.isdir(package_root):
            raise ValueError("Path not found: '%s'" % package_root)

    def __enter__(self):
        # delay importing docker here, since won't be able to import it if we are in a container
        import docker
        self.docker_client = docker.from_env()
        code_path = os.path.dirname(os.path.abspath(__file__))
        self.container = self.docker_client.containers.run(
            self.docker_image,
            volumes={code_path: {'bind': '/home/app/code', 'mode': 'ro'},
                     self.package_root: {'bind': '/home/app/data', 'mode': 'rw'}},
            remove=True, detach=True, tty=True)
        return self

    def __exit__(self, *args):
        self.container.stop()
        self.docker_client.close()

    def parse(self, setup_filename, mock_imports=False):
        setup_filename = os.path.abspath(setup_filename)
        if not os.path.isfile(setup_filename):
            raise ValueError("File not found: '%s'" % setup_filename)

        package_path = os.path.dirname(setup_filename)
        common = os.path.commonpath([self.package_root, package_path])
        if common != self.package_root:
            raise ValueError("'%s' isn't a subdirectory of package_root" % package_path)

        relative_path = package_path[len(common) + 1:]

        flags = " --trusted --printdelimiter "
        if mock_imports:
            flags += "--mockimports "

        data_path = '/home/app/data/' + relative_path + "/" + os.path.basename(setup_filename)

        # TODO: this is still relatively heavy weight, since the python process
        # inside the container has to start for every call (~250ms or so)
        command = "python -O /home/app/code/" + os.path.basename(__file__) + flags + data_path
        result = self.container.exec_run(command)
        if result.exit_code:
            raise RuntimeError(result.output.decode("utf8"))

        terminator = _STDOUT_TERMINATOR.encode("utf8")
        if terminator not in result.output:
            # exited via something like 'raise SystemExit' or sys.exit(0)
            raise RuntimeError(result.output.decode("utf8"))

        sections = result.output.split(terminator)
        ret = json.loads(sections[-1])

        if len(sections) > 1:
            ret['stdout'] = b"\n".join(sections[:-1])
        return ret


class Mock(object):
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, *args, **kwargs):
        return self
    __call__ = __getitem__ = __setitem__ = __add__ = __getattr__

    def __str__(self):
        return "<MockObject>"
    __repr__ = __str__

    def __fspath__(self):
        return __file__

    def __iter__(self):
        if False:
            yield self


class MockModule(types.ModuleType):
    def __getattr__(self, name, *args, **kwargs):
        if name == "__file__":
            return __file__
        if name == "__version__":
            return "1.0.0"
        return Mock()

    def __call__(self, *args, **kwargs):
        return Mock()


class MockModuleImporter(object):
    def find_module(self, fullname, path=None):
        return self

    def load_module(self, name):
        mock = MockModule(name)
        sys.modules[name] = mock
        return mock


@contextlib.contextmanager
def disable_importerror():
    meta_path = sys.meta_path
    sys.meta_path = sys.meta_path[:] + [MockModuleImporter()]
    yield
    sys.meta_path = meta_path


def __convert_args_to_json(args):
    def default_json(val):
        if isinstance(val, bytes):
            return val.decode("utf8", "ignore")

        # handle things like sets, filter objects etc
        if isinstance(val, collections.Iterable):
            return list(val)

        # rather than die here, convert to a string representation
        return str(val)

    # we know these will probably never work, so don't bother including
    args.pop('cmdclass', None)
    args.pop('ext_modules', None)
    args.pop('distclass', None)

    return json.dumps(args, skipkeys=True, default=default_json, indent=2)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser("Parse setup.py files")
    parser.add_argument('--trusted', dest='trusted', action='store_true')
    parser.add_argument('--mockimports', dest='mockimports', action='store_true')
    parser.add_argument('--printdelimiter', dest='printdelimiter', action='store_true')
    parser.add_argument('filename')
    args = parser.parse_args()

    results = parse_setup(args.filename, mock_imports=args.mockimports, trusted=args.trusted)

    if args.printdelimiter:
        print(_STDOUT_TERMINATOR)

    print(__convert_args_to_json(results))
