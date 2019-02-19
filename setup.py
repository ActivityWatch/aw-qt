#!/usr/bin/env python

import os
from setuptools import setup


def read_requirements():
    """Parse requirements from requirements.txt."""
    reqs_path = os.path.join('.', 'requirements.txt')
    with open(reqs_path, 'r') as f:
        requirements = [line.rstrip() for line in f]
    return requirements


setup(name='aw-qt',
      version='0.1',
      description='Trayicon for ActivityWatch, built with Qt',
      author='Erik Bjäreholt',
      author_email='erik@bjareho.lt',
      url='https://github.com/ActivityWatch/aw-trayicon',
      packages=['aw_qt'],
      install_requires=read_requirements(),
      entry_points={
          'console_scripts': ['aw-qt = aw_qt:main']
      },
      classifiers=[
          'Programing Language :: Python :: 3'
      ])
