parsesetup: parses information from untrusted setup.py files
============================================================

This package returns information from a python packages `setup.py <https://docs.python.org/3/distutils/setupscript.html>`_ file,
without installing the package or trusting the code inside the
setup.py.

What's this for?
----------------

This is really an experiment in large scale processing of setup.py files.
The information provided in these files lists the project description, author and
other metadata like package dependencies.

Unfortunately there isn't any easy way of programmatically accessing this information
for an arbitrary setup.py script. Each setup.py script is a full python program,
and the data can be arbitrary python objects passed as arguments to a function.
This package aims to provide a way for python programs to retrieve all this metadata
programmatically, without doing things like installing the program or trusting
the setup.py file to not have malicious side effects.

I've verified it by running on every Python repository on GitHub that has more
than 500 stars (about 2500 repos). Every package that can be
installed on a clean python installation can be parsed by this code, and additionally a bunch of
packages that can't be installed for different reasons can also have their setup contents
retrieved here.

How does this work?
-------------------

Since setup.py files are Python files, the arguments to the setup call can be arbitrary
Python expressions. This means that the only reliable way of getting these arguments is by
evaluating the setup.py file using a Python interpreter.

To return the arguments passed to setuptools.setup call from a setup.py file, I'm temporarily
monkey patching the setuptools.setup function to collect its arguments - and then
using exec to execute the setup.py file:

.. code-block:: python

    setup_args = [None]

    # patch setup functions to just keep track of arguments passed to them
    def patched_setup(**kwargs):
        setup_args[0] = kwargs

    setuptools.setup = distutils.core = patched_setup

    exec(open(setup_py_filename).read(), {
         "__name__": "__main__",
         "__builtins__": __builtins__,
         "__file__": setup_py_filename})

Globals are explicitly set up to match what most scripts expect, including ``__name__ == "__main__"``
guards. Likewise there are some special cases with setting up the python path, current directory
etc that are taken care of by the full code.

Sandboxing with Docker
----------------------

Running exec on an untrusted python file is a bad idea. As an example, some `setup.py scripts
<https://github.com/EasyEngine/easyengine/blob/21f56da90214d671fdbf3aad0f11d837631c2339/setup.py#L58>`_ do interesting things like mess around
with your root git config - but the potential harm could be much much worse than that.

To prevent harmful side-effects, this package runs by default in a sandboxed Docker container. In addition to security benefits,
this also lets us to cleanly fall back to using a Python2.7 Docker image in the case where the syntax is invalid for Python3.

Running in a docker container can be disabled by setting a ``trusted=True`` flag when calling. Also note that its probably worth
configuring Docker to use `gVisor <https://github.com/google/gvisor>`_  to provide some extra piece of mind when parsing untrusted code.

Handling Missing Dependencies
-----------------------------

A common pattern for setup.py files is to import the uninstalled module to look up things like version strings.
While this works, it can have the side effect of importing the modules dependencies before they are installed.

As an example, `tensorlayer <https://github.com/tensorlayer/tensorlayer/blob/7f692946619470967549d514c0295de3cbb0d92c/setup.py#L18>`_
imports some metadata from its root module - which in turn imports tensorflow, which hasn't been installed yet in the docker
image.

To hack around this problem, this code has the option of hooking into Python's import handling to prevent ImportError's
from surfacing when running.

The idea is to provide a module importer to ``sys.meta_path`` that always finds a module if the existing
resolution fails:

.. code-block:: python

    class MockModuleImporter(object):
        def find_module(self, fullname, path=None):
            return self

        def load_module(self, name):
            mock = MockModule(name)
            sys.modules[name] = mock
            return mock

    # This hooks into Pythons' import mechanism, meaning that any
    # module that fails to import will be replaced with a MockModule
    # object
    sys.meta_path.append([MockModuleImporter()])


The ``MockModule`` inherits from ``types.Modules`` and just returns a Mock object object
with the common magic methods defined

.. code-block:: python

    class MockModule(types.ModuleType):
        def __getattr__(self, name, *args, **kwargs):
            return Mock()

        def __call__(self, *args, **kwargs):
            return Mock()


    class Mock(object):
        def __getattr__(self, *args, **kwargs):
            return self
        __call__ = __getitem__ = __setitem__ = __add__ = __getattr__

        ... etc ...


This prevents a sizeable number of errors, and doesn't seem to affect the output noticeably.
This behaviour can be disabled by setting ``mock_imports=False``.

Usage
-----

This code can be installed via pip:

.. code-block:: shell

    pip install parsesetup

To run

.. code-block:: python

    import parsesetup

    # parses the setup.py file, returning arguments as a dict
    setup_args = parsesetup.parse_setup(path_to_setup_py)

    # Parses a single package without using docker (dangerous!)
    setup_args = parsesetup.parse_setup(path_to_setup_py, trusted=True)

    # Parses multiple packages in a single docker container. All packages
    # need to share a common directory root for this to work
    with parsesetup.DockerSetupParser(ROOT_PATH) as parser:
        setup_a = parser.parse(path_to_setup_py_a)
        setup_b = parser.parse(path_to_setup_py_a)

Features
--------

  - Programmatically lets you inspect information contained in setup.py files
  - Handles both python2.7 and 3.6 scripts
  - Hooks into setuptools, distutils.core and numpy.distutils.core setups
  - Runs untrusted setup.py files in a docker container
  - Reads files with a __name__ == "__main__" guard

Released under the MIT License
