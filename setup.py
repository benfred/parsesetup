from setuptools import setup

import parsesetup

with open("README.rst", "r") as fp:
    long_description = fp.read()

setup(name="parsesetup",
      version=parsesetup.__version__,
      author="Ben Frederickson",
      author_email="ben@benfrederickson.com",
      url='http://github.com/benfred/parsesetup/',
      py_modules=["parsesetup"],
      description="Parses setup.py files",
      long_description=long_description,
      license="MIT",
      classifiers=[
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Topic :: Software Development :: Libraries",
        "Topic :: Utilities"],
      python_requires=">=3.5",
      install_requires=['docker'])
